"""Offline training driver for D6 (Combined Autoencoder) — Stream 4.1.

Today the live backend trains D6 *every cold boot* inside
`backend/app/analytics_cache.build_cache()` because no artifact was
ever persisted. That cost is paid by the first user (and again on
every Fly.io VM restart). This script lifts the training off the hot
path: run it offline, commit `ml/artifacts/d6_combined_ae.pt`, and
the cache loader picks it up at boot.

Usage:

    python -m ml.training.train_d6 --epochs 30 --save
    python -m ml.training.train_d6 --dry-run   # train but don't write to disk

The output of `--save` is a single .pt file at the same path that
`backend/app/analytics_cache.py` reads. Schema version pinned in
`D6Artifacts._SCHEMA_VERSION` so a stale artifact fails loudly at load
time instead of silently emitting null detector scores.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402

from backend.app.ingest.schemas import CompanyBundle  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.modules.m11_anomaly import financial_feature_row  # noqa: E402
from ml.detectors.d6_combined_ae import D6Artifacts, train_d6  # noqa: E402
from ml.l05_graph_features import extract_features  # noqa: E402

logger = logging.getLogger("train_d6")

ARTIFACT_PATH = ROOT / "ml" / "artifacts" / "d6_combined_ae.pt"


def _graph_feature_row(features) -> np.ndarray:
    """Mirror of analytics_cache._graph_feature_row — kept local so the
    training script doesn't import the runtime cache module (which would
    drag in torch_geometric + the whole analytics build at import time)."""
    return np.asarray(
        [
            features.pagerank,
            features.betweenness,
            features.clustering_coeff,
            float(features.director_count),
            features.counterparty_median_age_days / 1825.0,  # 5-year scale
            float(features.degree),
            features.ego_density,
        ],
        dtype=np.float32,
    )


def _build_pool_graph(bundles: list[CompanyBundle]) -> tuple[nx.DiGraph, dict[str, int], dict[str, date]]:
    g: nx.DiGraph = nx.DiGraph()
    director_counts: dict[str, int] = {}
    incorporation_dates: dict[str, date] = {}
    for b in bundles:
        cin = b.company.cin
        g.add_node(cin, kind="company")
        director_counts[cin] = len(b.directors)
        incorporation_dates[cin] = b.company.incorporation_date
        for d in b.directors:
            g.add_node(d.din, kind="director")
            g.add_edge(cin, d.din)
    return g, director_counts, incorporation_dates


async def _load_all_fixture_bundles() -> list[CompanyBundle]:
    src = FixtureSource()
    bundles: list[CompanyBundle] = []
    for cin in await src.list_available_cins():
        bundle = await src.fetch_bundle(cin)
        if bundle is not None:
            bundles.append(bundle)
    return bundles


def _build_matrices(bundles: list[CompanyBundle]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Construct (tabular, graph, cin_order) matrices aligned row-by-row.
    Only CINs that carry both at least one FinancialStatement *and* a
    graph-features row are kept — D6 needs paired observations."""
    g, dcounts, dates = _build_pool_graph(bundles)
    feat_map = extract_features(
        g, director_counts=dcounts, incorporation_dates=dates,
    )
    rows_tab: list[np.ndarray] = []
    rows_graph: list[np.ndarray] = []
    cin_order: list[str] = []
    for b in bundles:
        cin = b.company.cin
        gf = feat_map.get(cin)
        if gf is None:
            continue
        fs_sorted = sorted(b.financials, key=lambda f: f.year)
        if not fs_sorted:
            continue
        rows_tab.append(financial_feature_row(fs_sorted[-1]).astype(np.float32))
        rows_graph.append(_graph_feature_row(gf))
        cin_order.append(cin)
    if not rows_tab:
        return np.zeros((0, 20), dtype=np.float32), np.zeros((0, 7), dtype=np.float32), []
    return np.vstack(rows_tab), np.vstack(rows_graph), cin_order


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train + persist D6 Combined-AE")
    p.add_argument("--epochs", type=int, default=30, help="training epochs (default 30)")
    p.add_argument("--seed", type=int, default=42, help="torch + numpy seed")
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--save", action="store_true", help="write artifact to ml/artifacts/d6_combined_ae.pt")
    p.add_argument("--dry-run", action="store_true", help="train but skip the save")
    p.add_argument("--output", type=str, default=None,
                   help="override artifact path (default ml/artifacts/d6_combined_ae.pt)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    args = _parse_args()

    bundles = asyncio.run(_load_all_fixture_bundles())
    if len(bundles) < 2:
        logger.error("train_d6: need >=2 fixture bundles; got %d", len(bundles))
        return 2

    tab, graph, cin_order = _build_matrices(bundles)
    if tab.shape[0] < 2:
        logger.error(
            "train_d6: only %d bundles carry both FS and graph features; "
            "need >=2 paired rows", tab.shape[0],
        )
        return 2

    logger.info(
        "train_d6: training on %d rows (tab=%s, graph=%s) — epochs=%d seed=%d",
        tab.shape[0], tab.shape, graph.shape, args.epochs, args.seed,
    )
    artifacts = train_d6(
        tab, graph, epochs=args.epochs, lr=args.lr,
        batch_size=args.batch_size, seed=args.seed,
    )
    sample_scores = artifacts.anomaly_scores(tab, graph)
    logger.info(
        "train_d6: error_p99=%.6f  score_min=%.4f  score_median=%.4f  score_max=%.4f",
        artifacts.error_p99,
        float(sample_scores.min()),
        float(np.median(sample_scores)),
        float(sample_scores.max()),
    )

    if args.dry_run or not args.save:
        logger.info("train_d6: --save not set, skipping persistence (dry run)")
        return 0

    out_path = Path(args.output) if args.output else ARTIFACT_PATH
    artifacts.save(str(out_path))
    logger.info("train_d6: artifact written to %s (%d rows, schema v%d)",
                out_path, tab.shape[0], D6Artifacts._SCHEMA_VERSION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

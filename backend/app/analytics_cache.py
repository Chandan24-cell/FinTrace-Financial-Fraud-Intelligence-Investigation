"""Startup-precomputed analytics cache — backing store for M10 + M11.

Both modules need a *population view* of the company pool to fire usefully:

* M10 (hypergraph shell) groups companies by shared attribute (address /
  auditor DIN / phone / IFSC). One CIN's contribution depends on whether
  other CINs share its attribute. Per-request scoring with just one CIN
  would never fire. We run the batch once at startup and cache the
  per-CIN ModuleResult dict.
* M11 (anomaly) trains IsolationForest + LOF against a background sample.
  Per-request scoring without a background returns abstained. We pre-build
  the 20-D financial matrix and the 7-D L0.5 graph-feature matrix from
  the fixture pool once.

Both caches are loaded lazily on first /analyse request via
`get_or_build()`. Subsequent requests hit the in-memory dict. Tests can
call `reset_for_tests()` to clear between runs.

LOCAL_TEST_REPORT §3.2: this closes the M10/M11 deferral.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np
import torch

from backend.app.ingest.schemas import CompanyBundle
from backend.app.modules.m10_hypergraph_shell import HypergraphInputs
from backend.app.modules.m10_hypergraph_shell import run as m10_run
from backend.app.modules.m11_anomaly import (
    financial_feature_row,
    graph_feature_row,
)
from ml.detectors.d3_tgn import D3TGN, TGNConfig
from ml.detectors.d4_lof import D4LOF
from ml.detectors.d5_mamba import (
    FEATURE_DIM as D5_FEATURE_DIM,
    TemporalConvolutionalNet,
    build_sequence_for_cin,
    build_sequences_from_company_bundles,
)
from ml.detectors.d6_combined_ae import D6Artifacts, train_d6
from ml.l05_graph_features import GraphFeatures, extract_features

if TYPE_CHECKING:  # pragma: no cover
    from backend.app.modules.base import ModuleResult

logger = logging.getLogger(__name__)

D5_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "ml" / "artifacts" / "d5_tcn.pt"
D6_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "ml" / "artifacts" / "d6_combined_ae.pt"


@dataclass
class AnalyticsCache:
    """All population-view artefacts the per-request scorer needs."""

    # M11 cache
    graph_feature_by_cin: dict[str, GraphFeatures] = field(default_factory=dict)
    financial_row_by_cin: dict[str, np.ndarray] = field(default_factory=dict)
    background_graph: np.ndarray = field(default_factory=lambda: np.zeros((0, 7)))
    background_financials: np.ndarray = field(default_factory=lambda: np.zeros((0, 20)))

    # M10 cache — full batch result, indexed by CIN
    m10_results: dict[str, "ModuleResult"] = field(default_factory=dict)

    # Detector outputs cached at build time — all four real detectors wired.
    # D1 (CatBoost) + D2 (β-VAE) were deleted upstream as unimplemented stubs.
    d3_tgn: D3TGN | None = None
    d3_node_id_by_cin: dict[str, int] = field(default_factory=dict)
    d3_scores_by_cin: dict[str, float] = field(default_factory=dict)
    d4_scores_by_cin: dict[str, float] = field(default_factory=dict)
    d5_model: TemporalConvolutionalNet | None = None
    d5_scores_by_cin: dict[str, float] = field(default_factory=dict)
    d6_artifacts: D6Artifacts | None = None
    d6_scores_by_cin: dict[str, float] = field(default_factory=dict)


_cache: AnalyticsCache | None = None
_build_lock = asyncio.Lock()


def get_status() -> dict:
    """Snapshot of cache build state for /health/ml. Never raises."""
    if _cache is None:
        return {"built": False}
    return {
        "built": True,
        "pool_size": len(_cache.financial_row_by_cin),
        "detectors": {
            "d3": {
                "ready": _cache.d3_tgn is not None,
                "scored_cins": len(_cache.d3_scores_by_cin),
            },
            "d4": {
                "ready": True,
                "scored_cins": len(_cache.d4_scores_by_cin),
            },
            "d5": {
                "ready": _cache.d5_model is not None,
                "scored_cins": len(_cache.d5_scores_by_cin),
            },
            "d6": {
                "ready": _cache.d6_artifacts is not None,
                "scored_cins": len(_cache.d6_scores_by_cin),
            },
        },
        "m10_precomputed": len(_cache.m10_results),
    }


def _build_global_graph(
    bundles: list[CompanyBundle],
) -> tuple[nx.DiGraph, dict[str, int], dict[str, date]]:
    """Construct the Company×Director bipartite graph used for L0.5 features.

    Edges: (company_cin -> director_din) for every IS_DIRECTOR_OF relationship
    in the fixture pool. Directors that direct multiple companies become hubs;
    L0.5 PageRank + betweenness pick them up, which is the actual signal we
    want M11 (LOF) to learn against.
    """
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


def build_cache(
    bundles: list[CompanyBundle], *, as_of: date | None = None,
) -> AnalyticsCache:
    """Synchronous builder — exposed for scripts/training that want offline
    access. The async `get_or_build()` wraps this via to_thread."""
    cache = AnalyticsCache()
    if not bundles:
        return cache

    # ---- M11: graph features over the fixture-pool bipartite graph --------
    g, director_counts, incorporation_dates = _build_global_graph(bundles)
    feat_map = extract_features(
        g,
        director_counts=director_counts,
        incorporation_dates=incorporation_dates,
        as_of=as_of,
    )

    graph_rows: list[np.ndarray] = []
    financial_rows: list[np.ndarray] = []

    for b in bundles:
        cin = b.company.cin
        gf = feat_map.get(cin)
        if gf is not None:
            cache.graph_feature_by_cin[cin] = gf
            graph_rows.append(graph_feature_row(gf))
        fs_sorted = sorted(b.financials, key=lambda f: f.year)
        if fs_sorted:
            row = financial_feature_row(fs_sorted[-1])
            cache.financial_row_by_cin[cin] = row
            financial_rows.append(row)

    cache.background_graph = (
        np.vstack(graph_rows) if graph_rows else np.zeros((0, 7))
    )
    cache.background_financials = (
        np.vstack(financial_rows) if financial_rows else np.zeros((0, 20))
    )

    # ---- M10: one-shot batch over all (companies + charges) ---------------
    companies = [b.company for b in bundles]
    charges = [c for b in bundles for c in b.charges]
    cache.m10_results = m10_run(HypergraphInputs(companies=companies, charges=charges))

    # ---- D4 (LOF) + D6 (Combined AE): batch-fit-and-score on backgrounds --
    # These match M11's LOF + IsoForest in spirit but expose normalised
    # scores in [0, 1] for direct concatenation onto the meta-learner
    # feature vector. Done here once at startup so per-request inference is
    # a dict lookup.
    rows_cin_order = list(cache.graph_feature_by_cin.keys())
    if rows_cin_order and cache.background_graph.shape[0] >= 2:
        d4 = D4LOF(n_neighbors=20)
        d4_scores = d4.fit_score(cache.background_graph)
        for cin, s in zip(rows_cin_order, d4_scores):
            cache.d4_scores_by_cin[cin] = float(s)

    # D6 needs row-aligned (tabular, graph) pairs. Some fixtures only have
    # one or the other — intersect the CINs that carry both, then build
    # aligned matrices in CIN order.
    both = [
        c for c in cache.financial_row_by_cin
        if c in cache.graph_feature_by_cin
    ]
    if len(both) >= 2:
        d6_tab = np.vstack([cache.financial_row_by_cin[c] for c in both]).astype(np.float32)
        d6_graph = np.vstack(
            [graph_feature_row(cache.graph_feature_by_cin[c]) for c in both]
        ).astype(np.float32)
        # Stream 4.1 — prefer the on-disk artifact when present so we
        # stop paying the ~3s autoencoder training cost on every cold
        # boot. Fall back to inline training when the file is missing
        # (fresh checkout) or its schema-version doesn't match.
        cache.d6_artifacts = None
        if D6_ARTIFACT_PATH.exists():
            try:
                cache.d6_artifacts = D6Artifacts.load(str(D6_ARTIFACT_PATH))
                logger.info(
                    "analytics_cache: D6 loaded from %s (skipped inline training)",
                    D6_ARTIFACT_PATH,
                )
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.warning(
                    "analytics_cache: D6 load failed (%s) — training inline as fallback",
                    exc,
                )
        if cache.d6_artifacts is None:
            cache.d6_artifacts = train_d6(d6_tab, d6_graph, epochs=20, seed=42)
            logger.info(
                "analytics_cache: D6 trained inline on %d rows — persist via "
                "`python -m ml.training.train_d6 --save` to skip on future boots",
                d6_tab.shape[0],
            )
        d6_scores = cache.d6_artifacts.anomaly_scores(d6_tab, d6_graph)
        for cin, s in zip(both, d6_scores):
            cache.d6_scores_by_cin[cin] = float(s)

    # ---- D5 (TCN) inference: load persisted model + score every bundle ----
    # Training happens in scripts/day13_oof_retrain.py (which has the labels);
    # at inference we just load the state_dict and predict. When the artefact
    # is missing — fresh checkout / artefact gitignored — D5 scores stay 0
    # for every CIN and the meta-learner's d5 slot is effectively unused.
    if D5_ARTIFACT_PATH.exists():
        try:
            cache.d5_model = TemporalConvolutionalNet()
            cache.d5_model.load_state_dict(
                torch.load(str(D5_ARTIFACT_PATH), map_location="cpu", weights_only=True)
            )
            cache.d5_model.eval()
            batch = build_sequences_from_company_bundles(bundles)
            if batch.cins:
                with torch.no_grad():
                    preds = cache.d5_model(
                        torch.from_numpy(batch.x),
                        torch.from_numpy(batch.mask),
                    ).cpu().numpy()
                for cin, s in zip(batch.cins, preds):
                    cache.d5_scores_by_cin[cin] = float(s)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("analytics_cache: D5 load/predict failed (%s) — d5 slot stays zero", exc)
            cache.d5_model = None

    # ---- D3 (TGN): train fresh on the bundle pool's event stream + embed --
    # No persisted artefact — the Day-7 d3_tgn.pt was a 50-synthetic-node
    # smoke check whose node-id space doesn't map to real CINs. Training
    # fresh here is fast (~2s on the fixture pool), gives us a real
    # per-CIN embedding, and stays consistent with the bundles M10/M11
    # also see.
    _build_d3(cache, bundles)

    logger.info(
        "analytics_cache: built from %d bundles — graph_bg=%d financial_bg=%d "
        "m10_hits=%d d3=%d d4=%d d5=%d d6=%d",
        len(bundles),
        cache.background_graph.shape[0],
        cache.background_financials.shape[0],
        len(cache.m10_results),
        len(cache.d3_scores_by_cin),
        len(cache.d4_scores_by_cin),
        len(cache.d5_scores_by_cin),
        len(cache.d6_scores_by_cin),
    )
    return cache


def _build_d3(cache: AnalyticsCache, bundles: list[CompanyBundle]) -> None:
    """Construct (src, dst, t, msg), train TGN, embed every CIN node, write
    min-max-normalised L2 embedding norms into cache.d3_scores_by_cin.

    Node-ID layout (deterministic):
       0 .. N_cin-1            company nodes (sorted CIN order)
       N_cin .. N_cin+N_len-1  lender pseudo-nodes (one per unique charge holder)
       last block              fiscal-year pseudo-nodes (one per (cin,year) FS row)

    Events:
       (cin_node, lender_node, charge_creation_epoch, tanh(amount/scale))
       (cin_node, year_node,   fs_year_end_epoch,     tanh(revenue/scale))
    """
    if not bundles:
        return

    cin_to_id: dict[str, int] = {b.company.cin: i for i, b in enumerate(sorted(bundles, key=lambda b: b.company.cin))}
    cache.d3_node_id_by_cin = cin_to_id
    n_cins = len(cin_to_id)

    lender_to_id: dict[str, int] = {}
    next_node = n_cins
    src_list: list[int] = []
    dst_list: list[int] = []
    t_list: list[int] = []
    msg_list: list[float] = []

    # Amount-normalisation scale: median of all observed amounts (avoids any
    # single mega-charge dominating the [-1, 1] msg encoding).
    all_amounts: list[float] = []
    for b in bundles:
        for c in b.charges:
            all_amounts.append(abs(c.amount))
        for fs in b.financials:
            all_amounts.append(abs(fs.revenue))
    if not all_amounts:
        return
    scale = float(np.median([a for a in all_amounts if a > 0]) or 1.0)

    for b in bundles:
        cid = cin_to_id[b.company.cin]
        for charge in b.charges:
            lid = lender_to_id.get(charge.lender_name)
            if lid is None:
                lid = next_node
                lender_to_id[charge.lender_name] = lid
                next_node += 1
            src_list.append(cid)
            dst_list.append(lid)
            t_list.append(int(__import__("datetime").datetime.combine(
                charge.creation_date, __import__("datetime").time()).timestamp()))
            msg_list.append(float(np.tanh(charge.amount / scale)))
        for fs in b.financials:
            yid = next_node
            next_node += 1
            src_list.append(cid)
            dst_list.append(yid)
            ye = __import__("datetime").date(fs.year, 3, 31)
            t_list.append(int(__import__("datetime").datetime.combine(
                ye, __import__("datetime").time()).timestamp()))
            msg_list.append(float(np.tanh(fs.revenue / scale)))

    if len(src_list) < 4:
        logger.info("analytics_cache: D3 skipped — only %d events, need >=4", len(src_list))
        return

    num_nodes = next_node
    try:
        cfg = TGNConfig(num_nodes=num_nodes)
        tgn = D3TGN(cfg, device="cpu")
        tgn.fit(
            np.asarray(src_list, dtype=np.int64),
            np.asarray(dst_list, dtype=np.int64),
            np.asarray(t_list, dtype=np.int64),
            np.asarray(msg_list, dtype=np.float32),
            epochs=2,
            batch_size=32,
        )
        cin_ids = np.asarray(list(cin_to_id.values()), dtype=np.int64)
        emb = tgn.embed(cin_ids)
        norms = np.linalg.norm(emb, axis=1)
        if float(norms.max() - norms.min()) < 1e-9:
            scores = np.zeros_like(norms)
        else:
            scores = (norms - norms.min()) / (norms.max() - norms.min())
        for cin, s in zip(cin_to_id.keys(), scores):
            cache.d3_scores_by_cin[cin] = float(s)
        cache.d3_tgn = tgn
    except Exception as exc:  # pragma: no cover — defensive (e.g. torch_geometric kernels)
        logger.warning("analytics_cache: D3 train/embed failed (%s) — d3 slot stays zero", exc)
        cache.d3_tgn = None
        cache.d3_scores_by_cin = {}


def compute_target_detector_scores(
    cin: str, bundle: CompanyBundle, cache: AnalyticsCache,
) -> tuple[float, float, float, float]:
    """Return (d3_score, d4_score, d5_score, d6_score) for a CIN.

    Fast-path: dict lookup when the CIN was in the cache build set. Slow-path
    (uploads / new CINs): compute fresh from the loaded model where cheap.
    Zeros when the cache or target rows are missing — never raises.
    """
    pre_d3 = cache.d3_scores_by_cin.get(cin)
    pre_d4 = cache.d4_scores_by_cin.get(cin)
    pre_d5 = cache.d5_scores_by_cin.get(cin)
    pre_d6 = cache.d6_scores_by_cin.get(cin)
    if pre_d3 is not None and pre_d4 is not None and pre_d5 is not None and pre_d6 is not None:
        return pre_d3, pre_d4, pre_d5, pre_d6

    # D3 has no cheap slow-path — embedding an unseen CIN requires the
    # inductive fallback inside D3TGN.embed (mean of seen-node embeddings),
    # which is not useful on its own. Zero is the honest fallback for CINs
    # outside the cache build set.
    d3_out: float = pre_d3 if pre_d3 is not None else 0.0
    d4_out: float = pre_d4 if pre_d4 is not None else 0.0
    d5_out: float = pre_d5 if pre_d5 is not None else 0.0
    d6_out: float = pre_d6 if pre_d6 is not None else 0.0

    # D6 slow-path — D4 needs a full LOF refit (prohibitive); we skip it.
    if pre_d6 is None and cache.d6_artifacts is not None and bundle.financials:
        gf = cache.graph_feature_by_cin.get(cin)
        if gf is not None:
            fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
            tab = financial_feature_row(fs_sorted[-1]).astype(np.float32).reshape(1, -1)
            graph = graph_feature_row(gf).astype(np.float32).reshape(1, -1)
            try:
                d6_out = float(cache.d6_artifacts.anomaly_scores(tab, graph)[0])
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("analytics_cache: D6 slow-path failed for %s (%s)", cin, exc)

    # D5 slow-path — TCN is cheap to evaluate; build target sequence + predict.
    if pre_d5 is None and cache.d5_model is not None:
        rows: list[dict] = []
        as_of = date.today()
        for charge in bundle.charges:
            rows.append({
                "amount": charge.amount,
                "counterparty_type": "bank",
                "gst_status": "active",
                "delta_days": (as_of - charge.creation_date).days,
            })
        for fs in sorted(bundle.financials, key=lambda f: f.year):
            ye = date(fs.year, 3, 31)
            rows.append({
                "amount": fs.revenue,
                "counterparty_type": "customer",
                "gst_status": "active" if not fs.adverse_flag else "missing_trader",
                "delta_days": (as_of - ye).days,
            })
        if rows:
            rows.sort(key=lambda r: -int(r["delta_days"]))
            try:
                x, mask = build_sequence_for_cin(rows)
                xt = torch.from_numpy(x.reshape(1, *x.shape))
                mt = torch.from_numpy(mask.reshape(1, *mask.shape))
                with torch.no_grad():
                    d5_out = float(cache.d5_model(xt, mt).cpu().numpy()[0])
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("analytics_cache: D5 slow-path failed for %s (%s)", cin, exc)
    return d3_out, d4_out, d5_out, d6_out


async def get_or_build(bundles_provider) -> AnalyticsCache:
    """Return the cached analytics, building it lazily on first call.

    bundles_provider is an async callable that returns list[CompanyBundle] —
    typically `lambda: _fixture_source.fetch_all_bundles()` from the analyse
    handler. Passing a thunk (not the list itself) avoids loading bundles on
    every request after the cache is warm.
    """
    global _cache
    if _cache is not None:
        return _cache
    async with _build_lock:
        if _cache is not None:
            return _cache
        bundles = await bundles_provider()
        # CPU-bound: extract_features + IsolationForest fit later use it.
        _cache = await asyncio.to_thread(build_cache, bundles)
        return _cache


def reset_for_tests() -> None:
    """Clear the module-level cache so tests can rebuild between runs."""
    global _cache
    _cache = None

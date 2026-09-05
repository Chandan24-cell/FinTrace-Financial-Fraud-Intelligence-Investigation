"""Day-5 acceptance script (PRD §10 Day 5).

Done-when criteria:
  - NCLT nodes written
  - LOF scores without error
  - Static formula replaced by LightGBM
  - Calibration live

Steps:
  1. Load NCLT proceedings + wilful defaulters + CERSAI charges from fixtures
     (and write to Neo4j unless --dry-run).
  2. Build the 7-feature L0.5 matrix for the 10 Day-3 companies (synthetic graph).
  3. Run D4 LOF on the matrix — verifies LOF returns finite scores.
  4. Generate a small synthetic training set (50 samples, ~30% positive) and
     train the OOF LightGBM stack F1a -> F1b isotonic -> F1c conformal.
  5. Save all artifacts to ml/artifacts/.

Day-12 retrains F1a/b/c with the real SFIO labels — Day 5 just proves the
pipeline runs end-to-end and the static formula is gone (PRD §13 grep check).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

import networkx as nx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingest.cersai import CERSAIFixtureSource  # noqa: E402
from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402
from ml.detectors.d4_lof import D4LOF  # noqa: E402
from ml.l05_graph_features import extract_features  # noqa: E402
from ml.meta.f1a_lightgbm_oof import fit_oof  # noqa: E402
from ml.meta.f1b_isotonic import fit_isotonic  # noqa: E402
from ml.meta.f1c_split_conformal import fit_conformal, predict_with_interval  # noqa: E402

ARTIFACT_DIR = ROOT / "ml" / "artifacts"
logger = logging.getLogger("day5")


def _build_company_graph(cins: list[str], incorporation_dates: dict[str, date]) -> nx.DiGraph:
    """Construct a synthetic transaction graph between the 10 fixture companies.

    Real edges come from Day-7 pre-cache + TRANSACTS_WITH ingestion. For Day 5
    we only need a non-trivial graph so the L0.5 features compute non-zero
    PageRank/betweenness/etc. for the LOF run.
    """
    g: nx.DiGraph = nx.DiGraph()
    # Hub spoke: company[0] (IL&FS) transacts with everyone else; alternating
    # pairs form a small cycle. Enough structure for PageRank to vary.
    for i, cin in enumerate(cins):
        g.add_node(cin)
        if i > 0:
            g.add_edge(cins[0], cin, weight=1.0)
            g.add_edge(cin, cins[0], weight=0.5)
        if i % 2 == 0 and i + 2 < len(cins):
            g.add_edge(cins[i], cins[i + 2], weight=0.7)
    return g


def _synthetic_training_set(rng: np.random.Generator, n: int = 60, d: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Generate a small balanced synthetic set so the OOF pipeline can run.

    Day-12 replaces this with the real SFIO 14-case labels + healthy peers.
    """
    n_pos = max(5, n // 3)
    n_neg = n - n_pos
    X_neg = rng.normal(loc=0.0, scale=1.0, size=(n_neg, d))
    X_pos = rng.normal(loc=1.5, scale=1.0, size=(n_pos, d))
    X = np.vstack([X_neg, X_pos]).astype(np.float32)
    y = np.array([0] * n_neg + [1] * n_pos, dtype=np.int32)
    perm = rng.permutation(len(y))
    return X[perm], y[perm]


async def _run(dry_run: bool) -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    fixture_source = FixtureSource()
    cins = (await fixture_source.list_available_cins())[:10]
    if len(cins) < 10:
        logger.error("Need 10 fixtures; found %d", len(cins))
        return 2

    nclt_records = await NCLTFixtureSource().fetch_all()
    wilful_records = await WilfulDefaulterFixtureSource().fetch_all()
    cersai_records = await CERSAIFixtureSource().fetch_all()
    logger.info(
        "Loaded ingest fixtures: NCLT=%d wilful=%d cersai=%d",
        len(nclt_records), len(wilful_records), len(cersai_records),
    )

    # Build L0.5 graph features
    incorporation_dates: dict[str, date] = {}
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if bundle:
            incorporation_dates[cin] = bundle.company.incorporation_date
    g = _build_company_graph(cins, incorporation_dates)
    features = extract_features(g, incorporation_dates=incorporation_dates, as_of=date(2025, 1, 1))

    X = np.array([
        [
            features[cin].pagerank,
            features[cin].betweenness,
            features[cin].clustering_coeff,
            features[cin].director_count,
            features[cin].counterparty_median_age_days,
            features[cin].degree,
            features[cin].ego_density,
        ]
        for cin in cins
    ], dtype=np.float32)
    logger.info("Built L0.5 feature matrix: shape=%s", X.shape)

    # D4 LOF on the L0.5 matrix
    lof = D4LOF(n_neighbors=5, feature_names=(
        "pagerank", "betweenness", "clustering_coeff", "director_count",
        "counterparty_median_age_days", "degree", "ego_density",
    ))
    lof_scores = lof.fit_score(X)
    logger.info("D4 LOF — scores range [%.3f, %.3f]", lof_scores.min(), lof_scores.max())
    lof.save(str(ARTIFACT_DIR / "d4_lof.joblib"))

    # F1a OOF + F1b isotonic + F1c conformal on a synthetic 60-sample set
    rng = np.random.default_rng(seed=42)
    X_train, y_train = _synthetic_training_set(rng, n=60, d=X.shape[1])

    # 15% hold-out from the synthetic set
    n_hold = max(2, int(0.15 * len(y_train)))
    X_oof, y_oof = X_train[:-n_hold], y_train[:-n_hold]
    X_hold, y_hold = X_train[-n_hold:], y_train[-n_hold:]

    f1a = fit_oof(X_oof, y_oof, n_splits=5, feature_names=lof.feature_names)
    f1a.save(str(ARTIFACT_DIR / "f1a_oof.joblib"))
    logger.info("F1a OOF — pred range [%.3f, %.3f]", f1a.oof_pred.min(), f1a.oof_pred.max())

    raw_holdout = f1a.final_booster.predict(X_hold)
    f1b = fit_isotonic(np.asarray(raw_holdout), y_hold)
    f1b.save(str(ARTIFACT_DIR / "f1b_isotonic.joblib"))
    logger.info("F1b isotonic — n_holdout=%d ECE=%.4f", f1b.n_holdout, f1b.ece)

    conformal = fit_conformal(f1a, f1b, X_hold, y_hold, alpha=0.10)
    conformal.save(str(ARTIFACT_DIR / "f1c_conformal.joblib"))
    logger.info(
        "F1c conformal — alpha=%.2f empirical coverage=%.2f",
        conformal.alpha, conformal.coverage,
    )

    p, low, high = predict_with_interval(f1a, f1b, conformal, X)
    print()
    print(f"{'CIN':<24} {'P(fraud)':<10} {'low':<8} {'high':<8} {'LOF':<8}")
    print("-" * 64)
    for cin, pf, pl, ph, ls in zip(cins, p, low, high, lof_scores):
        print(f"{cin:<24} {pf:<10.3f} {pl:<8.3f} {ph:<8.3f} {ls:<8.3f}")
    print("-" * 64)

    if dry_run:
        logger.warning("--dry-run set; skipping Neo4j writes")
        return 0

    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.writes import (
        upsert_charge,
        upsert_nclt_proceeding,
        upsert_wilful_defaulter,
    )

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:
        logger.error(
            "Cannot reach Neo4j at %s — start it with "
            "`docker compose -f infra/docker-compose.dev.yml up -d`. Error: %s",
            settings.neo4j_uri, exc,
        )
        await driver.close()
        return 3

    try:
        for n in nclt_records:
            await upsert_nclt_proceeding(driver, n)
        for w in wilful_records:
            await upsert_wilful_defaulter(driver, w)
        for c in cersai_records:
            await upsert_charge(driver, c)
    finally:
        await driver.close()

    print(f"\nDay-5 acceptance summary:")
    print(f"  NCLT proceedings written:        {len(nclt_records)}")
    print(f"  Wilful defaulter records:        {len(wilful_records)}")
    print(f"  CERSAI charges (LoanDisbursement): {len(cersai_records)}")
    print(f"  LOF scores produced for:         {len(cins)} companies")
    print(f"  F1a OOF range:                   [{f1a.oof_pred.min():.3f}, {f1a.oof_pred.max():.3f}]")
    print(f"  F1b ECE on hold-out:             {f1b.ece:.4f}")
    print(f"  F1c conformal coverage:          {conformal.coverage:.2f}")
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Skip Neo4j writes")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()

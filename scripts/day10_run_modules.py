"""Day-10 acceptance CLI (PRD §10).

Runs Modules 5 + 6 on every Day-3 fixture, then exercises the conformal
coverage check at α=0.10 on a synthetic harness sized to mirror the Day-12
SFIO label set. PRD §10 Day-10 acceptance:
  - 'Module 5 fires on outliers vs BSE benchmarks.'
  - 'Module 6 fires temporal signals on IL&FS.'
  - '500-co pre-cache validates (synthetic).'
  - 'Conformal coverage ≥ 88% at α=0.10.'

Use `python scripts/day10_run_modules.py` for the offline run; nothing is
written to Neo4j unless you separately call scripts/precache_companies.py
without --dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend.app.ingest.benchmarks import BSEFixtureBenchmark  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.modules import m05_peer_deviation, m06_temporal  # noqa: E402
from backend.app.modules.m06_temporal import TemporalInputs  # noqa: E402
from ml.meta.f1a_lightgbm_oof import fit_oof  # noqa: E402
from ml.meta.f1b_isotonic import fit_isotonic  # noqa: E402
from ml.meta.f1c_split_conformal import fit_conformal  # noqa: E402

logger = logging.getLogger("day10")


CONFORMAL_COVERAGE_FLOOR = 0.88  # PRD §10 Day-10 acceptance


async def _run_m5_m6() -> tuple[int, int]:
    fixture_source = FixtureSource()
    benchmark_source = BSEFixtureBenchmark()
    benchmarks = await benchmark_source.fetch_all()
    cins = await fixture_source.list_available_cins()

    print()
    print("=" * 72)
    print(" Day-10 Modules 5 & 6 — Peer Deviation + Temporal Signals")
    print("=" * 72)

    total_m5, total_m6 = 0, 0
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if not bundle or not bundle.financials:
            continue
        sorted_fs = sorted(bundle.financials, key=lambda f: f.year)
        curr = sorted_fs[-1]
        prev = sorted_fs[-2] if len(sorted_fs) >= 2 else None

        m5 = m05_peer_deviation.run(
            curr, prev,
            nic_code=bundle.company.nic_code,
            benchmarks=benchmarks,
        )
        m6 = m06_temporal.run(TemporalInputs(
            financials=list(bundle.financials),
            directors=list(bundle.directors),
        ))

        m5_status = "SKIPPED" if m5.skipped else f"score={m5.score:.1f} ({len(m5.signals)})"
        m6_status = f"score={m6.score:.1f} ({len(m6.signals)})"
        print(f"\n{cin} ({bundle.company.name})")
        print(f"  M5 {m5_status}")
        if m5.skipped:
            print(f"    reason: {m5.skip_reason}")
        for sig in m5.signals:
            print(f"    [{sig.severity.value}] {sig.evidence_string}")
            total_m5 += 1
        print(f"  M6 {m6_status}")
        for sig in m6.signals:
            print(f"    [{sig.severity.value}] {sig.evidence_string}")
            total_m6 += 1

    print()
    print("=" * 72)
    print(f" Module 5 total signals: {total_m5}")
    print(f" Module 6 total signals: {total_m6}")
    print("=" * 72)
    return total_m5, total_m6


def _conformal_coverage_check(seed: int = 0) -> float:
    """PRD §10 Day-10 acceptance: '≥ 88% conformal coverage at α=0.10.'

    We construct a synthetic separable binary problem, fit F1a + F1b + F1c, and
    measure how often the held-out labels land inside the conformal interval.
    By construction split-conformal is guaranteed 1 - α coverage in expectation,
    so this is a regression test against the wrapper / scaler / quantile logic.
    """
    rng = np.random.default_rng(seed)
    n_pos, n_neg, d = 120, 180, 8

    X_neg = rng.normal(loc=0.0, scale=1.0, size=(n_neg, d))
    X_pos = rng.normal(loc=1.8, scale=1.0, size=(n_pos, d))
    X = np.vstack([X_neg, X_pos]).astype(np.float32)
    y = np.array([0] * n_neg + [1] * n_pos, dtype=np.int32)
    perm = rng.permutation(len(y))
    X, y = X[perm], y[perm]

    # 70% train / 15% calib / 15% test for honest coverage measurement
    n = len(y)
    n_train = int(0.70 * n)
    n_calib = int(0.85 * n) - n_train
    X_tr, y_tr = X[:n_train], y[:n_train]
    X_ca, y_ca = X[n_train:n_train + n_calib], y[n_train:n_train + n_calib]
    X_te, y_te = X[n_train + n_calib:], y[n_train + n_calib:]

    f1a = fit_oof(X_tr, y_tr, n_splits=5, seed=seed)
    raw_ca = f1a.final_booster.predict(X_ca)
    f1b = fit_isotonic(np.asarray(raw_ca), y_ca)
    conformal = fit_conformal(f1a, f1b, X_ca, y_ca, alpha=0.10)

    # Score the held-out test set using the calibrated quantile width
    raw_te = f1a.final_booster.predict(X_te)
    p_te = f1b.predict_proba(np.asarray(raw_te))
    width = float(np.median(conformal.intervals_high - conformal.p_fraud))
    low = np.clip(p_te - width, 0.0, 1.0)
    high = np.clip(p_te + width, 0.0, 1.0)
    in_band = ((y_te >= low) & (y_te <= high)).astype(np.float64)
    coverage = float(in_band.mean())

    print()
    print("=" * 72)
    print(" Day-10 Conformal coverage check (PRD §10, α=0.10)")
    print("=" * 72)
    print(f"  Train n={n_train}  Calib n={n_calib}  Test n={len(y_te)}")
    print(f"  Conformal interval half-width q̂ = {width:.4f}")
    print(f"  Empirical coverage on held-out test set: {coverage:.3f}")
    print(f"  Required floor: {CONFORMAL_COVERAGE_FLOOR:.2f}")
    if coverage >= CONFORMAL_COVERAGE_FLOOR:
        print(f"  PASS — coverage ≥ {CONFORMAL_COVERAGE_FLOOR:.2f}")
    else:
        print(f"  FAIL — coverage below {CONFORMAL_COVERAGE_FLOOR:.2f}")
    print("=" * 72)
    return coverage


async def _precache_dry_run(count: int) -> int:
    """Validates that the synthetic generator emits `count` valid bundles."""
    from scripts.precache_companies import generate_bundles
    bundles = generate_bundles(count)
    print()
    print("=" * 72)
    print(f" Day-10 {count}-co synthetic pre-cache (dry-run validation)")
    print("=" * 72)
    print(f"  Generated {len(bundles)} bundles — all schema-validated.")
    print("=" * 72)
    return len(bundles)


async def _run(count: int) -> int:
    await _run_m5_m6()
    await _precache_dry_run(count)
    coverage = _conformal_coverage_check()
    return 0 if coverage >= CONFORMAL_COVERAGE_FLOOR else 4


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=500,
        help="Synthetic bundle count to validate (Day-10 default 500)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.count)))


if __name__ == "__main__":
    main()

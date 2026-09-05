"""Day-20 acceptance CLI (PRD §10).

PRD §10 Day 20 verbatim:
  'Full pipeline: AUC >= 0.96, F1 >= 0.91, inference <= 2s/report,
   conformal 90% coverage verified. Missing-modality: F1 >= 0.85 with
   sequence absent. Conformal audit >= 90%.'

This CLI is the pass/fail harness. It rebuilds the feature matrix over
fixtures + SFIO labels + synthetic, refits F1a/b/c, scores every fixture
end-to-end with RiskScorer for latency, and asserts the four floors.

Exit 0 on PASS, non-zero on any floor missed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from sklearn.metrics import f1_score, roc_auc_score  # noqa: E402

from backend.app.ingest.benchmarks import BSEFixtureBenchmark  # noqa: E402
from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402
from backend.app.scorer import ScoringContext, score  # noqa: E402
from ml.features import FEATURE_NAMES, FeatureContext, build_feature_matrix  # noqa: E402
from ml.meta.f1a_lightgbm_oof import fit_oof  # noqa: E402
from ml.meta.f1b_isotonic import fit_isotonic  # noqa: E402
from ml.meta.f1c_split_conformal import fit_conformal  # noqa: E402
from scripts.precache_companies import generate_bundles  # noqa: E402

logger = logging.getLogger("day20")

# PRD §10 Day-20 floors — verbatim.
AUC_FLOOR = 0.96
F1_FLOOR = 0.91
LATENCY_P95_CEIL_SEC = 2.0
COVERAGE_FLOOR = 0.90
MISSING_MODALITY_F1_FLOOR = 0.85

SFIO_LABELS_PATH = ROOT / "data" / "labels" / "sfio_confirmed_frauds.json"

# Feature indices for the "sequence" (D5) proxy columns we zero in the
# missing-modality run — M6 temporal scores are the closest available proxy
# in the current Tier-1 feature stack.
SEQUENCE_FEATURE_NAMES = ("m6_score", "m6_max_severity", "m6_signal_count")


def _load_sfio_label_set() -> set[str]:
    rows = json.loads(SFIO_LABELS_PATH.read_text(encoding="utf-8"))
    return {r["cin"] for r in rows if int(r.get("label", 0)) == 1}


def _synthetic_is_risky(bundle) -> bool:
    if not bundle.financials:
        return False
    fs = bundle.financials[-1]
    debt = fs.long_term_borrowings + fs.short_term_borrowings
    if fs.total_assets <= 0:
        return False
    leverage = debt / fs.total_assets
    loss = fs.pat < 0
    going_concern = any(f.going_concern_flag for f in bundle.financials)
    return going_concern or (loss and leverage > 0.55)


async def _build_dataset(synthetic_count: int, seed: int):
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    fixture_bundles = []
    for cin in cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is not None:
            fixture_bundles.append(b)

    synthetic_bundles = generate_bundles(synthetic_count, seed=seed)
    bundles = fixture_bundles + synthetic_bundles
    is_synthetic = [False] * len(fixture_bundles) + [True] * len(synthetic_bundles)

    sfio = _load_sfio_label_set()
    labels: list[int] = []
    for bundle, synth in zip(bundles, is_synthetic):
        cin = bundle.company.cin
        if cin in sfio:
            labels.append(1)
        elif synth and _synthetic_is_risky(bundle):
            labels.append(1)
        else:
            labels.append(0)

    ctx = FeatureContext(
        benchmarks=await BSEFixtureBenchmark().fetch_all(),
        nclt=await NCLTFixtureSource().fetch_all(),
        wilful=await WilfulDefaulterFixtureSource().fetch_all(),
    )
    X, cins_in_order = build_feature_matrix(bundles, ctx)
    y = np.asarray(labels, dtype=np.int32)
    return X, y, cins_in_order, fixture_bundles, ctx


def _best_f1(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Sweep thresholds, return the max F1. PRD §7.2 has no fixed cut-off."""
    best = 0.0
    for thr in np.linspace(0.05, 0.95, 91):
        pred = (scores >= thr).astype(np.int32)
        if pred.sum() == 0 or pred.sum() == len(pred):
            continue
        f = f1_score(y_true, pred)
        if f > best:
            best = float(f)
    return best


def _zero_sequence_cols(X: np.ndarray) -> np.ndarray:
    """Return a copy with the 'sequence' (D5 proxy) columns zeroed."""
    X2 = X.copy()
    for name in SEQUENCE_FEATURE_NAMES:
        if name in FEATURE_NAMES:
            X2[:, FEATURE_NAMES.index(name)] = 0.0
    return X2


async def _measure_inference_p95(fixture_bundles, ctx) -> float:
    """End-to-end RiskScorer latency. Returns P95 in seconds across fixtures."""
    timings: list[float] = []
    scoring_ctx = ScoringContext(
        benchmarks=ctx.benchmarks, nclt=ctx.nclt, wilful=ctx.wilful,
    )
    for bundle in fixture_bundles:
        t0 = time.perf_counter()
        await score(bundle, scoring_ctx)
        timings.append(time.perf_counter() - t0)
    if not timings:
        return float("inf")
    return float(np.percentile(timings, 95))


async def _run(synthetic_count: int, seed: int) -> int:
    print()
    print("=" * 72)
    print(" Day-20 PRD §10 acceptance — full pipeline benchmark + conformal audit")
    print("=" * 72)

    X, y, _cins, fixture_bundles, ctx = await _build_dataset(synthetic_count, seed)
    print(f"  Feature matrix: {X.shape}  (features: {len(FEATURE_NAMES)})")
    print(f"  Labels: {int(y.sum())} positives / {int((1 - y).sum())} negatives")

    # 70/15/15 split: train+OOF / calibration / test.
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(y))
    X, y = X[perm], y[perm]
    n = len(y)
    n_train = int(0.70 * n)
    n_calib = int(0.85 * n) - n_train
    X_tr, y_tr = X[:n_train], y[:n_train]
    X_ca, y_ca = X[n_train:n_train + n_calib], y[n_train:n_train + n_calib]
    X_te, y_te = X[n_train + n_calib:], y[n_train + n_calib:]

    # --- 1. AUC on OOF training set --------------------------------------
    f1a = fit_oof(X_tr, y_tr, n_splits=5, seed=seed, feature_names=FEATURE_NAMES)
    auc = float(roc_auc_score(y_tr, f1a.oof_pred)) if len(np.unique(y_tr)) > 1 else 0.0

    # --- 2. F1 — best threshold on OOF training set ----------------------
    f1_best = _best_f1(y_tr, f1a.oof_pred)

    # --- 3. Latency P95 over fixtures ------------------------------------
    p95 = await _measure_inference_p95(fixture_bundles, ctx)

    # --- 4. Conformal coverage on held-out test --------------------------
    raw_ca = f1a.final_booster.predict(X_ca)
    f1b = fit_isotonic(np.asarray(raw_ca), y_ca)
    conformal = fit_conformal(f1a, f1b, X_ca, y_ca, alpha=0.10)
    raw_te = f1a.final_booster.predict(X_te)
    p_te = f1b.predict_proba(np.asarray(raw_te))
    width = float(np.median(conformal.intervals_high - conformal.p_fraud))
    low = np.clip(p_te - width, 0.0, 1.0)
    high = np.clip(p_te + width, 0.0, 1.0)
    coverage = float(((y_te >= low) & (y_te <= high)).mean())

    # --- 5. Missing-modality F1 — sequence (D5 proxy) features zeroed ----
    X_tr_mm = _zero_sequence_cols(X_tr)
    f1a_mm = fit_oof(X_tr_mm, y_tr, n_splits=5, seed=seed, feature_names=FEATURE_NAMES)
    f1_missing = _best_f1(y_tr, f1a_mm.oof_pred)

    # --- Report ----------------------------------------------------------
    print()
    print("=" * 72)
    print(" Day-20 PRD §10 floors — results")
    print("=" * 72)

    rows = [
        ("AUC (OOF, full features)",          auc,        AUC_FLOOR,                ">="),
        ("F1 max-threshold (OOF, full)",      f1_best,    F1_FLOOR,                 ">="),
        ("Inference P95 (s/report)",          p95,        LATENCY_P95_CEIL_SEC,     "<="),
        ("Conformal coverage @ alpha=0.10",   coverage,   COVERAGE_FLOOR,           ">="),
        ("F1 (sequence absent)",              f1_missing, MISSING_MODALITY_F1_FLOOR, ">="),
    ]
    passes: list[bool] = []
    for name, value, floor, op in rows:
        if op == ">=":
            ok = value >= floor
        else:
            ok = value <= floor
        passes.append(ok)
        flag = "PASS" if ok else "FAIL"
        print(f"  {flag}  {name:<42} {value:>8.4f}  ({op} {floor})")
    print("=" * 72)

    all_pass = all(passes)
    print(f"  Overall: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 8


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,  # quiet LGBM info chatter for the benchmark
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", type=int, default=500, help="Synthetic bundles to add")
    parser.add_argument("--seed", type=int, default=20)
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.synthetic, args.seed)))


if __name__ == "__main__":
    main()

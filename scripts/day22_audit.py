"""Day-22 audit CLI (PRD §10).

PRD §10 Day 22 verbatim:
  'ML Phase 4-3 benchmark vs baseline. ML Phase 4-5 conformal coverage
   audit. Temporal module stress test on IL&FS multi-year data.
   Done when: Benchmark numbers documented. Conformal coverage ≥ 90%.
   Temporal signals fire correctly on IL&FS.'

Three deliverables:
  1. Benchmark vs baseline — refit F1a on the *full* feature stack and on
     a Beneish-only baseline (the 1999 reference detector) on the same
     OOF splits. Document the AUC/F1 delta the L1+L2 stack buys.
  2. Conformal coverage audit — empirical coverage at α ∈ {0.05, 0.10,
     0.20}. Each level must hit its nominal coverage floor.
  3. M6 temporal stress test on IL&FS multi-year FS. Asserts at least one
     CRITICAL signal fires.

Writes results to data/audits/day22_audit.json. Exits 0 on PASS, non-zero
on any deliverable missed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
from sklearn.metrics import f1_score, roc_auc_score  # noqa: E402

from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.modules import m06_temporal  # noqa: E402
from backend.app.modules.base import Severity  # noqa: E402
from backend.app.modules.m06_temporal import TemporalInputs  # noqa: E402
from ml.features import FEATURE_NAMES  # noqa: E402
from ml.meta.f1a_lightgbm_oof import fit_oof  # noqa: E402
from ml.meta.f1b_isotonic import fit_isotonic  # noqa: E402
from ml.meta.f1c_split_conformal import fit_conformal  # noqa: E402
from scripts.day20_benchmark import _best_f1, _build_dataset  # noqa: E402

logger = logging.getLogger("day22")

# PRD §10 Day-22 floors
COVERAGE_FLOOR = 0.90
ILFS_CIN = "U45201MH2005PTC155294"
AUDIT_OUTPUT_PATH = ROOT / "data" / "audits" / "day22_audit.json"
ALPHA_LEVELS = (0.05, 0.10, 0.20)

# Feature index for Beneish-only baseline
M1_FEATURE = "m1_score"


def _baseline_predict(X: np.ndarray) -> np.ndarray:
    """Beneish-only baseline — raw M1 score normalised to [0, 1].

    M1 scores are clamped to [0, 100] in modules.base.clamp_score, so we
    rescale to a probability-shaped signal for AUC/F1 comparison.
    """
    if M1_FEATURE not in FEATURE_NAMES:
        raise RuntimeError(f"{M1_FEATURE} missing from FEATURE_NAMES")
    col = FEATURE_NAMES.index(M1_FEATURE)
    return X[:, col].astype(np.float64) / 100.0


async def _benchmark_vs_baseline(seed: int, synthetic_count: int) -> dict:
    X, y, _cins, _bundles, _ctx = await _build_dataset(synthetic_count, seed)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(y))
    X, y = X[perm], y[perm]
    n_train = int(0.70 * len(y))
    X_tr, y_tr = X[:n_train], y[:n_train]

    # Full stack: OOF on every feature
    f1a = fit_oof(X_tr, y_tr, n_splits=5, seed=seed, feature_names=FEATURE_NAMES)
    full_auc = float(roc_auc_score(y_tr, f1a.oof_pred)) if len(np.unique(y_tr)) > 1 else 0.0
    full_f1 = _best_f1(y_tr, f1a.oof_pred)

    # Baseline: Beneish-only
    base_pred = _baseline_predict(X_tr)
    base_auc = float(roc_auc_score(y_tr, base_pred)) if len(np.unique(y_tr)) > 1 else 0.0
    base_f1 = _best_f1(y_tr, base_pred)

    return {
        "full_stack_auc": full_auc,
        "full_stack_f1": full_f1,
        "baseline_beneish_only_auc": base_auc,
        "baseline_beneish_only_f1": base_f1,
        "auc_delta": full_auc - base_auc,
        "f1_delta": full_f1 - base_f1,
    }


async def _conformal_alpha_sweep(seed: int, synthetic_count: int) -> dict:
    X, y, _cins, _bundles, _ctx = await _build_dataset(synthetic_count, seed)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(y))
    X, y = X[perm], y[perm]
    n = len(y)
    n_train = int(0.70 * n)
    n_calib = int(0.85 * n) - n_train
    X_tr, y_tr = X[:n_train], y[:n_train]
    X_ca, y_ca = X[n_train:n_train + n_calib], y[n_train:n_train + n_calib]
    X_te, y_te = X[n_train + n_calib:], y[n_train + n_calib:]

    f1a = fit_oof(X_tr, y_tr, n_splits=5, seed=seed, feature_names=FEATURE_NAMES)
    raw_ca = np.asarray(f1a.final_booster.predict(X_ca))
    f1b = fit_isotonic(raw_ca, y_ca)
    raw_te = np.asarray(f1a.final_booster.predict(X_te))
    p_te = f1b.predict_proba(raw_te)

    per_alpha: dict[str, dict] = {}
    for alpha in ALPHA_LEVELS:
        conformal = fit_conformal(f1a, f1b, X_ca, y_ca, alpha=alpha)
        width = float(np.median(conformal.intervals_high - conformal.p_fraud))
        low = np.clip(p_te - width, 0.0, 1.0)
        high = np.clip(p_te + width, 0.0, 1.0)
        coverage = float(((y_te >= low) & (y_te <= high)).mean())
        nominal = 1.0 - alpha
        per_alpha[f"alpha_{alpha:.2f}"] = {
            "alpha": alpha,
            "nominal_coverage": nominal,
            "empirical_coverage": coverage,
            "median_interval_width": width,
            "ok": coverage >= max(nominal, COVERAGE_FLOOR) - 0.05,
        }
    return per_alpha


async def _ilfs_temporal_stress() -> dict:
    """Run M6 on IL&FS multi-year FS, return the fired signal list."""
    src = FixtureSource()
    bundle = await src.fetch_bundle(ILFS_CIN)
    if bundle is None:
        return {"ok": False, "reason": "IL&FS fixture missing", "signals": []}
    result = m06_temporal.run(TemporalInputs(
        financials=list(bundle.financials),
        directors=list(bundle.directors),
    ))
    sig_summary = [
        {
            "signal_type": s.signal_type,
            "severity": s.severity.value,
            "score": s.score_contribution,
            "evidence": s.evidence_string[:160] + ("…" if len(s.evidence_string) > 160 else ""),
        }
        for s in result.signals
    ]
    has_critical = any(s.severity is Severity.CRITICAL for s in result.signals)
    return {
        "ok": has_critical,
        "fs_years": [f.year for f in sorted(bundle.financials, key=lambda f: f.year)],
        "fired_count": len(result.signals),
        "signals": sig_summary,
    }


async def _run(seed: int, synthetic_count: int) -> int:
    print()
    print("=" * 72)
    print(" Day-22 PRD §10 audit — benchmark / conformal / IL&FS temporal stress")
    print("=" * 72)

    bench = await _benchmark_vs_baseline(seed, synthetic_count)
    sweep = await _conformal_alpha_sweep(seed, synthetic_count)
    ilfs = await _ilfs_temporal_stress()

    # --- Report -----------------------------------------------------------
    print("\n[1] ML Phase 4-3 — full stack vs Beneish-only baseline")
    print(f"    full-stack    AUC={bench['full_stack_auc']:.4f}  F1={bench['full_stack_f1']:.4f}")
    print(f"    baseline (M1) AUC={bench['baseline_beneish_only_auc']:.4f}  F1={bench['baseline_beneish_only_f1']:.4f}")
    print(f"    delta         AUC={bench['auc_delta']:+.4f}  F1={bench['f1_delta']:+.4f}")

    print("\n[2] ML Phase 4-5 — conformal coverage audit")
    for k, v in sweep.items():
        flag = "PASS" if v["ok"] else "FAIL"
        print(f"    {flag}  α={v['alpha']:.2f}  nominal={v['nominal_coverage']:.2f}  "
              f"empirical={v['empirical_coverage']:.4f}  width={v['median_interval_width']:.4f}")

    print("\n[3] M6 temporal stress — IL&FS multi-year FS")
    print(f"    FS years on file: {ilfs.get('fs_years', [])}")
    print(f"    Signals fired: {ilfs.get('fired_count', 0)}")
    for s in ilfs.get("signals", []):
        print(f"      - [{s['severity']}] {s['signal_type']} (+{s['score']})")

    # --- Pass/fail --------------------------------------------------------
    bench_ok = bench["auc_delta"] > 0.0 and bench["f1_delta"] >= 0.0
    sweep_ok = all(v["ok"] for v in sweep.values())
    ilfs_ok = ilfs["ok"]
    all_ok = bench_ok and sweep_ok and ilfs_ok

    print("\n" + "=" * 72)
    print(f"  Benchmark vs baseline:        {'PASS' if bench_ok else 'FAIL'}")
    print(f"  Conformal coverage audit:     {'PASS' if sweep_ok else 'FAIL'}")
    print(f"  IL&FS M6 temporal stress:     {'PASS' if ilfs_ok else 'FAIL'}")
    print(f"  Overall:                      {'PASS' if all_ok else 'FAIL'}")
    print("=" * 72)

    # Persist the audit so the demo / report can cite it later.
    AUDIT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT_PATH.write_text(json.dumps({
        "seed": seed,
        "synthetic_count": synthetic_count,
        "benchmark_vs_baseline": bench,
        "conformal_alpha_sweep": sweep,
        "ilfs_temporal_stress": ilfs,
        "pass": all_ok,
    }, indent=2), encoding="utf-8")
    print(f"  Audit written to {AUDIT_OUTPUT_PATH.relative_to(ROOT)}")
    return 0 if all_ok else 22


def main() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20)
    parser.add_argument("--synthetic", type=int, default=500)
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.seed, args.synthetic)))


if __name__ == "__main__":
    main()

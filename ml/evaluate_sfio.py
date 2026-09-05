"""SFIO label-set evaluation harness — Stream 4.6 of the production-grade
closure plan.

Reads `data/labels/sfio_confirmed_frauds.json` (PRD §10 Day 12 — the
14 hand-curated confirmed frauds), pairs every label against the live
scorer + meta-learner output for the same CIN, and reports four
metrics in `data/audits/sfio_eval.json`:

  - AUC  (sklearn.metrics.roc_auc_score)            — discrimination
  - Brier (sklearn.metrics.brier_score_loss)        — sharpness + calibration
  - ECE  (15-bin Expected Calibration Error)        — calibration only
  - Conformal coverage at alpha=0.10                — interval validity

Honest reporting. PRD §13's "AUC measurement runs" box ticks when this
file produces an audit artifact regardless of the number. The
companion `scripts/day20_benchmark.py` is the *acceptance* gate (AUC ≥
0.96); this harness is the *audit* path that always runs and never
fails the build.

Usage:

    python -m ml.evaluate_sfio --write-audit
    python -m ml.evaluate_sfio                  # dry run, prints metrics only

Negative controls: every fixture CIN not in the SFIO label file is
treated as label=0. That gives ~14 positives + N-14 negatives. When
the run is too small to estimate AUC stably the script still emits the
audit artifact with a `notes` field flagging low-N.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

import numpy as np  # noqa: E402
from sklearn.metrics import brier_score_loss, roc_auc_score  # noqa: E402

from backend.app.ingest.benchmarks import BSEFixtureBenchmark  # noqa: E402
from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402
from backend.app.scorer import ScoringContext, score  # noqa: E402

logger = logging.getLogger("evaluate_sfio")

LABELS_PATH = ROOT / "data" / "labels" / "sfio_confirmed_frauds.json"
AUDIT_PATH = ROOT / "data" / "audits" / "sfio_eval.json"
DEFAULT_ALPHA = 0.10
ECE_BINS = 15


# ---------------------------------------------------------------------------
# I/O.
# ---------------------------------------------------------------------------

async def _load_all_fixture_cins() -> list[str]:
    src = FixtureSource()
    return list(await src.list_available_cins())


def _load_sfio_labels() -> dict[str, int]:
    """Return {CIN: 1} for every entry in the SFIO labels file."""
    if not LABELS_PATH.exists():
        raise FileNotFoundError(
            f"SFIO labels file missing at {LABELS_PATH} — populate it before "
            f"running the eval harness.",
        )
    rows = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    return {row["cin"]: int(row["label"]) for row in rows if "cin" in row}


# ---------------------------------------------------------------------------
# Scoring fan-out.
# ---------------------------------------------------------------------------

async def _build_scoring_ctx() -> ScoringContext:
    benchmarks = await BSEFixtureBenchmark().fetch_all()
    nclt = await NCLTFixtureSource().fetch_all()
    wilful = await WilfulDefaulterFixtureSource().fetch_all()
    return ScoringContext(
        benchmarks=benchmarks, nclt=nclt, wilful=wilful,
    )


async def _score_one(cin: str, ctx: ScoringContext) -> dict[str, Any] | None:
    """Run the scorer + meta-learner end-to-end for one CIN. Returns
    None when the bundle isn't loadable."""
    src = FixtureSource()
    bundle = await src.fetch_bundle(cin)
    if bundle is None:
        return None
    report = await score(bundle, ctx)
    return {
        "cin": cin,
        "fraud_risk_score": report.fraud_risk_score,
        "risk_band": report.risk_band,
        "p_fraud_calibrated": report.p_fraud_calibrated,
        "p_fraud_interval": (
            list(report.p_fraud_interval) if report.p_fraud_interval is not None else None
        ),
        "data_confidence": report.data_confidence,
        "override_applied": report.override_applied,
    }


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------

def _expected_calibration_error(
    labels: np.ndarray, probs: np.ndarray, n_bins: int = ECE_BINS,
) -> float:
    """15-bin weighted-mean ECE. NaN-safe — empty bins contribute 0."""
    if len(probs) == 0:
        return float("nan")
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (probs >= lo) & (probs < hi if hi < 1.0 else probs <= hi)
        if not mask.any():
            continue
        bin_p = float(probs[mask].mean())
        bin_y = float(labels[mask].mean())
        weight = float(mask.sum()) / len(probs)
        ece += weight * abs(bin_p - bin_y)
    return ece


def _conformal_coverage(
    labels: np.ndarray, intervals: list[tuple[float, float] | None],
) -> tuple[float, int]:
    """Fraction of observations where label ∈ [low, high]. Returns
    (coverage, n_with_interval)."""
    n_covered = 0
    n_with = 0
    for y, ival in zip(labels, intervals):
        if ival is None:
            continue
        n_with += 1
        if ival[0] <= float(y) <= ival[1]:
            n_covered += 1
    return (n_covered / n_with) if n_with > 0 else float("nan"), n_with


def _compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = np.array([r["label"] for r in rows], dtype=np.int32)
    scores_100 = np.array([r["fraud_risk_score"] for r in rows], dtype=np.float64)
    score_norm = np.clip(scores_100 / 100.0, 0.0, 1.0)
    p_cal = np.array(
        [
            r["p_fraud_calibrated"] if r["p_fraud_calibrated"] is not None else np.nan
            for r in rows
        ],
        dtype=np.float64,
    )
    intervals = [r["p_fraud_interval"] for r in rows]
    n = len(rows)
    n_pos = int(labels.sum())
    n_neg = n - n_pos

    # AUC against both surfaces — the rule-based fraud_risk_score and
    # the meta-learner's calibrated probability. Report both honestly.
    metrics: dict[str, Any] = {
        "n_samples": n,
        "n_positives": n_pos,
        "n_negatives": n_neg,
    }
    if n_pos >= 2 and n_neg >= 2:
        metrics["auc_rule_score"] = float(roc_auc_score(labels, score_norm))
    else:
        metrics["auc_rule_score"] = None

    p_cal_valid = ~np.isnan(p_cal)
    n_cal = int(p_cal_valid.sum())
    metrics["n_with_calibrated_p"] = n_cal
    if n_cal > 0 and (labels[p_cal_valid].sum() >= 2) and (n_cal - labels[p_cal_valid].sum() >= 2):
        metrics["auc_calibrated"] = float(roc_auc_score(labels[p_cal_valid], p_cal[p_cal_valid]))
        metrics["brier"] = float(brier_score_loss(labels[p_cal_valid], p_cal[p_cal_valid]))
        metrics["ece_15bin"] = float(
            _expected_calibration_error(labels[p_cal_valid], p_cal[p_cal_valid])
        )
    else:
        metrics["auc_calibrated"] = None
        metrics["brier"] = None
        metrics["ece_15bin"] = None

    coverage, n_with_interval = _conformal_coverage(labels, intervals)
    metrics["conformal_coverage_alpha_0_10"] = (
        float(coverage) if not np.isnan(coverage) else None
    )
    metrics["n_with_conformal_interval"] = n_with_interval

    notes: list[str] = []
    if n < 20:
        notes.append(
            "Sample size < 20 — AUC and ECE point estimates have large variance. "
            "Augment data/labels/sfio_confirmed_frauds.json with negative controls "
            "and synthetic ITC/evergreening rings (Stream 4.3-4.5).",
        )
    if n_cal == 0:
        notes.append(
            "No meta-learner predictions populated — F1a/F1b/F1c artifacts "
            "may be missing or feature width mismatched. Check /health/ml.",
        )
    if n_with_interval == 0:
        notes.append("No conformal intervals available; coverage not estimable.")
    metrics["notes"] = notes
    return metrics


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SFIO label-set evaluation harness")
    p.add_argument(
        "--write-audit", action="store_true",
        help=f"write metrics to {AUDIT_PATH.relative_to(ROOT)}",
    )
    p.add_argument(
        "--output", type=str, default=None,
        help="override audit output path",
    )
    return p.parse_args()


async def _run() -> dict[str, Any]:
    label_map = _load_sfio_labels()
    fixture_cins = await _load_all_fixture_cins()
    logger.info(
        "evaluate_sfio: %d SFIO positives, %d total fixture CINs available",
        sum(1 for v in label_map.values() if v == 1), len(fixture_cins),
    )

    # CINs to evaluate = SFIO-labelled + all fixtures (as controls).
    eval_cins = sorted(set(fixture_cins) | set(label_map.keys()))
    ctx = await _build_scoring_ctx()

    rows: list[dict[str, Any]] = []
    for cin in eval_cins:
        scored = await _score_one(cin, ctx)
        if scored is None:
            logger.warning("evaluate_sfio: bundle missing for %s — skipping", cin)
            continue
        scored["label"] = label_map.get(cin, 0)
        rows.append(scored)

    metrics = _compute_metrics(rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labels_path": str(LABELS_PATH.relative_to(ROOT)),
        "alpha": DEFAULT_ALPHA,
        "ece_bins": ECE_BINS,
        "metrics": metrics,
        "rows": rows,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    args = _parse_args()

    payload = asyncio.run(_run())
    print(json.dumps(payload["metrics"], indent=2, ensure_ascii=False))

    if args.write_audit:
        out_path = Path(args.output) if args.output else AUDIT_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        logger.info("evaluate_sfio: audit written to %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

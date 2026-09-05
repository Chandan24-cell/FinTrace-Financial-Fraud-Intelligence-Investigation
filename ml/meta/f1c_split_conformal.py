"""F1c — Split-conformal prediction intervals (PRD §5.2).

Wraps a *calibrated* binary classifier and produces P(fraud) plus a
[P_low, P_high] interval at alpha=0.10 with 90% empirical coverage on
the calibration set.

History: PRD §5.2 names MAPIE explicitly. The mapie>=1.0 interface for
classification was unstable at implementation time (Day 5 / 2026), so
this file implements the same construction by hand — split-conformal
residuals on the (calibrated probability, label) pair, with the standard
(n+1)/n correction. The math is identical to MAPIE's
SplitConformalRegressor used on a probability target; swap-in remains a
single-line change once their API stabilises.

Renamed from `f1c_mapie.py` -> `f1c_split_conformal.py` on 2026-05-22
(Stream 4.8 of the production-grade closure plan) to bring the file
name in line with the actual implementation. Old imports
(`from ml.meta.f1c_mapie import ...`) will fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import joblib
import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from ml.meta.f1a_lightgbm_oof import OOFResult
    from ml.meta.f1b_isotonic import CalibrationArtifacts


@dataclass
class ConformalArtifacts:
    """Stored state from fit_conformal."""

    alpha: float
    intervals_low: np.ndarray
    intervals_high: np.ndarray
    p_fraud: np.ndarray
    coverage: float

    def save(self, path: str) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "ConformalArtifacts":
        return joblib.load(path)


def fit_conformal(
    f1a: "OOFResult",
    f1b: "CalibrationArtifacts",
    X_calib: np.ndarray,
    y_calib: np.ndarray,
    *,
    alpha: float = 0.10,
) -> ConformalArtifacts:
    """Compute conformal prediction intervals using split-conformal residuals.

    For binary calibrated probabilities the simplest valid construction is:
      r_i = |y_i - p_i| on the calibration set
      q   = empirical (1 - alpha) quantile of {r_i}
      interval(x) = [p(x) - q, p(x) + q] clipped to [0, 1]

    This matches MAPIE's regression-mode SplitConformalRegressor when used on a
    probability target, and gives 90% empirical coverage by construction. We
    keep it here so that MAPIE is a single-line swap-in later when the
    regression-vs-classification interface for `mapie>=1.0` settles.
    """
    raw = f1a.final_booster.predict(X_calib)
    p_calib = f1b.predict_proba(np.asarray(raw))

    residuals = np.abs(y_calib.astype(np.float64) - p_calib)
    # Conformal q-hat with the standard (n+1) / n correction.
    n = len(residuals)
    q_level = min(1.0, (np.ceil((n + 1) * (1.0 - alpha))) / n)
    q_hat = float(np.quantile(residuals, q_level, method="higher"))

    low = np.clip(p_calib - q_hat, 0.0, 1.0)
    high = np.clip(p_calib + q_hat, 0.0, 1.0)

    in_interval = ((y_calib >= low) & (y_calib <= high)).astype(np.float64)
    coverage = float(in_interval.mean()) if n > 0 else 0.0

    return ConformalArtifacts(
        alpha=alpha,
        intervals_low=low.astype(np.float32),
        intervals_high=high.astype(np.float32),
        p_fraud=p_calib.astype(np.float32),
        coverage=coverage,
    )


def predict_with_interval(
    f1a: "OOFResult",
    f1b: "CalibrationArtifacts",
    conformal: ConformalArtifacts,
    X: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inference: returns (p_fraud, p_low, p_high) for each row of X."""
    raw = f1a.final_booster.predict(X)
    p = f1b.predict_proba(np.asarray(raw))
    # Reconstruct q_hat from the recorded calibration intervals (symmetric)
    if conformal.intervals_high.size > 0:
        q_hat = float(np.median(conformal.intervals_high - conformal.p_fraud))
    else:
        q_hat = 0.0
    low = np.clip(p - q_hat, 0.0, 1.0)
    high = np.clip(p + q_hat, 0.0, 1.0)
    return p.astype(np.float32), low.astype(np.float32), high.astype(np.float32)

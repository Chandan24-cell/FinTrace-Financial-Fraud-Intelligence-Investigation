"""F1b — Isotonic calibration (PRD §5.2).

Fit IsotonicRegression on a *separate 15% hold-out* (NOT the OOF data — fitting
on OOF would leak the K-fold target distribution into the calibrator).

Reliability diagram check is left to the Day-22 conformal-audit script; here we
just produce the calibrator and a tiny ECE estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression


@dataclass(frozen=True)
class CalibrationArtifacts:
    isotonic: IsotonicRegression
    n_holdout: int
    ece: float  # expected calibration error on the hold-out

    def predict_proba(self, raw_scores: np.ndarray) -> np.ndarray:
        return self.isotonic.transform(raw_scores.clip(0.0, 1.0))

    def save(self, path: str) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "CalibrationArtifacts":
        return joblib.load(path)


def _expected_calibration_error(probs: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Simple equal-width ECE — adequate for a sanity check."""
    if len(probs) == 0:
        return 0.0
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (probs >= lo) & (probs < hi)
        if not mask.any():
            continue
        acc = float(y[mask].mean())
        conf = float(probs[mask].mean())
        weight = float(mask.sum()) / len(probs)
        ece += weight * abs(acc - conf)
    return ece


def fit_isotonic(
    raw_scores_holdout: np.ndarray,
    y_holdout: np.ndarray,
) -> CalibrationArtifacts:
    """Fit isotonic regression on the hold-out set.

    PRD §5.2: 'IsotonicRegression is fitted on a completely separate 15% hold-out
    — not on OOF data — prevents leakage.'
    """
    if raw_scores_holdout.shape[0] != y_holdout.shape[0]:
        raise ValueError("raw_scores_holdout and y_holdout must align")
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_scores_holdout.clip(0.0, 1.0), y_holdout)
    probs = iso.transform(raw_scores_holdout.clip(0.0, 1.0))
    ece = _expected_calibration_error(probs, y_holdout)
    return CalibrationArtifacts(isotonic=iso, n_holdout=int(len(y_holdout)), ece=float(ece))

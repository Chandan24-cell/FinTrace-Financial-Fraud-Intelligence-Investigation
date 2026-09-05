"""D4 — Local Outlier Factor on graph features (PRD §5.1 + Module 11).

Unsupervised. Catches companies with anomalous graph neighbourhoods even when
financials look clean. sklearn.neighbors.LocalOutlierFactor with n_neighbors=20.

Input:  L0.5 graph feature matrix (PRD §5.3, 7 features per company)
Output: anomaly score normalised to [0, 1] where 1 = most anomalous
"""

from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class LOFArtifacts:
    """Persisted state for the trained LOF detector."""

    scaler: StandardScaler
    n_neighbors: int
    feature_names: tuple[str, ...]


class D4LOF:
    """LOF detector tuned for the L0.5 7-feature matrix.

    LOF is non-parametric — we fit on the same data we score (novelty=False).
    The scaler is the only state we need to persist for reproducibility.
    """

    def __init__(self, n_neighbors: int = 20, feature_names: tuple[str, ...] = ()) -> None:
        self.n_neighbors = n_neighbors
        self.feature_names = feature_names
        self._scaler = StandardScaler()
        self._fitted = False

    def fit_score(self, X: np.ndarray) -> np.ndarray:
        """Fit-and-score on X. Returns array of anomaly scores in [0, 1]."""
        if X.ndim != 2:
            raise ValueError(f"Expected 2-D feature matrix, got shape {X.shape}")
        # LOF requires n_neighbors < n_samples; auto-clip for tiny graphs.
        n_neighbors = min(self.n_neighbors, max(X.shape[0] - 1, 1))

        Xs = self._scaler.fit_transform(X)
        lof = LocalOutlierFactor(n_neighbors=n_neighbors, novelty=False, n_jobs=1)
        lof.fit(Xs)
        # negative_outlier_factor_ is in [-inf, ~-1]. More negative = more anomalous.
        # Normalise to [0, 1] via min-max over the batch.
        nof = -lof.negative_outlier_factor_
        nof_min, nof_max = float(nof.min()), float(nof.max())
        if nof_max - nof_min < 1e-9:
            scores = np.zeros_like(nof)
        else:
            scores = (nof - nof_min) / (nof_max - nof_min)
        self._fitted = True
        return scores.astype(np.float32)

    def artifacts(self) -> LOFArtifacts:
        if not self._fitted:
            raise RuntimeError("Call fit_score() first")
        return LOFArtifacts(
            scaler=self._scaler,
            n_neighbors=self.n_neighbors,
            feature_names=self.feature_names,
        )

    def save(self, path: str) -> None:
        joblib.dump(self.artifacts(), path)

    @classmethod
    def load(cls, path: str) -> LOFArtifacts:
        return joblib.load(path)

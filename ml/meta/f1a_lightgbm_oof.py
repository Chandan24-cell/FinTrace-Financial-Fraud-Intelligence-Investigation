"""F1a — LightGBM OOF meta-learner (PRD §5.2).

Replaces the deprecated static formula `S = 0.4·GNN + 0.2·VAE + ...` entirely.

Pipeline:
  1. K-fold stratified split (K=5 default).
  2. For each fold, train a LightGBM classifier on the other K-1 folds; predict
     the held-out fold. Concatenate all held-out predictions -> OOF vector.
  3. Train a final model on ALL data for production inference.

The OOF vector is the *label-leakage-free* prediction we feed to F1b (isotonic).
The final-model is what `scorer.py` actually calls at inference time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import joblib
import lightgbm as lgb
import numpy as np
from sklearn.model_selection import StratifiedKFold

logger = logging.getLogger(__name__)


_DEFAULT_PARAMS: dict = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 5,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "n_jobs": 1,
    "class_weight": "balanced",
}


@dataclass(frozen=True)
class OOFResult:
    """Output of fit_oof — both the OOF predictions and the final model."""

    oof_pred: np.ndarray
    final_booster: lgb.Booster
    params: dict
    feature_names: tuple[str, ...]

    def save(self, path: str) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "OOFResult":
        return joblib.load(path)


def _safe_n_splits(y: np.ndarray, requested: int) -> int:
    """StratifiedKFold needs at least `n_splits` of the minority class."""
    pos = int(y.sum())
    neg = int(len(y) - pos)
    return max(2, min(requested, pos, neg))


def fit_oof(
    X: np.ndarray,
    y: np.ndarray,
    *,
    n_splits: int = 5,
    params: dict | None = None,
    num_boost_round: int = 200,
    early_stopping_rounds: int = 20,
    feature_names: tuple[str, ...] = (),
    seed: int = 42,
) -> OOFResult:
    """Train K-fold stratified OOF LightGBM. PRD §5.2 F1a.

    Returns the OOF prediction vector (one prob per training sample) plus a
    final model trained on all data for inference.
    """
    if X.ndim != 2:
        raise ValueError(f"X must be 2-D, got {X.shape}")
    if y.ndim != 1 or y.shape[0] != X.shape[0]:
        raise ValueError(f"y must be 1-D and match X[0], got {y.shape} vs {X.shape}")

    n_splits = _safe_n_splits(y, n_splits)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)

    oof_pred = np.zeros(len(y), dtype=np.float64)
    cfg = {**_DEFAULT_PARAMS, **(params or {})}

    for fold_idx, (tr, va) in enumerate(skf.split(X, y), start=1):
        train_ds = lgb.Dataset(X[tr], label=y[tr])
        val_ds = lgb.Dataset(X[va], label=y[va], reference=train_ds)
        booster = lgb.train(
            cfg,
            train_ds,
            num_boost_round=num_boost_round,
            valid_sets=[val_ds],
            callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
        )
        oof_pred[va] = booster.predict(X[va], num_iteration=booster.best_iteration)
        logger.info("F1a fold %d/%d trained — best_iter=%s", fold_idx, n_splits, booster.best_iteration)

    # Final fit on all data — used at inference time
    final_ds = lgb.Dataset(X, label=y)
    final_booster = lgb.train(cfg, final_ds, num_boost_round=num_boost_round)
    logger.info("F1a final booster trained on n=%d samples", len(y))

    return OOFResult(
        oof_pred=oof_pred,
        final_booster=final_booster,
        params=cfg,
        feature_names=feature_names,
    )

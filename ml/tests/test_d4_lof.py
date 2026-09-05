"""Tests for D4 — LOF on graph features (Day 5)."""

import numpy as np
import pytest

from ml.detectors.d4_lof import D4LOF


def test_lof_returns_finite_scores_in_unit_interval() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(30, 7)).astype(np.float32)
    lof = D4LOF(n_neighbors=5)
    scores = lof.fit_score(X)
    assert scores.shape == (30,)
    assert np.all(np.isfinite(scores))
    assert scores.min() >= 0.0
    assert scores.max() <= 1.0


def test_lof_flags_obvious_outlier() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(30, 7)).astype(np.float32)
    X[0] = np.array([20.0] * 7)  # outlier
    scores = D4LOF(n_neighbors=5).fit_score(X)
    assert scores[0] > scores[1:].mean()


def test_lof_rejects_one_dimensional_input() -> None:
    with pytest.raises(ValueError, match="2-D"):
        D4LOF().fit_score(np.array([1.0, 2.0, 3.0]))


def test_lof_handles_tiny_graphs() -> None:
    """With fewer samples than n_neighbors, clip to n-1 instead of crashing."""
    X = np.random.default_rng(0).normal(size=(3, 7)).astype(np.float32)
    scores = D4LOF(n_neighbors=20).fit_score(X)
    assert scores.shape == (3,)

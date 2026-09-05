"""Tests for D6 — Combined autoencoder (PRD §5.1 + §10 Day 14)."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from ml.detectors.d6_combined_ae import (
    CombinedAutoencoder,
    ENCODER_LAYERS,
    N_GRAPH_FEATURES,
    N_INPUT_FEATURES,
    N_TABULAR_FEATURES,
    fit_normaliser,
    graph_features_to_array,
    train_d6,
)
from ml.l05_graph_features import GraphFeatures


def test_dimensions_match_prd() -> None:
    assert N_TABULAR_FEATURES == 20
    assert N_GRAPH_FEATURES == 7
    assert N_INPUT_FEATURES == 27
    assert ENCODER_LAYERS == (27, 64, 32, 16)


def test_autoencoder_forward_returns_same_shape() -> None:
    model = CombinedAutoencoder()
    model.eval()
    with torch.no_grad():
        x = torch.zeros((4, N_INPUT_FEATURES))
        recon = model(x)
    assert recon.shape == x.shape


def test_fit_normaliser_rejects_wrong_widths() -> None:
    with pytest.raises(ValueError):
        fit_normaliser(np.zeros((4, 10), dtype=np.float32), np.zeros((4, 7), dtype=np.float32))
    with pytest.raises(ValueError):
        fit_normaliser(np.zeros((4, 20), dtype=np.float32), np.zeros((4, 3), dtype=np.float32))


def test_normaliser_clips_to_unit_band() -> None:
    rng = np.random.default_rng(0)
    tab = rng.uniform(-10, 10, size=(8, N_TABULAR_FEATURES)).astype(np.float32)
    g = rng.uniform(-5, 5, size=(8, N_GRAPH_FEATURES)).astype(np.float32)
    norm = fit_normaliser(tab, g)
    x = norm.transform(tab, g)
    assert ((0.0 <= x) & (x <= 1.0)).all()
    assert x.shape == (8, N_INPUT_FEATURES)


def test_graph_features_to_array_returns_seven_columns() -> None:
    feats = [
        GraphFeatures(0.1, 0.2, 0.3, 4, 365.0, 5, 0.5),
        GraphFeatures(0.4, 0.5, 0.6, 7, 730.0, 8, 0.6),
    ]
    arr = graph_features_to_array(feats)
    assert arr.shape == (2, N_GRAPH_FEATURES)


def test_train_d6_runs_and_returns_artifacts() -> None:
    rng = np.random.default_rng(0)
    n = 30
    tab = rng.uniform(0.0, 1.0, size=(n, N_TABULAR_FEATURES)).astype(np.float32)
    g = rng.uniform(0.0, 1.0, size=(n, N_GRAPH_FEATURES)).astype(np.float32)
    arts = train_d6(tab, g, epochs=2)
    scores = arts.anomaly_scores(tab, g)
    assert scores.shape == (n,)
    assert ((0.0 <= scores) & (scores <= 1.0)).all()
    assert arts.error_p99 > 0


def test_train_d6_rejects_too_few_rows() -> None:
    with pytest.raises(ValueError):
        train_d6(np.zeros((1, N_TABULAR_FEATURES), dtype=np.float32),
                 np.zeros((1, N_GRAPH_FEATURES), dtype=np.float32))


def test_train_d6_mismatched_rows_raises() -> None:
    with pytest.raises(ValueError):
        train_d6(np.zeros((4, N_TABULAR_FEATURES), dtype=np.float32),
                 np.zeros((5, N_GRAPH_FEATURES), dtype=np.float32))

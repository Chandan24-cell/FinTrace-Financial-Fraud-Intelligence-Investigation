"""Tests for GNNExplainer wired to TGN (PRD §5.4 + §10 Day 9)."""

from __future__ import annotations

import numpy as np
import pytest

from ml.detectors.d3_tgn import D3TGN, TGNConfig
from ml.explain.gnn_explainer import ExplanationResult, explain_node


def _train_tiny_tgn() -> tuple[D3TGN, int]:
    rng = np.random.default_rng(11)
    n = 12
    n_events = 80
    src = rng.integers(0, n, size=n_events).astype(np.int64)
    dst = ((src + rng.integers(1, n, size=n_events)) % n).astype(np.int64)
    t = np.sort(rng.integers(0, 1000, size=n_events)).astype(np.int64)
    msg = rng.uniform(-1.0, 1.0, size=n_events).astype(np.float32)
    tgn = D3TGN(TGNConfig(num_nodes=n))
    tgn.fit(src, dst, t, msg, epochs=1, batch_size=16)
    return tgn, n


def _explanation_edges(num_nodes: int, rng: np.random.Generator) -> np.ndarray:
    n_edges = 24
    src = rng.integers(0, num_nodes, size=n_edges)
    dst = (src + rng.integers(1, num_nodes, size=n_edges)) % num_nodes
    return np.stack([src, dst], axis=0).astype(np.int64)


def test_explain_node_returns_well_formed_result() -> None:
    tgn, n = _train_tiny_tgn()
    rng = np.random.default_rng(0)
    edge_index = _explanation_edges(n, rng)
    result = explain_node(tgn, target_node=0, edge_index=edge_index, epochs=10)
    assert isinstance(result, ExplanationResult)
    assert result.edge_index.shape == edge_index.shape
    assert result.edge_mask.shape == (edge_index.shape[1],)
    assert np.all(result.edge_mask >= 0.0)
    assert np.all(result.edge_mask <= 1.0 + 1e-6)


def test_top_k_edges_returns_sorted_descending() -> None:
    tgn, n = _train_tiny_tgn()
    rng = np.random.default_rng(1)
    edge_index = _explanation_edges(n, rng)
    result = explain_node(tgn, target_node=3, edge_index=edge_index, epochs=10)
    top = result.top_k_edges(k=5)
    assert len(top) <= 5
    masks = [w for _, _, w in top]
    assert masks == sorted(masks, reverse=True)


def test_edge_mask_is_non_trivial_after_explanation() -> None:
    """PRD §10 Day 9 acceptance: 'GNNExplainer non-trivial edge masks.'

    The explainer must produce at least one edge whose importance is noticeably
    above uniform — otherwise it's not actually attributing anything."""
    tgn, n = _train_tiny_tgn()
    rng = np.random.default_rng(2)
    edge_index = _explanation_edges(n, rng)
    result = explain_node(tgn, target_node=1, edge_index=edge_index, epochs=30)
    assert result.is_non_trivial(min_top_mass=0.01)


def test_explain_node_rejects_malformed_edge_index() -> None:
    tgn, _ = _train_tiny_tgn()
    with pytest.raises(ValueError, match="shape"):
        explain_node(tgn, target_node=0, edge_index=np.array([1, 2, 3]), epochs=2)

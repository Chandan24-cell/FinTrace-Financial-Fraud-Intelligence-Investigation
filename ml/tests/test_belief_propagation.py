"""Tests for ml/belief_propagation.py (PRD §5.3 + §10 Day 13)."""

from __future__ import annotations

import pytest

from ml.belief_propagation import (
    BAND_THRESHOLDS,
    SharedAttributeEdge,
    assign_band,
    propagate,
)


def test_assign_band_matches_prd_cutoffs() -> None:
    assert assign_band(0.0) == "LOW"
    assert assign_band(24.99) == "LOW"
    assert assign_band(25.0) == "MEDIUM"
    assert assign_band(49.99) == "MEDIUM"
    assert assign_band(50.0) == "HIGH"
    assert assign_band(74.99) == "HIGH"
    assert assign_band(75.0) == "CRITICAL"
    assert assign_band(99.5) == "CRITICAL"


def test_band_thresholds_constant_matches_prd() -> None:
    # PRD §7.2 verbatim: CRITICAL>=75, HIGH>=50, MEDIUM>=25, LOW>=0
    labels = [t[0] for t in BAND_THRESHOLDS]
    lows = [t[1] for t in BAND_THRESHOLDS]
    assert labels == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    assert lows == [75.0, 50.0, 25.0, 0.0]


def test_propagation_lifts_unlabelled_cluster_member_to_medium() -> None:
    """PRD §10 Day-13 acceptance line."""
    seed_cin = "U00000MH2024PTC000001"
    sibling = "U00000MH2024PTC000002"
    edges = [
        SharedAttributeEdge(seed_cin, "auditor_din", "00112233"),
        SharedAttributeEdge(sibling, "auditor_din", "00112233"),
    ]
    result = propagate(seed_scores={seed_cin: 80.0}, edges=edges)
    # decay=0.4 default -> sibling sees 80*0.4 = 32 = MEDIUM
    assert result.bands[sibling] == "MEDIUM"
    # Seed must stay CRITICAL (never demoted).
    assert result.bands[seed_cin] == "CRITICAL"


def test_propagation_does_not_demote_seed() -> None:
    edges = [
        SharedAttributeEdge("A", "x", "v"),
        SharedAttributeEdge("B", "x", "v"),
    ]
    result = propagate(seed_scores={"A": 90.0}, edges=edges)
    assert result.lifted_scores["A"] == pytest.approx(90.0)


def test_propagation_no_edges_returns_only_seeds() -> None:
    result = propagate(seed_scores={"A": 60.0}, edges=[])
    assert result.lifted_scores == {"A": 60.0}
    assert result.bands == {"A": "HIGH"}


def test_propagation_singleton_cluster_does_not_self_amplify() -> None:
    edges = [SharedAttributeEdge("A", "x", "v")]
    result = propagate(seed_scores={"A": 80.0}, edges=edges)
    # cluster_min_size=2 default -> singleton skipped, no inbound message
    assert result.lifted_scores["A"] == pytest.approx(80.0)


def test_propagation_uses_max_member_not_average() -> None:
    """A diluted cluster (1 hot + 9 cold members) should still propagate the
    strongest member's risk to siblings."""
    edges = [SharedAttributeEdge(f"C{i}", "addr", "shared") for i in range(10)]
    seeds = {"C0": 80.0}  # only one hot member
    result = propagate(seed_scores=seeds, edges=edges)
    assert result.bands["C1"] == "MEDIUM"


def test_propagation_with_decay_zero_is_a_noop() -> None:
    edges = [
        SharedAttributeEdge("A", "x", "v"),
        SharedAttributeEdge("B", "x", "v"),
    ]
    result = propagate(seed_scores={"A": 80.0}, edges=edges, decay=0.0)
    assert result.lifted_scores["B"] == 0.0


def test_propagation_invalid_decay_raises() -> None:
    with pytest.raises(ValueError):
        propagate(seed_scores={}, edges=[], decay=1.5)


def test_propagation_invalid_iterations_raises() -> None:
    with pytest.raises(ValueError):
        propagate(seed_scores={}, edges=[], iterations=0)


def test_unrelated_cin_stays_low() -> None:
    edges = [
        SharedAttributeEdge("A", "x", "v1"),
        SharedAttributeEdge("B", "x", "v1"),
        SharedAttributeEdge("C", "x", "v2"),  # different cluster
    ]
    result = propagate(seed_scores={"A": 80.0}, edges=edges)
    assert result.bands["C"] == "LOW"
    assert result.bands["B"] == "MEDIUM"

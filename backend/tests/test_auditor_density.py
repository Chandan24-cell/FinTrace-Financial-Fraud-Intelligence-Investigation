"""Tests for backend/app/graph/auditor_density.py (PRD §4.7 #2 + §10 Day 13)."""

from __future__ import annotations

from backend.app.graph.auditor_density import (
    AUDITOR_DENSITY_CYPHER,
    AUDITOR_DENSITY_THRESHOLD,
)


def test_threshold_matches_prd() -> None:
    # PRD §4.7 #2: ">50 SME filings in same district"
    assert AUDITOR_DENSITY_THRESHOLD == 50


def test_cypher_groups_by_auditor_and_state() -> None:
    text = AUDITOR_DENSITY_CYPHER
    assert "c.auditor_din" in text
    assert "c.state" in text
    assert "size(client_cins) >" in text
    assert "$threshold" in text


def test_cypher_filters_null_audit_dins() -> None:
    """The query must not group on NULL auditor DINs — otherwise every CIN
    without a registered auditor lands in one giant fake cluster."""
    assert "c.auditor_din IS NOT NULL" in AUDITOR_DENSITY_CYPHER
    assert "c.auditor_din <> ''" in AUDITOR_DENSITY_CYPHER


def test_cypher_returns_bounded_sample_cins() -> None:
    """Response payload must be bounded — slicing keeps results returnable."""
    assert "client_cins[0..200]" in AUDITOR_DENSITY_CYPHER


def test_cypher_orders_by_cluster_size_desc() -> None:
    assert "ORDER BY cluster_size DESC" in AUDITOR_DENSITY_CYPHER

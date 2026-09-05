"""Tests for Module 11 — Anomaly Detection (PRD §4.11 + §10 Day 15)."""

from __future__ import annotations

import numpy as np
import pytest

from backend.app.modules import m11_anomaly
from backend.app.modules.base import Severity
from backend.app.modules.m11_anomaly import (
    AnomalyInputs,
    FINANCIAL_FEATURE_NAMES,
    ISOFOREST_SCORE_THRESHOLD,
    LOF_N_NEIGHBORS,
    LOF_SCORE_THRESHOLD,
    financial_feature_row,
    graph_feature_row,
)
from backend.app.ingest.schemas import RawFinancialStatement
from ml.l05_graph_features import GraphFeatures


def test_constants_match_prd() -> None:
    assert ISOFOREST_SCORE_THRESHOLD == -0.10
    assert LOF_SCORE_THRESHOLD == -1.50
    assert LOF_N_NEIGHBORS == 20
    assert len(FINANCIAL_FEATURE_NAMES) == 20


def test_financial_feature_row_returns_twenty_values() -> None:
    fs = RawFinancialStatement(cin="U27101MH2010PTC215432", year=2023, revenue=1.0)
    row = financial_feature_row(fs)
    assert row.shape == (20,)


def test_graph_feature_row_returns_seven_values() -> None:
    feats = GraphFeatures(0.1, 0.2, 0.3, 4, 365.0, 5, 0.5)
    row = graph_feature_row(feats)
    assert row.shape == (7,)
    assert row[0] == 0.1  # pagerank preserved
    assert row[3] == 4.0  # director_count
    assert row[5] == 5.0  # degree


def test_module_abstains_when_background_too_small() -> None:
    inputs = AnomalyInputs(
        cin="U27101MH2010PTC215432",
        year=2023,
        financial_row=np.zeros(20),
        graph_row=np.zeros(7),
        background_financials=np.zeros((2, 20)),
        background_graph=np.zeros((2, 7)),
    )
    result = m11_anomaly.run(inputs)
    # Both detectors should abstain (background < min sizes) -> no signals.
    assert result.signals == []
    assert result.score == 0.0


def test_isolation_forest_fires_on_extreme_target() -> None:
    rng = np.random.default_rng(0)
    # Tightly clustered background; target far from cluster across every dim
    # (single-dim outliers aren't enough to push decision score < -0.10 in 20-D).
    background = rng.normal(loc=0.0, scale=0.1, size=(200, 20))
    target = np.full(20, 100.0)

    inputs = AnomalyInputs(
        cin="U27101MH2010PTC215432",
        year=2023,
        financial_row=target,
        graph_row=None,
        background_financials=background,
        background_graph=np.zeros((0, 7)),
    )
    result = m11_anomaly.run(inputs)
    types = {s.signal_type for s in result.signals}
    assert "ANOMALY_NOVEL_PATTERN_FINANCIAL" in types
    sig = next(s for s in result.signals if s.signal_type == "ANOMALY_NOVEL_PATTERN_FINANCIAL")
    assert sig.severity is Severity.HIGH


def test_isolation_forest_quiet_on_typical_target() -> None:
    rng = np.random.default_rng(0)
    background = rng.normal(loc=0.0, scale=1.0, size=(200, 20))
    target = rng.normal(loc=0.0, scale=1.0, size=20)  # drawn from same dist

    inputs = AnomalyInputs(
        cin="U27101MH2010PTC215432",
        year=2023,
        financial_row=target,
        graph_row=None,
        background_financials=background,
        background_graph=np.zeros((0, 7)),
    )
    result = m11_anomaly.run(inputs)
    # Not guaranteed quiet but on this seed it is — guard with a soft assertion.
    types = {s.signal_type for s in result.signals}
    assert "ANOMALY_NOVEL_PATTERN_FINANCIAL" not in types


def test_lof_fires_on_isolated_graph_target() -> None:
    rng = np.random.default_rng(0)
    background = rng.normal(loc=0.0, scale=0.05, size=(50, 7))
    target = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])  # far from cluster

    inputs = AnomalyInputs(
        cin="U27101MH2010PTC215432",
        year=2023,
        financial_row=None,
        graph_row=target,
        background_financials=np.zeros((0, 20)),
        background_graph=background,
    )
    result = m11_anomaly.run(inputs)
    types = {s.signal_type for s in result.signals}
    assert "ANOMALY_NOVEL_PATTERN_GRAPH" in types


def test_evidence_strings_cite_specific_score() -> None:
    rng = np.random.default_rng(0)
    background = rng.normal(loc=0.0, scale=0.1, size=(200, 20))
    target = np.full(20, 100.0)
    inputs = AnomalyInputs(
        cin="U27101MH2010PTC215432",
        year=2023,
        financial_row=target,
        graph_row=None,
        background_financials=background,
        background_graph=np.zeros((0, 7)),
    )
    result = m11_anomaly.run(inputs)
    sig = next(s for s in result.signals if s.signal_type == "ANOMALY_NOVEL_PATTERN_FINANCIAL")
    # PRD §4.2 specific-numbers rule
    assert "decision score" in sig.evidence_string
    assert "-0.10" in sig.evidence_string

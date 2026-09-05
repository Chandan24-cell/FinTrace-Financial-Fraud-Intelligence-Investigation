"""Tests for backend/app/scorer.py — RiskScorer orchestrator (PRD §7 + §10 Day 15)."""

from __future__ import annotations

import pytest

from backend.app.ingest.benchmarks import BSEFixtureBenchmark
from backend.app.ingest.nclt import NCLTFixtureSource
from backend.app.ingest.sources import FixtureSource
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource
from backend.app.scorer import (
    CRITICAL_FLAG_FLOOR_SCORE,
    ENSEMBLE_DISAGREEMENT_DELTA,
    NCLT_WD_FLOOR_SCORE,
    ScoringContext,
    score,
)
from backend.app.scorer import _ensemble_disagreement, _aggregate_score
from backend.app.modules.base import ModuleResult, Severity


@pytest.fixture
async def scoring_ctx() -> ScoringContext:
    return ScoringContext(
        benchmarks=await BSEFixtureBenchmark().fetch_all(),
        nclt=await NCLTFixtureSource().fetch_all(),
        wilful=await WilfulDefaulterFixtureSource().fetch_all(),
    )


def test_prd_floor_constants() -> None:
    assert CRITICAL_FLAG_FLOOR_SCORE == 60.0
    assert NCLT_WD_FLOOR_SCORE == 75.0
    assert ENSEMBLE_DISAGREEMENT_DELTA == 30.0


def test_ensemble_disagreement_fires_on_wide_split() -> None:
    assert _ensemble_disagreement([0.0, 0.0, 90.0, 10.0]) is True


def test_ensemble_disagreement_quiet_on_narrow_split() -> None:
    assert _ensemble_disagreement([20.0, 30.0, 25.0]) is False


def test_ensemble_disagreement_ignores_skipped_modules() -> None:
    # Two non-zero scores within 10 pts; the zero (skipped) is excluded.
    assert _ensemble_disagreement([0.0, 50.0, 60.0]) is False


def test_aggregate_score_rescales_partial_coverage() -> None:
    """Single module @100 should still produce a 100 even when others abstain."""
    results = [
        ModuleResult(module_name="m01_beneish", cin="X", year=2024, score=100.0),
        ModuleResult.skipped_for("m02_cross_statement", "X", 2024, "skip"),
    ]
    s = _aggregate_score(results)
    assert s == pytest.approx(100.0)


def test_aggregate_score_zero_when_all_skipped() -> None:
    results = [
        ModuleResult.skipped_for("m01_beneish", "X", 2024, "skip"),
        ModuleResult.skipped_for("m02_cross_statement", "X", 2024, "skip"),
    ]
    assert _aggregate_score(results) == 0.0


@pytest.mark.asyncio
async def test_ilfs_returns_critical_band_with_override(scoring_ctx) -> None:
    """PRD §10 Day-15 acceptance: '/analyse returns full dual-output payload on IL&FS.'"""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    report = await score(bundle, scoring_ctx)
    assert report.cin == "U45201MH2005PTC155294"
    assert report.risk_band == "CRITICAL"
    assert report.fraud_risk_score >= NCLT_WD_FLOOR_SCORE
    assert report.override_applied is True
    assert report.ensemble_disagreement_flag is True
    assert report.data_confidence > 0
    assert len(report.evidence_chain) >= 5


@pytest.mark.asyncio
async def test_dhfl_override_promotes_to_critical(scoring_ctx) -> None:
    """PRD §10 Day-15 acceptance: NCLT/WD override must fire on DHFL too."""
    bundle = await FixtureSource().fetch_bundle("L65910MH1984PLC032662")
    assert bundle is not None
    report = await score(bundle, scoring_ctx)
    assert report.fraud_risk_score >= NCLT_WD_FLOOR_SCORE
    assert report.risk_band == "CRITICAL"
    assert report.override_applied is True


@pytest.mark.asyncio
async def test_clean_company_stays_low(scoring_ctx) -> None:
    """A no-fraud-signal fixture must not be promoted by the orchestrator."""
    bundle = await FixtureSource().fetch_bundle("U14101MH2019PTC298765")
    assert bundle is not None
    report = await score(bundle, scoring_ctx)
    assert report.fraud_risk_score < CRITICAL_FLAG_FLOOR_SCORE
    assert report.override_applied is False
    assert report.risk_band in {"LOW", "MEDIUM"}


@pytest.mark.asyncio
async def test_payload_shape_matches_prd(scoring_ctx) -> None:
    """PRD §7.1 verbatim: every key required."""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    payload = (await score(bundle, scoring_ctx)).to_dict()
    for key in (
        "cin", "fraud_risk_score", "risk_band", "p_fraud_calibrated",
        "p_fraud_interval", "data_confidence", "ensemble_disagreement_flag",
        "evidence_chain", "module_breakdown", "override_applied",
    ):
        assert key in payload, f"missing PRD §7.1 key: {key}"
    # data_confidence + fraud_risk_score invariant (never one without the other)
    assert isinstance(payload["data_confidence"], int)
    assert isinstance(payload["fraud_risk_score"], float)


@pytest.mark.asyncio
async def test_evidence_chain_entries_serialise_with_provenance(scoring_ctx) -> None:
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    payload = (await score(bundle, scoring_ctx)).to_dict()
    assert payload["evidence_chain"], "IL&FS must produce at least one FraudSignal"
    sig = payload["evidence_chain"][0]
    for key in ("signal_type", "severity", "score_contribution",
                "evidence_string", "module_name", "triggered_by", "signal_id"):
        assert key in sig


@pytest.mark.asyncio
async def test_module_breakdown_lists_every_runnable_module(scoring_ctx) -> None:
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    report = await score(bundle, scoring_ctx)
    for mod in (
        "m01_beneish", "m02_cross_statement", "m05_peer_deviation",
        "m06_temporal", "m07_auditor_nlp", "m08_document_forensics",
        "m09_nclt_defaulter",
    ):
        assert mod in report.module_breakdown

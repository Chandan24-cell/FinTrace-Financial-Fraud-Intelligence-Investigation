"""Tests for Module 1 — Beneish M-Score (PRD §4.1 + §10 Day 8).

PRD acceptance: "IL&FS passes Beneish threshold."
"""

from __future__ import annotations

import pytest

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.ingest.sources import FixtureSource
from backend.app.modules import m01_beneish
from backend.app.modules.base import Severity


def _fs(**kwargs) -> RawFinancialStatement:
    """Helper: minimal RawFinancialStatement with overridable defaults."""
    base = {
        "cin": "U27101MH2010PTC215432",
        "year": 2023,
        "revenue": 1000.0,
        "cost_of_materials": 600.0,
        "employee_cost": 100.0,
        "finance_costs": 50.0,
        "depreciation": 50.0,
        "other_expenses": 100.0,
        "pbt": 100.0,
        "pat": 75.0,
        "total_assets": 2000.0,
        "fixed_assets": 800.0,
        "current_assets": 800.0,
        "cash_and_equivalents": 200.0,
        "receivables": 200.0,
        "long_term_borrowings": 600.0,
        "short_term_borrowings": 200.0,
        "cf_operating": 80.0,
    }
    base.update(kwargs)
    return RawFinancialStatement(**base)


@pytest.mark.asyncio
async def test_ilfs_passes_beneish_threshold() -> None:
    """PRD §10 Day 8 acceptance criterion: IL&FS FY17 vs FY16 -> M-Score > -1.78."""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    by_year = {f.year: f for f in bundle.financials}
    fy16, fy17 = by_year[2016], by_year[2017]

    score = m01_beneish.m_score(fy17, fy16)
    assert score > m01_beneish.M_SCORE_THRESHOLD, (
        f"IL&FS FY17 M-Score {score:.3f} should exceed PRD §4.1 threshold "
        f"{m01_beneish.M_SCORE_THRESHOLD}"
    )


@pytest.mark.asyncio
async def test_ilfs_fires_beneish_critical_signal() -> None:
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    by_year = {f.year: f for f in bundle.financials}
    result = m01_beneish.run(by_year[2017], by_year[2016])

    critical = [s for s in result.signals if s.severity == Severity.CRITICAL]
    assert critical, "Expected at least one CRITICAL signal on IL&FS FY17"
    assert any(s.signal_type == "BENEISH_M_SCORE_BREACH" for s in critical)


@pytest.mark.asyncio
async def test_ilfs_evidence_cites_specific_numbers() -> None:
    """PRD §6.2: 'evidence_string must always contain specific numbers.'"""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    by_year = {f.year: f for f in bundle.financials}
    result = m01_beneish.run(by_year[2017], by_year[2016])

    headline = next(s for s in result.signals if s.signal_type == "BENEISH_M_SCORE_BREACH")
    # The headline string must mention the actual M-Score and the years
    assert "M-Score" in headline.evidence_string
    assert "FY2017" in headline.evidence_string
    assert "FY2016" in headline.evidence_string
    # And specific numerical evidence (₹ formatted INR helper)
    assert "₹" in headline.evidence_string


def test_dsri_fires_when_receivables_jump() -> None:
    """DSRI > 1.31 — receivables growing much faster than revenue."""
    prev = _fs(year=2022, receivables=100.0, revenue=1000.0)
    curr = _fs(year=2023, receivables=300.0, revenue=1100.0)
    ratios = m01_beneish.ratios(curr, prev)
    # DSRI = (300/1100)/(100/1000) = 2.727 — well over 1.31
    assert ratios["DSRI"] > 1.31


def test_module_skips_on_single_year_data() -> None:
    """PRD §4.1: 'Skip (weight -> 0) with single-year data.'"""
    result = m01_beneish.run(_fs(year=2023), prev=None)
    assert result.skipped is True
    assert "two consecutive years" in result.skip_reason


def test_module_skips_on_non_consecutive_years() -> None:
    """Beneish requires year_t and year_{t-1} — gaps invalidate the comparison."""
    result = m01_beneish.run(_fs(year=2023), prev=_fs(year=2020))
    assert result.skipped is True
    assert "Non-consecutive" in result.skip_reason


def test_score_is_clamped_to_unit_range() -> None:
    """ModuleResult.score must be in [0, 100] per PRD §7 invariant."""
    prev = _fs(year=2022, receivables=100.0, revenue=1000.0)
    curr = _fs(year=2023, receivables=500.0, revenue=1100.0, cf_operating=-500.0)
    result = m01_beneish.run(curr, prev)
    assert 0.0 <= result.score <= 100.0


def test_healthy_fixture_does_not_breach_m_score() -> None:
    """ABC Steel Trading (Day-3 fixture) is the healthy peer; Beneish should NOT fire."""
    prev = _fs(year=2022, revenue=48.2e6, cost_of_materials=39.6e6, receivables=8.8e6,
               total_assets=22.4e6, fixed_assets=1.8e6, current_assets=19.2e6,
               long_term_borrowings=4.2e6, short_term_borrowings=6.8e6,
               depreciation=0.22e6, cf_operating=2.98e6, pat=2.81e6, pbt=3.75e6)
    curr = _fs(year=2023, revenue=56.4e6, cost_of_materials=46.2e6, receivables=10.1e6,
               total_assets=26.8e6, fixed_assets=2.4e6, current_assets=22.6e6,
               long_term_borrowings=4.8e6, short_term_borrowings=7.8e6,
               depreciation=0.28e6, cf_operating=3.54e6, pat=3.32e6, pbt=4.43e6)
    score = m01_beneish.m_score(curr, prev)
    assert score <= m01_beneish.M_SCORE_THRESHOLD, (
        f"Healthy peer score {score:.3f} should NOT exceed -1.78"
    )


def test_ratios_dict_has_all_eight_keys() -> None:
    prev = _fs(year=2022)
    curr = _fs(year=2023)
    r = m01_beneish.ratios(curr, prev)
    assert set(r.keys()) == {"DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI", "TATA"}


def test_threshold_constant_matches_prd() -> None:
    """PRD §4.1 verbatim: M-Score > -1.78 indicates manipulation."""
    assert m01_beneish.M_SCORE_THRESHOLD == -1.78

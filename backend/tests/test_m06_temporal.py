"""Tests for Module 6 — Temporal Signals (PRD §4.6 + §10 Day 10).

PRD Day-10 acceptance: 'Module 6 fires temporal signals on IL&FS.'
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.ingest.schemas import RawDirector, RawFinancialStatement
from backend.app.ingest.sources import FixtureSource
from backend.app.modules import m06_temporal
from backend.app.modules.base import Severity


def _fs(year: int, **overrides) -> RawFinancialStatement:
    base = {
        "cin": "U27101MH2010PTC215432",
        "year": year,
        "revenue": 1_000_000.0,
        "pbt": 100_000.0,
        "pat": 75_000.0,
        "long_term_borrowings": 500_000.0,
        "short_term_borrowings": 200_000.0,
        "auditor_name": "ABC & Co.",
        "auditor_type": "partnership",
    }
    base.update(overrides)
    return RawFinancialStatement(**base)


def _director(din: str, appt: date, ceased: date | None = None) -> RawDirector:
    return RawDirector(
        din=din,
        name=f"Director {din[-2:]}",
        appointment_date=appt,
        cessation_date=ceased,
        num_directorships=1,
    )


# --- T2 auditor change -------------------------------------------------------
def test_auditor_change_fires() -> None:
    fs_list = [
        _fs(2021, auditor_name="Old & Co."),
        _fs(2022, auditor_name="New & Co."),
    ]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    sig_types = {s.signal_type for s in result.signals}
    assert "TEMPORAL_AUDITOR_CHANGE" in sig_types


def test_auditor_change_does_not_fire_when_name_stable() -> None:
    fs_list = [_fs(2021), _fs(2022), _fs(2023)]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    sig_types = {s.signal_type for s in result.signals}
    assert "TEMPORAL_AUDITOR_CHANGE" not in sig_types


# --- T3 sole-prop auditor on large company -----------------------------------
def test_sole_prop_auditor_large_co_fires() -> None:
    fs_list = [_fs(2023, revenue=800_000_000.0, auditor_type="sole_proprietor")]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    sig = next(s for s in result.signals if s.signal_type == "TEMPORAL_SOLE_PROP_AUDITOR_LARGE_CO")
    assert sig.severity is Severity.MEDIUM
    assert "₹" in sig.evidence_string  # specific-numbers rule


def test_sole_prop_auditor_small_co_does_not_fire() -> None:
    fs_list = [_fs(2023, revenue=10_000_000.0, auditor_type="sole_proprietor")]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    assert all(s.signal_type != "TEMPORAL_SOLE_PROP_AUDITOR_LARGE_CO" for s in result.signals)


# --- T4 sudden recovery ------------------------------------------------------
def test_sudden_recovery_fires_after_loss() -> None:
    fs_list = [_fs(2022, pat=-1_000_000.0), _fs(2023, pat=5_000_000.0)]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    assert any(s.signal_type == "TEMPORAL_SUDDEN_RECOVERY" for s in result.signals)


def test_sudden_recovery_does_not_fire_on_small_swing() -> None:
    fs_list = [_fs(2022, pat=-1_000_000.0), _fs(2023, pat=500_000.0)]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    assert all(s.signal_type != "TEMPORAL_SUDDEN_RECOVERY" for s in result.signals)


# --- T6 round-trip borrowing -------------------------------------------------
def test_round_trip_borrowing_fires() -> None:
    fs_list = [
        _fs(2021, long_term_borrowings=100_000.0, short_term_borrowings=0.0),
        _fs(2022, long_term_borrowings=200_000.0, short_term_borrowings=0.0),
        _fs(2023, long_term_borrowings=100_000.0, short_term_borrowings=0.0),
    ]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    assert any(s.signal_type == "TEMPORAL_ROUND_TRIP_BORROWING" for s in result.signals)


def test_round_trip_does_not_fire_on_monotone_debt() -> None:
    fs_list = [
        _fs(2021, long_term_borrowings=100_000.0),
        _fs(2022, long_term_borrowings=150_000.0),
        _fs(2023, long_term_borrowings=200_000.0),
    ]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    assert all(s.signal_type != "TEMPORAL_ROUND_TRIP_BORROWING" for s in result.signals)


# --- T8 policy flag flip ----------------------------------------------------
def test_policy_change_post_bad_year_fires_critical() -> None:
    fs_list = [
        _fs(2017, going_concern_flag=True),
        _fs(2018, going_concern_flag=False, adverse_flag=True),
    ]
    result = m06_temporal.run(m06_temporal.TemporalInputs(financials=fs_list, directors=[]))
    sig = next(s for s in result.signals if s.signal_type == "TEMPORAL_POLICY_CHANGE_POST_BAD_YEAR")
    assert sig.severity is Severity.CRITICAL


# --- Module 6 on IL&FS fixture ----------------------------------------------
@pytest.mark.asyncio
async def test_ilfs_fixture_fires_at_least_one_temporal_signal() -> None:
    """PRD §10 Day-10 acceptance: 'Module 6 fires temporal signals on IL&FS.'"""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    result = m06_temporal.run(m06_temporal.TemporalInputs(
        financials=list(bundle.financials),
        directors=list(bundle.directors),
    ))
    assert len(result.signals) >= 1, "IL&FS must fire at least one Module 6 signal"
    assert result.score > 0


def test_module_thresholds_match_prd() -> None:
    assert m06_temporal.DIRECTOR_CHANGE_WINDOW_DAYS == 90
    assert m06_temporal.LARGE_COMPANY_REVENUE == 500_000_000.0
    assert m06_temporal.SUDDEN_RECOVERY_MULTIPLE == 3.0
    assert m06_temporal.ROUND_TRIP_BORROWING_PCT == 25.0

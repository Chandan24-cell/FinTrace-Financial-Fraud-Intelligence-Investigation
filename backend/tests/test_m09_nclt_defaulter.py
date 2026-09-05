"""Tests for Module 9 — NCLT / DRT / Wilful Defaulter (PRD §4.9 + §7.3 + §10 Day 12)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.ingest.nclt import NCLTFixtureSource, RawNCLTProceeding
from backend.app.ingest.wilful_defaulter import RawWilfulDefaulter, WilfulDefaulterFixtureSource
from backend.app.modules import m09_nclt_defaulter as m09
from backend.app.modules.base import Severity
from backend.app.modules.m09_nclt_defaulter import (
    NCLT_WD_FLOOR_SCORE,
    NCLTDefaulterInputs,
    apply_override,
)


def _nclt(cin: str, **overrides) -> RawNCLTProceeding:
    base = {
        "case_number": "C.P. (IB) 1/MB/2020",
        "cin": cin,
        "petition_type": "CIRP",
        "filing_date": date(2020, 1, 1),
        "status": "admitted",
        "amount_claimed": 1_000_000_000.0,
        "bench": "NCLT Mumbai",
    }
    base.update(overrides)
    return RawNCLTProceeding.model_validate(base)


def _wd(cin: str, **overrides) -> RawWilfulDefaulter:
    base = {
        "cin": cin,
        "din": "00554433",
        "bank_name": "Test Bank",
        "amount": 500_000_000.0,
        "declared_date": date(2020, 3, 1),
        "source": "RBI",
    }
    base.update(overrides)
    return RawWilfulDefaulter.model_validate(base)


def test_admitted_cirp_fires_critical() -> None:
    cin = "U27101MH2010PTC215432"
    res = m09.run(NCLTDefaulterInputs(cin=cin, nclt_proceedings=[_nclt(cin)], wilful_declarations=[]))
    sig = next(s for s in res.signals if s.signal_type == "NCLT_PROCEEDING_MATCH")
    assert sig.severity is Severity.CRITICAL
    assert "Admitted CIRP" in sig.evidence_string


def test_drt_proceeding_fires_critical() -> None:
    cin = "U27101MH2010PTC215432"
    p = _nclt(cin, petition_type="DRT", status="filed")
    res = m09.run(NCLTDefaulterInputs(cin=cin, nclt_proceedings=[p], wilful_declarations=[]))
    sig = next(s for s in res.signals if s.signal_type == "DRT_PROCEEDING_MATCH")
    assert sig.severity is Severity.CRITICAL


def test_wilful_defaulter_fires_critical() -> None:
    cin = "U27101MH2010PTC215432"
    res = m09.run(NCLTDefaulterInputs(cin=cin, nclt_proceedings=[], wilful_declarations=[_wd(cin)]))
    sig = next(s for s in res.signals if s.signal_type == "WILFUL_DEFAULTER_MATCH")
    assert sig.severity is Severity.CRITICAL
    assert "Test Bank" in sig.evidence_string


def test_no_match_for_unrelated_cin() -> None:
    cin = "U27101MH2010PTC215432"
    other = "U45201MH2005PTC155294"
    res = m09.run(NCLTDefaulterInputs(cin=cin, nclt_proceedings=[_nclt(other)], wilful_declarations=[_wd(other)]))
    assert res.signals == []
    assert res.score == 0.0


def test_override_promotes_low_score_to_floor() -> None:
    cin = "U27101MH2010PTC215432"
    res = m09.run(NCLTDefaulterInputs(cin=cin, nclt_proceedings=[_nclt(cin)], wilful_declarations=[]))
    final, applied, _matched_ids = apply_override(score=10.0, signals=res.signals)
    assert applied is True
    assert final == NCLT_WD_FLOOR_SCORE


def test_override_leaves_higher_score_alone() -> None:
    cin = "U27101MH2010PTC215432"
    res = m09.run(NCLTDefaulterInputs(cin=cin, nclt_proceedings=[_nclt(cin)], wilful_declarations=[]))
    final, applied, _matched_ids = apply_override(score=92.0, signals=res.signals)
    assert applied is True
    assert final == 92.0


def test_override_noop_when_no_match() -> None:
    final, applied, _matched_ids = apply_override(score=20.0, signals=[])
    assert applied is False
    assert final == 20.0


@pytest.mark.asyncio
async def test_ilfs_override_fires_on_real_seeds() -> None:
    """PRD §10 Day-12 acceptance: 'Override rules fire on IL&FS + DHFL.'"""
    proc = await NCLTFixtureSource().fetch_all()
    wds = await WilfulDefaulterFixtureSource().fetch_all()
    res = m09.run(NCLTDefaulterInputs(
        cin="U45201MH2005PTC155294", nclt_proceedings=proc, wilful_declarations=wds,
    ))
    final, applied, _matched_ids = apply_override(score=15.0, signals=res.signals)
    assert applied is True
    assert final >= NCLT_WD_FLOOR_SCORE
    sig_types = {s.signal_type for s in res.signals}
    assert "NCLT_PROCEEDING_MATCH" in sig_types
    assert "WILFUL_DEFAULTER_MATCH" in sig_types


@pytest.mark.asyncio
async def test_dhfl_override_fires_on_real_seeds() -> None:
    """PRD §10 Day-12 acceptance: 'Override rules fire on IL&FS + DHFL.'"""
    proc = await NCLTFixtureSource().fetch_all()
    wds = await WilfulDefaulterFixtureSource().fetch_all()
    res = m09.run(NCLTDefaulterInputs(
        cin="L65910MH1984PLC032662", nclt_proceedings=proc, wilful_declarations=wds,
    ))
    final, applied, _matched_ids = apply_override(score=15.0, signals=res.signals)
    assert applied is True
    assert final >= NCLT_WD_FLOOR_SCORE
    sig_types = {s.signal_type for s in res.signals}
    assert "NCLT_PROCEEDING_MATCH" in sig_types
    assert "WILFUL_DEFAULTER_MATCH" in sig_types


def test_floor_constant_matches_prd() -> None:
    assert NCLT_WD_FLOOR_SCORE == 75.0

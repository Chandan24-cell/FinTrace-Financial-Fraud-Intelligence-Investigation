"""Tests for Module 7 — Auditor NLP (PRD §4.7 + §10 Day 11)."""

from __future__ import annotations

import pytest

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.ingest.sources import FixtureSource
from backend.app.modules import m07_auditor_nlp as m07
from backend.app.modules.base import Severity
from backend.app.modules.m07_auditor_nlp import OpinionCategory


def _fs(year: int, **overrides) -> RawFinancialStatement:
    base = {
        "cin": "U27101MH2010PTC215432",
        "year": year,
        "revenue": 1_000_000.0,
    }
    base.update(overrides)
    return RawFinancialStatement(**base)


def test_classify_clean_when_no_keywords_and_no_flags() -> None:
    a = m07.classify_opinion(_fs(2023, audit_opinion_text="In our opinion, the financial statements give a true and fair view."))
    assert a.category is OpinionCategory.CLEAN


def test_classify_emphasis_of_matter() -> None:
    a = m07.classify_opinion(_fs(2023, audit_opinion_text="We draw attention to Note 28 regarding the carrying value of investments."))
    assert a.category is OpinionCategory.EMPHASIS


def test_classify_going_concern_via_text() -> None:
    a = m07.classify_opinion(_fs(2023, audit_opinion_text="Material uncertainty exists regarding the ability to continue as a going concern."))
    assert a.category is OpinionCategory.GOING_CONCERN


def test_classify_going_concern_via_flag_only() -> None:
    a = m07.classify_opinion(_fs(2023, going_concern_flag=True))
    assert a.category is OpinionCategory.GOING_CONCERN
    assert a.matched_phrase == "going_concern_flag=True"


def test_classify_qualified() -> None:
    a = m07.classify_opinion(_fs(2023, audit_opinion_text="Except for the matter described above, the statements present fairly."))
    assert a.category is OpinionCategory.QUALIFIED


def test_classify_adverse_via_flag_overrides_text() -> None:
    a = m07.classify_opinion(_fs(
        2023,
        audit_opinion_text="In our opinion, the statements give a true and fair view.",
        adverse_flag=True,
    ))
    assert a.category is OpinionCategory.ADVERSE


def test_classify_disclaimer_of_opinion() -> None:
    a = m07.classify_opinion(_fs(2023, audit_opinion_text="Disclaimer of opinion. We were unable to obtain sufficient appropriate audit evidence."))
    assert a.category is OpinionCategory.DISCLAIMER


def test_run_returns_per_year_signals_and_skips_clean() -> None:
    fs_list = [
        _fs(2021),  # clean
        _fs(2022, audit_opinion_text="We draw attention to Note 28."),  # emphasis
        _fs(2023, going_concern_flag=True),  # going concern
    ]
    result = m07.run(fs_list)
    sig_types = [s.signal_type for s in result.signals]
    assert "AUDITOR_OPINION_EMPHASIS" in sig_types
    assert "AUDITOR_OPINION_GOING_CONCERN" in sig_types
    # Escalation from clean -> emphasis and emphasis -> going_concern -> two escalations
    escalations = [s for s in result.signals if s.signal_type == "AUDITOR_OPINION_ESCALATION"]
    assert len(escalations) >= 1


def test_severity_ladder() -> None:
    assert m07._severity_for(OpinionCategory.EMPHASIS) is Severity.MEDIUM
    assert m07._severity_for(OpinionCategory.GOING_CONCERN) is Severity.HIGH
    assert m07._severity_for(OpinionCategory.QUALIFIED) is Severity.HIGH
    assert m07._severity_for(OpinionCategory.ADVERSE) is Severity.CRITICAL
    assert m07._severity_for(OpinionCategory.DISCLAIMER) is Severity.CRITICAL


@pytest.mark.asyncio
async def test_ilfs_progression_fires_escalation() -> None:
    """PRD §10 Day-11 acceptance: 'Auditor NLP module classifies opinion text
    on all 10 fixtures.' IL&FS has a CLEAN→EMPHASIS→GOING_CONCERN→DISCLAIMER
    progression — must surface an escalation signal."""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    result = m07.run(list(bundle.financials))
    sig_types = [s.signal_type for s in result.signals]
    assert "AUDITOR_OPINION_DISCLAIMER" in sig_types
    assert any(s.startswith("AUDITOR_OPINION_") and s.endswith("ESCALATION") for s in sig_types)


@pytest.mark.asyncio
async def test_all_fixtures_classify_without_error() -> None:
    """PRD §10 Day-11 acceptance: classify on all 10 fixtures without throwing."""
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    assert len(cins) >= 1
    classified = 0
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if bundle is None or not bundle.financials:
            continue
        result = m07.run(list(bundle.financials))
        assert result.module_name == "m07_auditor_nlp"
        classified += 1
    assert classified >= 9  # 10 fixtures; allow one with empty financials


def test_evidence_string_cites_year_phrase_and_emphasis_count() -> None:
    fs_list = [_fs(2023, audit_opinion_text="Disclaimer of opinion.", emphasis_count=7)]
    result = m07.run(fs_list)
    sig = next(s for s in result.signals if s.signal_type == "AUDITOR_OPINION_DISCLAIMER")
    assert "FY2023" in sig.evidence_string
    assert "disclaimer" in sig.evidence_string.lower()
    assert "emphasis_count=7" in sig.evidence_string

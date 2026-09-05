"""Tests for Module 8 — Document Forensics (PRD §4.8 + §10 Day 11)."""

from __future__ import annotations

import pytest

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.ingest.sources import FixtureSource
from backend.app.modules import m08_document_forensics as m08
from backend.app.modules.base import Severity
from backend.app.modules.m08_document_forensics import ForensicInputs


def _fs(year: int, **overrides) -> RawFinancialStatement:
    base = {
        "cin": "U27101MH2010PTC215432",
        "year": year,
        "revenue": 1_000_000.0,
    }
    base.update(overrides)
    return RawFinancialStatement(**base)


def test_suspicious_software_check_recognises_word() -> None:
    assert m08._is_suspicious_software("Microsoft Word") is True
    assert m08._is_suspicious_software("WPS Office") is True
    assert m08._is_suspicious_software("LibreOffice") is True
    assert m08._is_suspicious_software("Adobe LiveCycle Designer") is False
    assert m08._is_suspicious_software(None) is False


def test_suspicious_software_fires_high() -> None:
    fs = _fs(2023, pdf_creation_software="Microsoft Word")
    result = m08.run([ForensicInputs(fs=fs)])
    sig = next(s for s in result.signals if s.signal_type == "DOC_FORENSICS_SUSPICIOUS_CREATOR")
    assert sig.severity is Severity.HIGH
    assert "Microsoft Word" in sig.evidence_string


def test_metadata_gap_fires_via_flag() -> None:
    fs = _fs(2023, pdf_metadata_anomaly=True)
    result = m08.run([ForensicInputs(fs=fs)])
    sig = next(s for s in result.signals if s.signal_type == "DOC_FORENSICS_METADATA_GAP")
    assert sig.severity is Severity.HIGH


def test_metadata_gap_fires_via_computed_days() -> None:
    fs = _fs(2023)
    result = m08.run([ForensicInputs(fs=fs, creation_mod_gap_days=1)])
    assert any(s.signal_type == "DOC_FORENSICS_METADATA_GAP" for s in result.signals)


def test_metadata_gap_does_not_fire_when_gap_above_floor() -> None:
    fs = _fs(2023)
    result = m08.run([ForensicInputs(fs=fs, creation_mod_gap_days=14)])
    assert all(s.signal_type != "DOC_FORENSICS_METADATA_GAP" for s in result.signals)


def test_font_count_fires_when_above_threshold() -> None:
    fs = _fs(2023)
    result = m08.run([ForensicInputs(fs=fs, distinct_fonts=12)])
    sig = next(s for s in result.signals if s.signal_type == "DOC_FORENSICS_FONT_COUNT_HIGH")
    assert sig.severity is Severity.MEDIUM
    assert "12" in sig.evidence_string


def test_dpi_inconsistency_fires_critical() -> None:
    fs = _fs(2023)
    result = m08.run([ForensicInputs(fs=fs, dpi_inconsistency=True)])
    sig = next(s for s in result.signals if s.signal_type == "DOC_FORENSICS_DPI_INCONSISTENT")
    assert sig.severity is Severity.CRITICAL


def test_clean_fs_produces_zero_signals() -> None:
    fs = _fs(2023, pdf_creation_software="Adobe LiveCycle Designer")
    result = m08.run([ForensicInputs(fs=fs, distinct_fonts=5, dpi_inconsistency=False)])
    assert result.signals == []
    assert result.score == 0.0


def test_run_for_fs_list_thin_wrapper() -> None:
    fs = _fs(2023, pdf_metadata_anomaly=True)
    result = m08.run_for_fs_list([fs])
    assert any(s.signal_type == "DOC_FORENSICS_METADATA_GAP" for s in result.signals)


@pytest.mark.asyncio
async def test_hij_auto_parts_fixture_fires_anomaly_flag() -> None:
    """PRD §10 Day-11 acceptance: 'Document forensics fires anomaly flag.'

    HIJ Auto Parts FY2023 carries pdf_creation_software='Microsoft Word' +
    pdf_metadata_anomaly=True — both PRD §4.8 markers should fire."""
    bundle = await FixtureSource().fetch_bundle("U29304MH2019PTC287654")
    assert bundle is not None
    result = m08.run_for_fs_list(list(bundle.financials))
    sig_types = [s.signal_type for s in result.signals]
    assert "DOC_FORENSICS_SUSPICIOUS_CREATOR" in sig_types
    assert "DOC_FORENSICS_METADATA_GAP" in sig_types
    assert result.score > 0


def test_module_thresholds_match_prd() -> None:
    assert m08.FONT_COUNT_RED_FLAG == 8
    assert m08.CREATION_MOD_GAP_FLOOR_DAYS == 3
    assert "microsoft word" in m08.SUSPICIOUS_CREATION_SOFTWARE

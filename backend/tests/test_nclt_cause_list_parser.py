"""Tests for the NCLT cause-list HTML parser (PRD §10 Day 14 hardening)."""

from __future__ import annotations

import pytest

from backend.app.ingest.nclt import (
    NCLTScraper,
    _normalize_petition,
    _normalize_status,
    parse_cause_list_html,
)


_SAMPLE_HTML = """\
<html><body>
<h2>Cause List</h2>
<table>
  <tr><th>Case No.</th><th>CIN</th><th>Petition</th>
      <th>Filing Date</th><th>Status</th><th>Amount Claimed</th></tr>
  <tr><td>C.P. (IB) 100/MB/2026</td><td>U45201MH2005PTC155294</td>
      <td>IBC — Section 7</td><td>2026-02-10</td><td>Admitted</td>
      <td>Rs 12,40,00,000</td></tr>
  <tr><td>C.P. 250/MB/2026</td><td>U27101MH2010PTC215432</td>
      <td>Winding Up</td><td>15-02-2026</td><td>In Progress</td>
      <td>Rs 2,75,00,000</td></tr>
</table>
</body></html>
"""


def test_parses_two_rows_from_well_formed_html() -> None:
    rows = parse_cause_list_html(_SAMPLE_HTML, bench="NCLT Mumbai")
    assert len(rows) == 2
    assert [r.case_number for r in rows] == [
        "C.P. (IB) 100/MB/2026",
        "C.P. 250/MB/2026",
    ]
    assert rows[0].cin == "U45201MH2005PTC155294"
    assert rows[0].bench == "NCLT Mumbai"


def test_petition_section_seven_maps_to_cirp() -> None:
    rows = parse_cause_list_html(_SAMPLE_HTML)
    assert rows[0].petition_type == "CIRP"
    assert rows[1].petition_type == "winding_up"


def test_status_admitted_in_progress_normalized() -> None:
    rows = parse_cause_list_html(_SAMPLE_HTML)
    assert rows[0].status == "admitted"
    assert rows[1].status == "in_progress"


def test_amount_strips_currency_and_commas() -> None:
    rows = parse_cause_list_html(_SAMPLE_HTML)
    # "Rs 12,40,00,000" -> 124000000
    assert rows[0].amount_claimed == 124000000.0
    assert rows[1].amount_claimed == 27500000.0


def test_filing_dates_in_both_formats() -> None:
    rows = parse_cause_list_html(_SAMPLE_HTML)
    assert rows[0].filing_date.isoformat() == "2026-02-10"
    assert rows[1].filing_date.isoformat() == "2026-02-15"


def test_row_without_valid_cin_is_dropped() -> None:
    html = """\
<table>
  <tr><th>Case</th><th>CIN</th><th>Section</th></tr>
  <tr><td>C.P. 1/MB/2026</td><td>NOT-A-CIN</td><td>IBC</td></tr>
</table>
"""
    assert parse_cause_list_html(html) == []


def test_empty_html_yields_no_rows() -> None:
    assert parse_cause_list_html("") == []
    assert parse_cause_list_html("<html></html>") == []


def test_normalize_petition_handles_known_synonyms() -> None:
    assert _normalize_petition("IBC — Section 7") == "CIRP"
    assert _normalize_petition("Winding-Up") == "winding_up"
    assert _normalize_petition("Debt Recovery") == "DRT"
    assert _normalize_petition("Unknown") == "other"


def test_normalize_status_handles_synonyms() -> None:
    assert _normalize_status("Admitted") == "admitted"
    assert _normalize_status("Pending") == "in_progress"
    assert _normalize_status("Disposed off") == "disposed"
    assert _normalize_status("") == "filed"


def test_scraper_fetch_html_raises_without_fetcher() -> None:
    s = NCLTScraper()
    with pytest.raises(NotImplementedError):
        # awaitable raises immediately on call() because it isn't awaited.
        import asyncio
        asyncio.run(s.fetch_html("https://example"))

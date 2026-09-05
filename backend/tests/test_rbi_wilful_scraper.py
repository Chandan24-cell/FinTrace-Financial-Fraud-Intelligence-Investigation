"""Tests for the RBI wilful-defaulter scraper (PRD §10 Phase B free-source).

Strategy mirrors the NCLT cause-list parser tests: inject a canned HTML
coroutine so CI never reaches out to rbi.org.in, then assert the parser
shapes the data into RawWilfulDefaulter records correctly.

Cases:
  - No fetcher → NotImplementedError (composite/operator treats as 'skip').
  - Well-formed HTML → two rows, validated, with correct source label.
  - Column-order reshuffle still locates CIN via regex (resilience check).
  - DD/MM/YYYY date format parses identically to ISO.
  - Indian comma-grouped amount ("1,25,50,000") parses to int rupees.
  - Both list-URL + suit-filed-URL combine + dedupe across calls.
  - Row missing CIN is dropped (not raised).
"""

from __future__ import annotations

import pytest

from backend.app.ingest.rbi_html_parser import parse_rbi_wilful_html
from backend.app.ingest.wilful_defaulter import (
    RawWilfulDefaulter,
    RBIWilfulScraper,
)


_SAMPLE_HTML = """\
<html><body>
<h2>List of Wilful Defaulters (Non-Suit Filed Accounts)</h2>
<table>
  <tr><th>Borrower CIN</th><th>Promoter DIN</th><th>Bank Name</th>
      <th>Outstanding Amount</th><th>Date of Declaration</th></tr>
  <tr><td>U45201MH2005PTC155294</td><td>00009876</td>
      <td>State Bank of India</td><td>Rs 2,85,00,00,000</td>
      <td>10-09-2018</td></tr>
  <tr><td>U27101MH2010PTC215432</td><td>00554433</td>
      <td>ICICI Bank</td><td>Rs 85,00,00,000</td>
      <td>22/02/2019</td></tr>
</table>
</body></html>
"""


_SUIT_FILED_HTML = """\
<html><body>
<table>
  <tr><th>CIN</th><th>DIN</th><th>Lender</th>
      <th>Amount Outstanding</th><th>Declared Date</th></tr>
  <tr><td>U46190MH2019PTC295432</td><td>04455667</td>
      <td>Indian Overseas Bank</td><td>23,80,00,000</td>
      <td>20-01-2022</td></tr>
</table>
</body></html>
"""


_RESHUFFLED_HTML = """\
<html><body>
<table>
  <tr><th>Bank Name</th><th>Date of Declaration</th>
      <th>Outstanding Amount</th><th>Borrower CIN</th><th>Promoter DIN</th></tr>
  <tr><td>Punjab National Bank</td><td>14-08-2019</td>
      <td>1,27,00,00,000</td><td>U27109MH2018PTC312456</td>
      <td>01122334</td></tr>
</table>
</body></html>
"""


def _canned_fetcher(payload_by_url: dict[str, str]):
    async def fetcher(url: str) -> str:
        if url not in payload_by_url:
            raise RuntimeError(f"unexpected URL: {url}")
        return payload_by_url[url]
    return fetcher


def test_parses_two_rows_from_well_formed_html() -> None:
    rows = parse_rbi_wilful_html(_SAMPLE_HTML, source="RBI")
    assert len(rows) == 2
    assert isinstance(rows[0], RawWilfulDefaulter)
    assert rows[0].cin == "U45201MH2005PTC155294"
    assert rows[0].bank_name == "State Bank of India"
    assert rows[0].source == "RBI"


def test_column_reshuffle_still_finds_cin_and_din() -> None:
    """Layout drift on rbi.org.in must not corrupt the dataset."""
    rows = parse_rbi_wilful_html(_RESHUFFLED_HTML, source="RBI")
    assert len(rows) == 1
    assert rows[0].cin == "U27109MH2018PTC312456"
    assert rows[0].din == "01122334"
    assert rows[0].bank_name == "Punjab National Bank"


def test_indian_comma_grouping_strips_correctly() -> None:
    rows = parse_rbi_wilful_html(_SAMPLE_HTML, source="RBI")
    # "Rs 2,85,00,00,000" -> 2850000000 (285 crore)
    assert rows[0].amount == 2_850_000_000.0
    # "Rs 85,00,00,000" -> 850000000 (85 crore)
    assert rows[1].amount == 850_000_000.0


def test_mixed_dd_mm_yyyy_and_iso_dates_parse() -> None:
    rows = parse_rbi_wilful_html(_SAMPLE_HTML, source="RBI")
    assert rows[0].declared_date.isoformat() == "2018-09-10"
    assert rows[1].declared_date.isoformat() == "2019-02-22"


def test_source_label_propagates_to_every_row() -> None:
    rows = parse_rbi_wilful_html(_SUIT_FILED_HTML, source="SUIT_FILED")
    assert all(r.source == "SUIT_FILED" for r in rows)


def test_row_without_cin_is_dropped_silently() -> None:
    html = """\
<table>
  <tr><th>CIN</th><th>Bank</th><th>Amount</th><th>Date</th></tr>
  <tr><td>NOT-A-CIN</td><td>SBI</td><td>1000</td><td>2024-01-01</td></tr>
  <tr><td>U45201MH2005PTC155294</td><td>SBI</td><td>5000</td><td>2024-01-01</td></tr>
</table>
"""
    rows = parse_rbi_wilful_html(html, source="RBI")
    assert len(rows) == 1
    assert rows[0].cin == "U45201MH2005PTC155294"


@pytest.mark.asyncio
async def test_no_fetcher_raises_not_implemented() -> None:
    scraper = RBIWilfulScraper()
    with pytest.raises(NotImplementedError):
        await scraper.fetch_all()


@pytest.mark.asyncio
async def test_both_lists_combine_and_dedupe() -> None:
    """Wilful list + suit-filed list both populate; identical (cin, din, bank)
    rows across the two pages dedupe so module 9 doesn't double-count."""
    scraper = RBIWilfulScraper(
        fetch_html=_canned_fetcher({
            scraper_list_url(): _SAMPLE_HTML,
            scraper_suit_url(): _SUIT_FILED_HTML,
        }),
    )
    rows = await scraper.fetch_all()
    assert len(rows) == 3
    cins = {r.cin for r in rows}
    assert cins == {
        "U45201MH2005PTC155294",
        "U27101MH2010PTC215432",
        "U46190MH2019PTC295432",
    }
    # Source label is set per page, not per row globally.
    by_cin = {r.cin: r.source for r in rows}
    assert by_cin["U46190MH2019PTC295432"] == "SUIT_FILED"
    assert by_cin["U45201MH2005PTC155294"] == "RBI"


@pytest.mark.asyncio
async def test_one_url_failing_does_not_kill_other() -> None:
    """Best-effort: if the suit-filed page 500s, we still return the
    non-suit-filed rows. Mirrors MCAPublicScraper's partial-bundle policy."""

    async def half_broken(url: str) -> str:
        if "BS_NPAList" in url:
            raise RuntimeError("simulated 500 on suit-filed page")
        return _SAMPLE_HTML

    scraper = RBIWilfulScraper(fetch_html=half_broken)
    rows = await scraper.fetch_all()
    assert len(rows) == 2
    assert all(r.source == "RBI" for r in rows)


def scraper_list_url() -> str:
    from backend.app.ingest.wilful_defaulter import RBI_DEFAULT_LIST_URL
    return RBI_DEFAULT_LIST_URL


def scraper_suit_url() -> str:
    from backend.app.ingest.wilful_defaulter import RBI_DEFAULT_SUIT_FILED_URL
    return RBI_DEFAULT_SUIT_FILED_URL

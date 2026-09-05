"""NCLT proceedings source (PRD §4.9 + §10 Day 5).

NCLT publishes daily cause lists at https://nclt.gov.in. We scrape company-as-
respondent rows and write NCLTProceeding nodes. Module 9 raises CRITICAL (force
score >= 75) when a Company has an admitted CIRP proceeding.

Two implementations:
  NCLTFixtureSource  — reads infra/seeds/nclt/*.json. Default working path.
  NCLTScraper        — BeautifulSoup scraper for the cause-list HTML; Day 7+ task.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


SEED_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds" / "nclt"

_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")


class RawNCLTProceeding(BaseModel):
    """One NCLT proceeding. PRD §3 keys: case_number, cin, petition_type, ..."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    case_number: str
    cin: str
    petition_type: Literal["CIRP", "winding_up", "DRT", "other"] = "other"
    filing_date: date
    status: Literal["filed", "admitted", "withdrawn", "disposed", "in_progress"] = "filed"
    amount_claimed: float = Field(default=0.0, ge=0.0)
    bench: str | None = None  # e.g. "NCLT Mumbai"

    @field_validator("cin")
    @classmethod
    def _validate_cin(cls, v: str) -> str:
        if not _CIN_RE.match(v):
            raise ValueError(f"Invalid CIN: {v!r}")
        return v


class NCLTSource(Protocol):
    name: str

    async def fetch_all(self) -> list[RawNCLTProceeding]: ...

    async def fetch_for_cin(self, cin: str) -> list[RawNCLTProceeding]: ...


class NCLTFixtureSource:
    """Reads NCLT proceedings from infra/seeds/nclt/*.json."""

    name = "nclt_fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SEED_ROOT

    async def fetch_all(self) -> list[RawNCLTProceeding]:
        if not self.root.exists():
            return []
        proceedings: list[RawNCLTProceeding] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for row in raw:
                proceedings.append(RawNCLTProceeding.model_validate(row))
        return proceedings

    async def fetch_for_cin(self, cin: str) -> list[RawNCLTProceeding]:
        return [p for p in await self.fetch_all() if p.cin == cin]


class _CauseListTableExtractor(HTMLParser):
    """Pulls rows out of nclt.gov.in cause-list HTML tables.

    The pages publish one <table> per bench with a recognisable header row —
    e.g. ['Case No.', 'Petitioner', 'Respondent', 'Status', 'Bench']. We
    stream-parse the document with the stdlib `html.parser` (no new dep
    required) and collect every (header, row[]) pair we see. Callers map the
    extracted columns onto RawNCLTProceeding fields.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._row_buf: list[str] = []
        self._table_buf: list[list[str]] = []
        self.tables: list[list[list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._table_buf = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row_buf = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._row_buf.append("".join(self._cell_buf).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row_buf:
                self._table_buf.append(self._row_buf)
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._table_buf:
                self.tables.append(self._table_buf)
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)


# Cause-list status strings -> RawNCLTProceeding.status literal.
_STATUS_NORMALIZE: dict[str, str] = {
    "filed": "filed",
    "registered": "filed",
    "admitted": "admitted",
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "in_progress": "in_progress",
    "pending": "in_progress",
    "withdrawn": "withdrawn",
    "disposed": "disposed",
    "disposed off": "disposed",
    "disposed of": "disposed",
}

# Cause-list petition strings -> RawNCLTProceeding.petition_type literal.
_PETITION_NORMALIZE: dict[str, str] = {
    "cirp": "CIRP",
    "ibc": "CIRP",
    "ibc — section 7": "CIRP",
    "section 7": "CIRP",
    "section 9": "CIRP",
    "winding up": "winding_up",
    "winding-up": "winding_up",
    "winding_up": "winding_up",
    "drt": "DRT",
    "debt recovery": "DRT",
}

_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y")
_AMOUNT_CLEAN_RE = re.compile(r"[^\d.]")


def _normalize_petition(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "other"
    for key, val in _PETITION_NORMALIZE.items():
        if key in s:
            return val
    return "other"


def _normalize_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    for key, val in _STATUS_NORMALIZE.items():
        if key in s:
            return val
    return "filed"


def _parse_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    from datetime import datetime
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> float:
    if not raw:
        return 0.0
    cleaned = _AMOUNT_CLEAN_RE.sub("", raw)
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_cause_list_html(
    html: str, *, bench: str | None = None,
) -> list[RawNCLTProceeding]:
    """Stream-parse one cause-list HTML page into RawNCLTProceeding rows.

    Tolerant of column-order variations: we locate the CIN column via the
    `_CIN_RE` regex on cell contents rather than positional indexing.
    """
    parser = _CauseListTableExtractor()
    parser.feed(html)
    parser.close()

    proceedings: list[RawNCLTProceeding] = []
    for table in parser.tables:
        if not table or len(table) < 2:
            continue
        header = [c.strip().lower() for c in table[0]]
        # Map header tokens to column indices.
        idx_case = next((i for i, h in enumerate(header) if "case" in h), 0)
        idx_petition = next((i for i, h in enumerate(header) if "petition" in h or "section" in h), -1)
        idx_status = next((i for i, h in enumerate(header) if "status" in h or "stage" in h), -1)
        idx_filing = next((i for i, h in enumerate(header) if "filing" in h or "date" in h), -1)
        idx_amount = next((i for i, h in enumerate(header) if "amount" in h or "claim" in h), -1)

        for row in table[1:]:
            if not row:
                continue
            case_number = row[idx_case].strip() if idx_case < len(row) else ""
            # Locate the CIN cell anywhere in the row.
            cin = next((cell.strip() for cell in row if _CIN_RE.match(cell.strip())), "")
            if not case_number or not cin:
                continue
            filing_str = row[idx_filing] if 0 <= idx_filing < len(row) else ""
            filing_date = _parse_date(filing_str) or date.today()
            petition_str = row[idx_petition] if 0 <= idx_petition < len(row) else ""
            status_str = row[idx_status] if 0 <= idx_status < len(row) else ""
            amount = _parse_amount(row[idx_amount]) if 0 <= idx_amount < len(row) else 0.0

            try:
                proceedings.append(RawNCLTProceeding(
                    case_number=case_number,
                    cin=cin,
                    petition_type=_normalize_petition(petition_str),
                    filing_date=filing_date,
                    status=_normalize_status(status_str),
                    amount_claimed=max(amount, 0.0),
                    bench=bench,
                ))
            except Exception as exc:  # pragma: no cover — Pydantic error path
                logger.warning("NCLT row failed validation: %s — %r", exc, row)
    return proceedings


class IBBIFixtureSource:
    """Reads IBBI CIRP proceedings from the live-fetch cache (ibbi_live.json).

    ibbi_fetcher.write_seed() writes there after a successful live pull.
    Falls through to the existing NCLTFixtureSource if the file is absent.
    The schema emitted by ibbi_fetcher._records_from_json() is identical to
    RawNCLTProceeding, so validation is plug-compatible.
    """

    name = "ibbi_fixture"

    _LIVE_PATH = SEED_ROOT / "ibbi_live.json"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self._LIVE_PATH

    async def fetch_all(self) -> list[RawNCLTProceeding]:
        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        proceedings: list[RawNCLTProceeding] = []
        for row in raw:
            try:
                proceedings.append(RawNCLTProceeding.model_validate(row))
            except Exception as exc:
                logger.debug("IBBI row skipped (%s): %r", exc, row)
        logger.info("IBBIFixtureSource: loaded %d proceedings from %s", len(proceedings), self._path)
        return proceedings

    async def fetch_for_cin(self, cin: str) -> list[RawNCLTProceeding]:
        return [p for p in await self.fetch_all() if p.cin == cin]


class CompositeNCLTSource:
    """Union of IBBIFixtureSource + NCLTFixtureSource, deduped by case_number.

    Priority: IBBI live data first (more current), then static fixtures.
    Use this as the default source in the RiskScorer so it picks up fresh
    IBBI data automatically after fetch_real_data.py has run.
    """

    name = "nclt_composite"

    def __init__(self) -> None:
        self._ibbi = IBBIFixtureSource()
        self._fixture = NCLTFixtureSource()

    async def fetch_all(self) -> list[RawNCLTProceeding]:
        ibbi_records = await self._ibbi.fetch_all()
        fixture_records = await self._fixture.fetch_all()
        seen: set[str] = set()
        merged: list[RawNCLTProceeding] = []
        for p in ibbi_records + fixture_records:
            key = p.case_number
            if key not in seen:
                seen.add(key)
                merged.append(p)
        return merged

    async def fetch_for_cin(self, cin: str) -> list[RawNCLTProceeding]:
        return [p for p in await self.fetch_all() if p.cin == cin]


class NCLTScraper:
    """Real NCLT cause-list scraper (PRD §10 Day-14 hardening).

    nclt.gov.in publishes daily cause lists as HTML tables (one per bench).
    `parse_cause_list_html` does the heavy lifting; this class is a thin
    HTTP shell that fetches the page and feeds the parser. The HTTP fetch
    happens via the playwright MCP server (the bench-selection page renders
    a few fields with JS), but we wrap that detail behind `fetch_html` so
    the test suite can pass canned HTML.

    NOTE: `fetch_html` is intentionally left abstract until the playwright
    handshake credentials are populated in .env.local (Day 17 pre-cache).
    """

    name = "nclt_scraper"

    def __init__(self, *, fetch_html=None) -> None:
        self._fetch_html = fetch_html

    async def fetch_html(self, url: str) -> str:
        if self._fetch_html is None:
            raise NotImplementedError(
                "NCLTScraper.fetch_html requires a playwright fetcher; pass one in "
                "via the constructor or call parse_cause_list_html directly on a "
                "pre-fetched HTML string."
            )
        return await self._fetch_html(url)

    async def fetch_all(self) -> list[RawNCLTProceeding]:
        # PRD §10 Day-17 hands this the full bench-list iteration. Day-14
        # hardening proves the parser is correct; the polling loop is wired
        # then.
        raise NotImplementedError("NCLTScraper.fetch_all — Day-17 task")

    async def fetch_for_cin(self, cin: str) -> list[RawNCLTProceeding]:
        raise NotImplementedError("NCLTScraper.fetch_for_cin — Day-17 task")

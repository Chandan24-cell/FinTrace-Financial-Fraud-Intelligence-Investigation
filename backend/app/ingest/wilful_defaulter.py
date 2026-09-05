"""RBI / CIBIL wilful defaulter list source (PRD §4.9 + §10 Day 5).

RBI publishes the 'Suspect List' and 'List of Wilful Defaulters' quarterly as
PDFs. CIBIL also maintains its own list. We aggregate both into WilfulDefaulter
nodes. Module 9 raises CRITICAL (force score >= 75) on any match.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


SEED_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds" / "wilful_defaulter"

_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_DIN_RE = re.compile(r"^\d{8}$")


class RawWilfulDefaulter(BaseModel):
    """One wilful defaulter declaration. PRD §3 keys verbatim."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    cin: str
    din: str | None = None  # Promoter DIN, when declared on an individual
    bank_name: str
    amount: float = Field(ge=0.0)
    declared_date: date
    source: str = "RBI"  # RBI | CIBIL | SUIT_FILED

    @field_validator("cin")
    @classmethod
    def _validate_cin(cls, v: str) -> str:
        if not _CIN_RE.match(v):
            raise ValueError(f"Invalid CIN: {v!r}")
        return v

    @field_validator("din")
    @classmethod
    def _validate_din(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _DIN_RE.match(v):
            raise ValueError(f"Invalid DIN: {v!r}")
        return v


class WilfulDefaulterSource(Protocol):
    name: str

    async def fetch_all(self) -> list[RawWilfulDefaulter]: ...


class WilfulDefaulterFixtureSource:
    """Reads wilful defaulter declarations from infra/seeds/wilful_defaulter/*.json."""

    name = "wilful_fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SEED_ROOT

    async def fetch_all(self) -> list[RawWilfulDefaulter]:
        if not self.root.exists():
            return []
        records: list[RawWilfulDefaulter] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for row in raw:
                records.append(RawWilfulDefaulter.model_validate(row))
        return records


# PRD §10 Phase B — free-source replacement. Default endpoints on rbi.org.in.
# Override via constructor for staging mirrors or air-gapped operator runs.
RBI_DEFAULT_LIST_URL = "https://www.rbi.org.in/scripts/BS_ViewDefaulterListLogin.aspx"
RBI_DEFAULT_SUIT_FILED_URL = "https://www.rbi.org.in/scripts/BS_NPAList.aspx"


HtmlFetcher = Callable[[str], Awaitable[str]]


class RBIWilfulScraper:
    """RBI wilful-defaulter / suit-filed list scraper (PRD §10 Phase B).

    The RBI publishes two free web lists:
      - Wilful defaulters (non-suit-filed): RBI_DEFAULT_LIST_URL
      - Suit-filed defaulters:               RBI_DEFAULT_SUIT_FILED_URL

    Both render as HTML tables. We pass the page through
    `rbi_html_parser.parse_rbi_wilful_html` which is tolerant of column-
    order drift. Layout changes on rbi.org.in only require updating the
    header heuristics in the parser, not this class.

    The booklet/CD compilation is priced; the web pages are free, no login
    required. The CIBIL public defaulter list (same shape) can be scraped
    by injecting its URL pair via the constructor.

    HTTP fetch is injected as `fetch_html` so the test suite can pass a
    canned-HTML coroutine — CI never touches rbi.org.in.
    """

    name = "rbi_wilful_scraper"

    def __init__(
        self,
        *,
        fetch_html: HtmlFetcher | None = None,
        list_url: str = RBI_DEFAULT_LIST_URL,
        suit_filed_url: str = RBI_DEFAULT_SUIT_FILED_URL,
    ) -> None:
        self._fetch_html = fetch_html
        self.list_url = list_url
        self.suit_filed_url = suit_filed_url

    async def fetch_html(self, url: str) -> str:
        if self._fetch_html is None:
            raise NotImplementedError(
                "RBIWilfulScraper.fetch_html requires an HTTP fetcher; pass one in "
                "via the constructor or use WilfulDefaulterFixtureSource for "
                "offline runs."
            )
        return await self._fetch_html(url)

    async def fetch_all(self) -> list[RawWilfulDefaulter]:
        # Local import keeps wilful_defaulter.py importable when rbi_html_parser
        # would otherwise cause a circular import (the parser imports
        # RawWilfulDefaulter from here).
        from backend.app.ingest.rbi_html_parser import parse_rbi_wilful_html

        records: list[RawWilfulDefaulter] = []
        seen: set[tuple[str, str | None, str]] = set()

        for source_label, url in (
            ("RBI", self.list_url),
            ("SUIT_FILED", self.suit_filed_url),
        ):
            try:
                html = await self.fetch_html(url)
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning("RBI fetch failed for %s (%s): %s", source_label, url, exc)
                continue

            for row in parse_rbi_wilful_html(html, source=source_label):
                key = (row.cin, row.din, row.bank_name)
                if key in seen:
                    continue
                seen.add(key)
                records.append(row)

        return records

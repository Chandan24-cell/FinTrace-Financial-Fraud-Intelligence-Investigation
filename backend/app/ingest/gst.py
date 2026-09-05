"""GST entity sources (PRD §3 Extension A + §10 Day 4).

GSTN public 'search taxpayer' returns registration details for a GSTIN. We map
those into GSTEntity nodes — the prerequisite for Module 4 patterns 8-12
(ITC carousel ring, Missing Trader, Cancelled GSTIN Still Claiming ITC, etc.).

Two implementations:
  GSTFixtureSource — reads infra/seeds/gst/*.json. Default working path.
  GSTNScraper      — real GSTN public portal scraper (Day 5+ task; captcha-gated).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


SEED_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds" / "gst"

# GSTIN regex matches RawCompany validator: 2 state + 10 PAN + 1 entity + 'Z' + checksum
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


def pan_from_gstin(gstin: str) -> str:
    """The PAN is embedded as chars 3-12 of the GSTIN (1-indexed)."""
    if not _GSTIN_RE.match(gstin):
        raise ValueError(f"Invalid GSTIN: {gstin!r}")
    return gstin[2:12]


class RawGSTEntity(BaseModel):
    """GSTEntity payload from GSTN. Maps 1:1 to the PRD §3 node properties."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    gstin: str
    pan: str
    cin: str  # back-link to Company.cin for HAS_GST_ENTITY
    registration_date: date
    cancellation_date: date | None = None
    is_cancelled: bool = False
    taxpayer_type: Literal["regular", "composition", "casual", "sez", "isd"] = "regular"
    aggregate_turnover: float = Field(default=0.0, ge=0.0)
    tax_paid_ytd: float = Field(default=0.0, ge=0.0)

    @field_validator("gstin")
    @classmethod
    def _validate_gstin(cls, v: str) -> str:
        if not _GSTIN_RE.match(v):
            raise ValueError(f"Invalid GSTIN: {v!r}")
        return v

    @field_validator("pan")
    @classmethod
    def _validate_pan(cls, v: str) -> str:
        if not _PAN_RE.match(v):
            raise ValueError(f"Invalid PAN: {v!r}")
        return v


class GSTSource(Protocol):
    name: str

    async def fetch_all(self) -> list[RawGSTEntity]: ...

    async def fetch_one(self, gstin: str) -> RawGSTEntity | None: ...


class GSTFixtureSource:
    """Reads GSTEntity records from infra/seeds/gst/*.json.

    File layout: gst_entities.json — single array of RawGSTEntity dicts. Easy to
    grep and edit during demo prep.
    """

    name = "gst_fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SEED_ROOT

    async def fetch_all(self) -> list[RawGSTEntity]:
        if not self.root.exists():
            return []
        records: list[RawGSTEntity] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for row in raw:
                records.append(RawGSTEntity.model_validate(row))
        return records

    async def fetch_one(self, gstin: str) -> RawGSTEntity | None:
        for rec in await self.fetch_all():
            if rec.gstin == gstin:
                return rec
        return None


class GSTNScraper:
    """Real GSTN public portal scraper. Day 5+ task.

    The 'Search Taxpayer by GSTIN' page is captcha-protected and re-renders state
    via aspx ViewState. Use the Playwright MCP server when wiring this up.
    """

    name = "gstn_scraper"

    async def fetch_all(self) -> list[RawGSTEntity]:
        raise NotImplementedError("GSTNScraper — Day 5+ task")

    async def fetch_one(self, gstin: str) -> RawGSTEntity | None:
        raise NotImplementedError("GSTNScraper — Day 5+ task")

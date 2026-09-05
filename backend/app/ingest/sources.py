"""Company data sources.

Protocol:    CompanySource — async fetch_bundle(cin) -> CompanyBundle | None
Implementations:
  FixtureSource    — reads infra/seeds/companies/*.json. Default, works without keys.
  MCA21V3Source    — real MCA21 V3 REST client (needs MCA21_API_KEY). Stub for now.
  CERSAIScraper    — Playwright-based scraper for CERSAI charge data. Stub for now.

PRD §10 Day 7+ uses MCA21V3Source. Days 3–6 use FixtureSource (PRD Day 6 itself
hand-codes IL&FS FY2014-18 from the SFIO report into JSON — same pattern).
"""

from __future__ import annotations

import logging
import json
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.ingest.schemas import (
    CompanyBundle,
    RawCharge,
    RawCompany,
    RawDirector,
    RawFinancialStatement,
)

logger = logging.getLogger(__name__)


SEED_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds" / "companies"


class CompanySource(Protocol):
    """Async source for one company's bundle."""

    name: str

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None: ...

    async def list_available_cins(self) -> list[str]: ...


# --- Fixture source ---------------------------------------------------------
class FixtureSource:
    """Reads pre-built CompanyBundle JSONs from infra/seeds/companies/.

    File layout: infra/seeds/companies/<CIN>.json — one bundle per file.
    """

    name = "fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SEED_ROOT

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None:
        path = self.root / f"{cin}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            return CompanyBundle.model_validate(raw)
        except ValidationError:
            # Re-raise — the pipeline will catch and emit a DataQualityError.
            raise

    async def list_available_cins(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))


# --- MCA21 V3 REST source ---------------------------------------------------
class MCA21KeyMissingError(RuntimeError):
    """Raised by MCA21V3Source when no real API key is configured. Callers
    (e.g. CompositeSource) treat this as 'fall back to fixtures', not a bug."""


class MCA21V3Source:
    """Real MCA21 V3 REST API client (PRD §10 Day 17).

    The V3 REST endpoints we consume:
      GET /v3/companies/{cin}                     -> company master + GSTIN
      GET /v3/companies/{cin}/directors           -> [{din, name, ...}, …]
      GET /v3/companies/{cin}/financials          -> [{year, revenue, ...}, …]
      GET /v3/companies/{cin}/charges             -> [{charge_id, lender, ...}, …]

    `fetch_bundle` orchestrates the four calls concurrently and assembles a
    CompanyBundle. Network errors surface as `None` (caller falls back).
    Auth-key absence is signalled distinctly via MCA21KeyMissingError so the
    CompositeSource knows we never even tried.
    """

    name = "mca21_v3"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.mca.gov.in/v3",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or get_settings().mca21_api_key
        self.base_url = base_url
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _has_real_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("PLACEHOLDER")

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
                transport=self._transport,
            )
        return self._client

    async def _get_json(self, path: str) -> Any | None:
        client = await self._ensure_client()
        try:
            resp = await client.get(path)
        except httpx.HTTPError as exc:
            logger.warning("MCA21 GET %s failed: %s", path, exc)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            logger.warning("MCA21 GET %s -> HTTP %d: %s", path, resp.status_code, resp.text[:200])
            return None
        try:
            return resp.json()
        except ValueError:
            logger.warning("MCA21 GET %s returned non-JSON body", path)
            return None

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None:
        if not self._has_real_key():
            raise MCA21KeyMissingError(
                "MCA21V3Source has no real MCA21_API_KEY — caller should fall back."
            )
        import asyncio
        company_json, directors_json, financials_json, charges_json = await asyncio.gather(
            self._get_json(f"/companies/{cin}"),
            self._get_json(f"/companies/{cin}/directors"),
            self._get_json(f"/companies/{cin}/financials"),
            self._get_json(f"/companies/{cin}/charges"),
        )
        if company_json is None:
            return None
        try:
            company = self._parse_company(company_json, cin)
            directors = [self._parse_director(d) for d in (directors_json or [])]
            financials = [self._parse_financial(f, cin) for f in (financials_json or [])]
            charges = [self._parse_charge(c, cin) for c in (charges_json or [])]
        except (ValueError, ValidationError) as exc:
            logger.warning("MCA21 payload for %s failed validation: %s", cin, exc)
            return None
        return CompanyBundle(
            company=company,
            directors=directors,
            financials=financials,
            charges=charges,
        )

    async def list_available_cins(self) -> list[str]:
        # MCA21 V3 has no "list all CINs" endpoint — enumeration is by-design.
        # CompositeSource asks FixtureSource for this when running batch jobs.
        return []

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Per-row parsers. Tolerant of MCA21's quirky key casing. ----------
    @staticmethod
    def _parse_company(row: dict, cin: str) -> RawCompany:
        return RawCompany(
            cin=row.get("cin", cin),
            name=row["name"],
            incorporation_date=_parse_iso_date(row["incorporation_date"]),
            nic_code=int(row.get("nic_code") or row.get("nic")),
            state=row.get("state", "MH"),
            gstin=row.get("gstin"),
            employee_count_reported=row.get("employee_count_reported"),
            registered_address=row.get("registered_address"),
            contact_phone=row.get("contact_phone"),
            auditor_din=row.get("auditor_din"),
        )

    @staticmethod
    def _parse_director(row: dict) -> RawDirector:
        return RawDirector(
            din=row["din"],
            name=row["name"],
            designation=row.get("designation", "director"),
            appointment_date=_parse_iso_date(row["appointment_date"]),
            cessation_date=(
                _parse_iso_date(row["cessation_date"])
                if row.get("cessation_date") else None
            ),
            is_disqualified=bool(row.get("is_disqualified", False)),
            num_directorships=int(row.get("num_directorships", 1)),
        )

    @staticmethod
    def _parse_financial(row: dict, cin: str) -> RawFinancialStatement:
        return RawFinancialStatement(
            cin=row.get("cin", cin),
            year=int(row["year"]),
            **{
                k: row[k] for k in (
                    "revenue", "cost_of_materials", "employee_cost",
                    "finance_costs", "depreciation", "other_expenses",
                    "pbt", "tax", "pat",
                    "total_assets", "total_liabilities", "fixed_assets",
                    "cwip", "current_assets", "cash_and_equivalents",
                    "receivables", "trade_payables",
                    "long_term_borrowings", "short_term_borrowings",
                    "cf_operating", "auditor_name", "auditor_type",
                    "going_concern_flag", "adverse_flag", "emphasis_count",
                    "notes_word_count", "audit_opinion_text",
                ) if k in row
            },
        )

    @staticmethod
    def _parse_charge(row: dict, cin: str) -> RawCharge:
        return RawCharge(
            cin=row.get("cin", cin),
            charge_id=row["charge_id"],
            lender_name=row["lender_name"],
            bank_branch_ifsc=row["bank_branch_ifsc"],
            amount=float(row["amount"]),
            creation_date=_parse_iso_date(row["creation_date"]),
            satisfaction_date=(
                _parse_iso_date(row["satisfaction_date"])
                if row.get("satisfaction_date") else None
            ),
            charge_type=row.get("charge_type", "hypothecation"),
        )


def _parse_iso_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# --- CERSAI Playwright-driven scraper (PRD §10 Day 17 hardening) ------------
class CERSAIScraper:
    """Playwright-driven CERSAI charges scraper.

    cersai.org.in's public search portal is captcha-protected and JS-heavy.
    Playwright is the only feasible automation path; we inject the page
    fetcher so the test suite can hand in a canned-HTML / canned-JSON
    coroutine and we don't take a hard dep on the playwright binary.

    The injected fetcher receives the CIN and must return a list of
    dict payloads matching `RawCharge` shape. In production the fetcher is
    wired via the playwright MCP server (Day-17 demo).
    """

    name = "cersai_playwright"

    def __init__(self, fetcher: Any | None = None) -> None:
        self._fetcher = fetcher

    async def fetch_charges(self, cin: str) -> list[RawCharge]:
        if self._fetcher is None:
            raise NotImplementedError(
                "CERSAIScraper requires a fetcher callable — pass one in via the "
                "constructor or use CERSAIFixtureSource for offline runs."
            )
        rows = await self._fetcher(cin)
        out: list[RawCharge] = []
        for row in rows or []:
            row = dict(row)
            row.setdefault("cin", cin)
            try:
                out.append(RawCharge.model_validate(row))
            except ValidationError as exc:
                logger.warning("CERSAI row failed validation for %s: %s", cin, exc)
        return out

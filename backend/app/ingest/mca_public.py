"""MCA Public Portal scraper — free replacement for paid MCA21 V3 + CERSAI.

PRD §10 free-source plan, Phase A. The mca.gov.in public portal exposes
three lookup pages that together cover everything the paid MCA21 V3 REST
API would have given us, *and* replace CERSAI's per-query paid portal:

  - "View Company/LLP Master Data"  -> RawCompany
  - "View Signatory Details"        -> RawDirector[]
  - "View Index of Charges"         -> RawCharge[]  (replaces CERSAI)

The portal is HTML, captcha-gated on first session, free with no login.
Verified free in 2026 — see plan file for source list.

This module deliberately splits "scrape orchestration" from "browser
automation":

  - `MCAPublicScraper` (here) is the orchestrator. Its constructor takes
    an async `fetcher(cin, page)` callable that returns the parsed
    payload for one of the three pages. Tests inject a canned-HTML fake;
    production injects the Playwright-backed fetcher (in the sibling
    `mca_public_playwright.py` module).
  - `mca_public_playwright.py` (separate) owns the Playwright session +
    captcha-bootstrap. Lazily imported so this module + its tests don't
    need the playwright binary in CI.

The orchestrator returns a `CompanyBundle` shaped exactly like the
existing MCA21V3Source output, so `CompositeCompanySource` can swap
between them transparently. Financials are NOT available on the public
portal (MCA charges per-document download fees for AOC-4 PDFs) — the
Day-16 `/upload/financials` analyst-upload path remains the substitute.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import ValidationError

from backend.app.ingest.schemas import (
    CompanyBundle,
    RawCharge,
    RawCompany,
    RawDirector,
)

logger = logging.getLogger(__name__)


# A fetcher returns the parsed payload for one of the three portal pages.
# The contract is intentionally JSON-shaped (dict / list[dict]) rather than
# HTML — the fetcher does the HTML→dict conversion so this orchestrator
# stays decoupled from page markup. Returning None means "page not found"
# (i.e. CIN doesn't exist) and the bundle is dropped.
Page = Literal["master", "signatories", "charges"]
Fetcher = Callable[[str, Page], Awaitable[Any | None]]


class MCAPublicFetcherNotConfiguredError(RuntimeError):
    """Raised when MCAPublicScraper.fetch_bundle is called without a
    fetcher injected. CompositeCompanySource treats this the same as
    MCA21KeyMissingError — silently fall through to the next tier."""


class MCAPublicScraper:
    """Orchestrates the three public-portal page scrapes into one
    `CompanyBundle`. Production wiring lives in
    `mca_public_playwright.MCAPublicPlaywrightFetcher`."""

    name = "mca_public"

    def __init__(self, fetcher: Fetcher | None = None) -> None:
        self._fetcher = fetcher

    def has_fetcher(self) -> bool:
        return self._fetcher is not None

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None:
        """Hit all three pages concurrently-ish, parse, assemble.

        Raises `MCAPublicFetcherNotConfiguredError` when no fetcher is
        wired (same convention as `MCA21KeyMissingError` — composite
        treats it as 'skip this tier, try the next one').
        """
        if self._fetcher is None:
            raise MCAPublicFetcherNotConfiguredError(
                "MCAPublicScraper has no fetcher — caller should fall through. "
                "Wire MCAPublicPlaywrightFetcher in production or inject a "
                "canned fetcher in tests."
            )

        # Master is the gate — if the CIN isn't on the master page it
        # doesn't exist on MCA at all, no need to hit the other two pages.
        try:
            master_raw = await self._fetcher(cin, "master")
        except Exception as exc:
            logger.warning("MCA public master fetch failed for %s: %s", cin, exc)
            return None
        if master_raw is None:
            return None

        # Best-effort on the other two — partial data is better than no data.
        try:
            signatories_raw = await self._fetcher(cin, "signatories")
        except Exception as exc:
            logger.warning("MCA public signatories fetch failed for %s: %s", cin, exc)
            signatories_raw = None
        try:
            charges_raw = await self._fetcher(cin, "charges")
        except Exception as exc:
            logger.warning("MCA public charges fetch failed for %s: %s", cin, exc)
            charges_raw = None

        try:
            company = self._parse_company(master_raw, cin)
            directors = self._parse_directors(signatories_raw or [])
            charges = self._parse_charges(charges_raw or [], cin)
        except (ValueError, ValidationError) as exc:
            logger.warning("MCA public payload for %s failed validation: %s", cin, exc)
            return None

        return CompanyBundle(
            company=company,
            directors=directors,
            financials=[],   # MCA public portal does not expose AOC-4 financials free
            charges=charges,
        )

    # --- Per-page parsers. Each accepts the dict shape the fetcher emits
    # rather than raw HTML, so swapping the production fetcher (Playwright)
    # for a different one (e.g. canned HTTP, or a different scraper) is
    # transparent to this module.

    @staticmethod
    def _parse_company(row: dict, cin: str) -> RawCompany:
        # The MCA master-data page typically returns these fields under
        # camelCase keys; we accept both common cases.
        name = row.get("companyName") or row.get("company_name") or row.get("name")
        if not name:
            raise ValueError("master payload missing company name")

        incorp = row.get("dateOfIncorporation") or row.get("incorporation_date")
        if not incorp:
            raise ValueError("master payload missing incorporation date")

        # NIC: the master-data page surfaces "Industrial Classification" as
        # a 5-digit code embedded in a free-text field. Accept either an
        # explicit `nic_code` int or the parent free-text string.
        nic_code = row.get("nic_code")
        if nic_code is None:
            nic_code = _first_numeric_code(row.get("industrial_classification") or "")
        if nic_code is None:
            nic_code = 1   # sentinel — M5 peer-deviation notes missing-NIC

        # State derived from CIN structure (chars 8-9) as a defensive
        # fallback when the master page abbreviates differently.
        state = (row.get("state") or cin[7:9] or "MH").upper()[:2]

        return RawCompany(
            cin=row.get("cin", cin),
            name=name,
            incorporation_date=_parse_iso_date(incorp),
            nic_code=int(nic_code),
            state=state,
            gstin=row.get("gstin"),
            employee_count_reported=row.get("employee_count_reported"),
            registered_address=row.get("registered_address") or row.get("address"),
            contact_phone=row.get("contact_phone") or row.get("email_id"),
            auditor_din=row.get("auditor_din"),
        )

    @staticmethod
    def _parse_directors(rows: list[dict]) -> list[RawDirector]:
        out: list[RawDirector] = []
        for r in rows:
            din = _coerce_din(r.get("din") or r.get("dinPan"))
            if din is None:
                continue
            try:
                out.append(RawDirector(
                    din=din,
                    name=r.get("name") or r.get("director_name") or "",
                    designation=(r.get("designation") or "director").lower(),
                    appointment_date=_parse_iso_date(
                        r.get("appointment_date") or r.get("date_of_appointment") or "1970-01-01"
                    ),
                    cessation_date=(
                        _parse_iso_date(r["cessation_date"])
                        if r.get("cessation_date") else None
                    ),
                    is_disqualified=bool(r.get("is_disqualified", False)),
                    num_directorships=int(r.get("num_directorships", 1)),
                ))
            except (ValueError, ValidationError) as exc:
                logger.warning("MCA public signatory dropped: %s", exc)
        return out

    @staticmethod
    def _parse_charges(rows: list[dict], cin: str) -> list[RawCharge]:
        out: list[RawCharge] = []
        for r in rows:
            try:
                out.append(RawCharge(
                    cin=r.get("cin", cin),
                    charge_id=str(r.get("charge_id") or r.get("scin") or ""),
                    lender_name=r.get("lender_name") or r.get("charge_holder") or "",
                    bank_branch_ifsc=r.get("bank_branch_ifsc") or r.get("ifsc") or "UNKNOWN",
                    amount=float(r.get("amount") or 0.0),
                    creation_date=_parse_iso_date(
                        r.get("creation_date") or r.get("date_of_creation") or "1970-01-01"
                    ),
                    satisfaction_date=(
                        _parse_iso_date(r["satisfaction_date"])
                        if r.get("satisfaction_date") else None
                    ),
                    charge_type=(r.get("charge_type") or "hypothecation").lower(),
                ))
            except (ValueError, ValidationError) as exc:
                logger.warning("MCA public charge row dropped: %s", exc)
        return out


# --- Helpers (module-private) ---------------------------------------------
def _first_numeric_code(text: str) -> int | None:
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    if not digits:
        return None
    try:
        n = int(digits[:5])
    except ValueError:
        return None
    return n if 1 <= n <= 99999 else None


def _coerce_din(value: Any) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    return digits[-8:].zfill(8)


def _parse_iso_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    s = str(value)[:10]
    # MCA portal sometimes uses DD/MM/YYYY; accept both.
    if "/" in s:
        d, m, y = s.split("/")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(s)


class MCAPublicFetcherProtocol(Protocol):
    """Structural type for any object suitable as `fetcher=` on the
    scraper. Lets `mca_public_playwright.py` advertise compatibility
    without importing this module's concrete class."""

    async def __call__(self, cin: str, page: Page) -> Any | None: ...

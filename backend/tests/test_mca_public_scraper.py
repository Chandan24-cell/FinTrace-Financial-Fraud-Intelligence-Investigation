"""Tests for the MCA Public Portal scraper (Phase A of the free-source
ingestion plan).

The orchestrator (`MCAPublicScraper`) is tested with a canned async
fetcher so CI never needs the playwright binary. The production
fetcher (`mca_public_playwright.MCAPublicPlaywrightFetcher`) is
exercised separately by the operator via the --bootstrap CLI.

Cases:
  - No fetcher → MCAPublicFetcherNotConfiguredError (composite skips).
  - Master 404 → bundle is None.
  - Master OK + signatories OK + charges OK → fully populated bundle.
  - Master OK + signatories raises → bundle still returned (best-effort).
  - Composite falls through to MCA-public after MCA21 placeholder.
  - Composite falls through past MCA-public to FixtureSource when both
    upstream sources have no data.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.ingest.composite import CompositeCompanySource
from backend.app.ingest.mca_public import (
    MCAPublicFetcherNotConfiguredError,
    MCAPublicScraper,
    Page,
)
from backend.app.ingest.schemas import CompanyBundle


# A canned MCA Public Portal payload covering all three pages for one CIN.
_PUBLIC_PAYLOAD = {
    "master": {
        "cin": "U99999MH2024PTC900444",
        "companyName": "Free Scrape Co Pvt Ltd",
        "dateOfIncorporation": "2024-04-01",
        "industrial_classification": "27101 Manufacture of electric motors",
        "state": "MH",
        "registered_address": "Plot 44, MIDC Andheri East, Mumbai",
        "email_id": "compliance@freescrape.in",
    },
    "signatories": [
        {
            "din": "11112222",
            "name": "Asha Sharma",
            "designation": "Director",
            "appointment_date": "2024-04-01",
        },
        {
            "din": "33334444",
            "name": "Ravi Kumar",
            "designation": "Managing Director",
            "appointment_date": "2024-06-15",
        },
    ],
    "charges": [
        {
            "charge_id": "MCA-CHG-2024-001",
            "lender_name": "State Bank of India",
            "amount": "25000000",
            "creation_date": "2024-05-12",
            "satisfaction_date": None,
            "charge_type": "hypothecation",
            "bank_branch_ifsc": "SBIN0000300",
        },
    ],
}


def _canned_fetcher(payload: dict | None):
    """Return an async callable that satisfies MCAPublicFetcherProtocol.
    `payload=None` simulates a 404 on the master page (entire CIN missing)."""
    async def fetcher(cin: str, page: Page) -> Any | None:
        if payload is None:
            return None
        return payload.get(page)
    return fetcher


def _raising_fetcher(failing_page: Page):
    """Fetcher that returns master OK but raises on a specific later page."""
    async def fetcher(cin: str, page: Page) -> Any | None:
        if page == "master":
            return _PUBLIC_PAYLOAD["master"]
        if page == failing_page:
            raise RuntimeError(f"simulated {failing_page} fetch failure")
        return _PUBLIC_PAYLOAD.get(page)
    return fetcher


@pytest.mark.asyncio
async def test_no_fetcher_raises_not_configured() -> None:
    scraper = MCAPublicScraper()
    with pytest.raises(MCAPublicFetcherNotConfiguredError):
        await scraper.fetch_bundle("U99999MH2024PTC900444")


@pytest.mark.asyncio
async def test_master_not_found_returns_none() -> None:
    scraper = MCAPublicScraper(fetcher=_canned_fetcher(None))
    bundle = await scraper.fetch_bundle("U00000XX0000PTC000000")
    assert bundle is None


@pytest.mark.asyncio
async def test_happy_path_returns_full_bundle() -> None:
    scraper = MCAPublicScraper(fetcher=_canned_fetcher(_PUBLIC_PAYLOAD))
    bundle = await scraper.fetch_bundle("U99999MH2024PTC900444")
    assert isinstance(bundle, CompanyBundle)
    assert bundle.company.name == "Free Scrape Co Pvt Ltd"
    assert bundle.company.cin == "U99999MH2024PTC900444"
    assert bundle.company.state == "MH"
    assert bundle.company.nic_code == 27101
    # Two signatories parsed.
    assert len(bundle.directors) == 2
    assert {d.din for d in bundle.directors} == {"11112222", "33334444"}
    # One charge parsed; replaces CERSAI.
    assert len(bundle.charges) == 1
    assert bundle.charges[0].charge_id == "MCA-CHG-2024-001"
    assert bundle.charges[0].amount == 25_000_000.0
    # MCA public portal does NOT expose AOC-4 financials.
    assert bundle.financials == []


@pytest.mark.asyncio
async def test_signatories_failure_still_returns_partial_bundle() -> None:
    """Best-effort policy: master OK + charges OK, signatories blows up
    -> we still return a bundle (with empty directors). Better than 404."""
    scraper = MCAPublicScraper(fetcher=_raising_fetcher("signatories"))
    bundle = await scraper.fetch_bundle("U99999MH2024PTC900444")
    assert bundle is not None
    assert bundle.directors == []
    assert len(bundle.charges) == 1


@pytest.mark.asyncio
async def test_charges_failure_still_returns_partial_bundle() -> None:
    scraper = MCAPublicScraper(fetcher=_raising_fetcher("charges"))
    bundle = await scraper.fetch_bundle("U99999MH2024PTC900444")
    assert bundle is not None
    assert bundle.charges == []
    assert len(bundle.directors) == 2


@pytest.mark.asyncio
async def test_dd_mm_yyyy_dates_parse() -> None:
    """MCA portal sometimes serves DD/MM/YYYY instead of ISO."""
    indian_payload = {
        "master": {**_PUBLIC_PAYLOAD["master"], "dateOfIncorporation": "01/04/2024"},
        "signatories": [{**_PUBLIC_PAYLOAD["signatories"][0],
                         "appointment_date": "15/04/2024"}],
        "charges": [],
    }
    scraper = MCAPublicScraper(fetcher=_canned_fetcher(indian_payload))
    bundle = await scraper.fetch_bundle("U99999MH2024PTC900444")
    assert bundle is not None
    assert bundle.company.incorporation_date.isoformat() == "2024-04-01"
    assert bundle.directors[0].appointment_date.isoformat() == "2024-04-15"


@pytest.mark.asyncio
async def test_composite_falls_through_to_mca_public_when_mca21_placeholder() -> None:
    """MCA21 in placeholder mode → composite reaches MCA-public next.
    With MCA-public serving a parsed bundle, that wins over the fixture."""
    secondary = MCAPublicScraper(fetcher=_canned_fetcher(_PUBLIC_PAYLOAD))
    composite = CompositeCompanySource(secondary=secondary)
    bundle = await composite.fetch_bundle("U99999MH2024PTC900444")
    assert bundle is not None
    assert bundle.company.name == "Free Scrape Co Pvt Ltd"


@pytest.mark.asyncio
async def test_composite_falls_through_past_mca_public_to_fixture() -> None:
    """MCA21 placeholder + MCA-public has no fetcher → fixture wins. Demo
    path stays intact regardless of free-tier wiring."""
    composite = CompositeCompanySource()
    bundle = await composite.fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    assert bundle.company.name.startswith("IL&FS")


@pytest.mark.asyncio
async def test_composite_mca_public_404_falls_to_fixture() -> None:
    """MCA-public returns master-not-found → composite tries fixture."""
    secondary = MCAPublicScraper(fetcher=_canned_fetcher(None))
    composite = CompositeCompanySource(secondary=secondary)
    bundle = await composite.fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    assert bundle.company.name.startswith("IL&FS")

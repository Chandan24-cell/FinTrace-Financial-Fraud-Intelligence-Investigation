"""Tests for Day-17 live-ingest surfaces (PRD §10 Day 17):
  - MCA21V3Source via httpx.MockTransport
  - CERSAIScraper via injected fetcher
  - CompositeCompanySource fallback + charge merge
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.ingest.composite import CompositeCompanySource
from backend.app.ingest.schemas import CompanyBundle
from backend.app.ingest.sources import (
    CERSAIScraper,
    FixtureSource,
    MCA21KeyMissingError,
    MCA21V3Source,
)


def _mock_transport(payloads: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = payloads.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


_LIVE_PAYLOADS: dict = {
    "/v3/companies/U99999MH2024PTC900111": {
        "cin": "U99999MH2024PTC900111",
        "name": "Live MCA21 Smoke Co Pvt Ltd",
        "incorporation_date": "2024-04-01",
        "nic_code": 27101,
        "state": "MH",
        "gstin": "27AAACL5555L1Z9",
        "registered_address": "Plot 17, MIDC Andheri East, Mumbai",
        "auditor_din": "01122334",
    },
    "/v3/companies/U99999MH2024PTC900111/directors": [
        {
            "din": "12345678", "name": "Live Director",
            "designation": "managing director",
            "appointment_date": "2024-04-01",
            "num_directorships": 1,
        }
    ],
    "/v3/companies/U99999MH2024PTC900111/financials": [
        {
            "cin": "U99999MH2024PTC900111", "year": 2025,
            "revenue": 12_000_000.0, "pat": 800_000.0,
            "total_assets": 8_000_000.0,
        }
    ],
    "/v3/companies/U99999MH2024PTC900111/charges": [],
}


@pytest.mark.asyncio
async def test_mca21_placeholder_key_raises_key_missing() -> None:
    src = MCA21V3Source(api_key="PLACEHOLDER_MCA21_KEY")
    with pytest.raises(MCA21KeyMissingError):
        await src.fetch_bundle("U99999MH2024PTC900111")


@pytest.mark.asyncio
async def test_mca21_real_key_fetches_bundle_via_mocked_http() -> None:
    src = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport(_LIVE_PAYLOADS))
    try:
        bundle = await src.fetch_bundle("U99999MH2024PTC900111")
    finally:
        await src.aclose()
    assert isinstance(bundle, CompanyBundle)
    assert bundle.company.name == "Live MCA21 Smoke Co Pvt Ltd"
    assert bundle.company.gstin == "27AAACL5555L1Z9"
    assert len(bundle.directors) == 1
    assert len(bundle.financials) == 1
    assert bundle.financials[0].year == 2025


@pytest.mark.asyncio
async def test_mca21_unknown_cin_returns_none() -> None:
    src = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport({}))
    try:
        bundle = await src.fetch_bundle("U00000XX0000PTC000000")
    finally:
        await src.aclose()
    assert bundle is None


@pytest.mark.asyncio
async def test_mca21_network_error_swallowed_as_none() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")
    src = MCA21V3Source(api_key="REAL_KEY", transport=httpx.MockTransport(boom))
    try:
        bundle = await src.fetch_bundle("U99999MH2024PTC900111")
    finally:
        await src.aclose()
    assert bundle is None


@pytest.mark.asyncio
async def test_composite_falls_back_to_fixture_when_key_placeholder() -> None:
    """PRD §10 Day-17: composite must transparently fall through to fixtures."""
    composite = CompositeCompanySource()
    bundle = await composite.fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    assert bundle.company.name.startswith("IL&FS")


@pytest.mark.asyncio
async def test_composite_prefers_mca21_when_real_key_present() -> None:
    primary = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport(_LIVE_PAYLOADS))
    composite = CompositeCompanySource(primary=primary)
    try:
        bundle = await composite.fetch_bundle("U99999MH2024PTC900111")
    finally:
        await primary.aclose()
    assert bundle is not None
    assert bundle.company.name == "Live MCA21 Smoke Co Pvt Ltd"


@pytest.mark.asyncio
async def test_composite_returns_none_when_both_sources_miss() -> None:
    primary = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport({}))
    composite = CompositeCompanySource(primary=primary)
    try:
        bundle = await composite.fetch_bundle("U00000XX0000PTC000000")
    finally:
        await primary.aclose()
    assert bundle is None


@pytest.mark.asyncio
async def test_composite_falls_back_when_mca21_returns_none() -> None:
    """Real-key MCA21 returning 404 must let the fixture answer (PRD §10)."""
    primary = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport({}))
    composite = CompositeCompanySource(primary=primary)
    try:
        bundle = await composite.fetch_bundle("U45201MH2005PTC155294")
    finally:
        await primary.aclose()
    assert bundle is not None
    assert bundle.company.name.startswith("IL&FS")


@pytest.mark.asyncio
async def test_composite_merges_cersai_charges_onto_primary() -> None:
    """When MCA21 returns a bundle, extra CERSAI charges are deduped and merged."""
    cin = "U99999MH2024PTC900111"
    # Inject one extra CERSAI charge so we can verify merge behaviour.
    async def cersai_fetcher(_cin: str) -> list:
        return [{
            "cin": cin,
            "charge_id": "EXTRA-CHG-001",
            "lender_name": "HDFC Bank",
            "bank_branch_ifsc": "HDFC0000412",
            "amount": 7_500_000.0,
            "creation_date": "2024-09-01",
            "charge_type": "hypothecation",
        }]
    primary = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport(_LIVE_PAYLOADS))
    composite = CompositeCompanySource(
        primary=primary,
        cersai=CERSAIScraper(fetcher=cersai_fetcher),
    )
    try:
        bundle = await composite.fetch_bundle(cin)
    finally:
        await primary.aclose()
    assert bundle is not None
    assert any(c.charge_id == "EXTRA-CHG-001" for c in bundle.charges)


@pytest.mark.asyncio
async def test_cersai_scraper_without_fetcher_raises() -> None:
    scraper = CERSAIScraper()
    with pytest.raises(NotImplementedError):
        await scraper.fetch_charges("U45201MH2005PTC155294")


@pytest.mark.asyncio
async def test_cersai_scraper_validates_rows() -> None:
    async def fetcher(_cin: str) -> list:
        return [
            {  # valid
                "cin": "U45201MH2005PTC155294",
                "charge_id": "CHG-1",
                "lender_name": "SBI",
                "bank_branch_ifsc": "SBIN0001234",
                "amount": 100.0,
                "creation_date": "2024-01-01",
                "charge_type": "hypothecation",
            },
            {"cin": "U45201MH2005PTC155294", "charge_id": "BAD"},  # missing fields
        ]
    scraper = CERSAIScraper(fetcher=fetcher)
    rows = await scraper.fetch_charges("U45201MH2005PTC155294")
    assert len(rows) == 1
    assert rows[0].charge_id == "CHG-1"


@pytest.mark.asyncio
async def test_composite_list_available_cins_uses_fallback() -> None:
    composite = CompositeCompanySource(fallback=FixtureSource())
    cins = await composite.list_available_cins()
    assert "U45201MH2005PTC155294" in cins

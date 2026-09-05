"""Day-5 ingest fixture tests: NCLT, WilfulDefaulter, CERSAI."""

import pytest

from backend.app.ingest.cersai import CERSAIFixtureSource
from backend.app.ingest.nclt import NCLTFixtureSource, RawNCLTProceeding
from backend.app.ingest.wilful_defaulter import (
    RawWilfulDefaulter,
    WilfulDefaulterFixtureSource,
)


# --- NCLT ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nclt_fixture_loads_known_admitted_cirp() -> None:
    records = await NCLTFixtureSource().fetch_all()
    assert len(records) >= 1
    cirp = [r for r in records if r.petition_type == "CIRP" and r.status == "admitted"]
    assert any(r.cin == "U45201MH2005PTC155294" for r in cirp), (
        "IL&FS admitted CIRP must be in the fixture — PRD §4.9 force min score 75"
    )


@pytest.mark.asyncio
async def test_nclt_fetch_for_cin_filters() -> None:
    records = await NCLTFixtureSource().fetch_for_cin("U45201MH2005PTC155294")
    assert len(records) >= 1
    assert all(r.cin == "U45201MH2005PTC155294" for r in records)


def test_rawnctl_rejects_bad_cin() -> None:
    with pytest.raises(Exception):
        RawNCLTProceeding.model_validate({
            "case_number": "X-1",
            "cin": "NOT-A-CIN",
            "filing_date": "2018-10-01",
        })


# --- Wilful defaulter ------------------------------------------------------
@pytest.mark.asyncio
async def test_wilful_fixture_includes_ilfs() -> None:
    records = await WilfulDefaulterFixtureSource().fetch_all()
    cins = {r.cin for r in records}
    assert "U45201MH2005PTC155294" in cins


def test_wilful_rejects_bad_din() -> None:
    with pytest.raises(Exception):
        RawWilfulDefaulter.model_validate({
            "cin": "U45201MH2005PTC155294",
            "din": "BADDIN",
            "bank_name": "Test Bank",
            "amount": 100.0,
            "declared_date": "2020-01-01",
        })


# --- CERSAI ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_cersai_fixture_includes_serial_cycling_for_efg() -> None:
    """EFG Trading has 3 charges across overlapping windows — Pattern 14 prep."""
    records = await CERSAIFixtureSource().fetch_for_cin("U46190MH2019PTC295432")
    assert len(records) >= 3
    assert all(c.lender_name == "Yes Bank" for c in records)


@pytest.mark.asyncio
async def test_cersai_total_count_matches_seed() -> None:
    records = await CERSAIFixtureSource().fetch_all()
    assert len(records) >= 5

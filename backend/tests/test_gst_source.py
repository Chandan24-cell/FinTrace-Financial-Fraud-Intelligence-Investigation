"""Tests for the GSTEntity fixture source (Day 4)."""

import pytest
from pydantic import ValidationError

from backend.app.ingest.gst import GSTFixtureSource, RawGSTEntity, pan_from_gstin


def test_pan_extracted_from_gstin() -> None:
    assert pan_from_gstin("27AAACI4567A1Z5") == "AAACI4567A"


def test_pan_extraction_rejects_bad_gstin() -> None:
    with pytest.raises(ValueError, match="Invalid GSTIN"):
        pan_from_gstin("NOT-A-GSTIN")


def test_rawgstentity_rejects_bad_pan() -> None:
    with pytest.raises(ValidationError, match="Invalid PAN"):
        RawGSTEntity.model_validate({
            "gstin": "27AAACI4567A1Z5",
            "pan": "BADPAN",
            "cin": "U45201MH2005PTC155294",
            "registration_date": "2005-07-15",
        })


@pytest.mark.asyncio
async def test_fixture_loads_ten_entities() -> None:
    source = GSTFixtureSource()
    records = await source.fetch_all()
    assert len(records) == 10, "Expected one GSTEntity per Day-3 fixture company"


@pytest.mark.asyncio
async def test_fixture_includes_cancelled_efg_trading() -> None:
    records = await GSTFixtureSource().fetch_all()
    efg = next((r for r in records if r.cin == "U46190MH2019PTC295432"), None)
    assert efg is not None
    assert efg.is_cancelled is True
    assert efg.cancellation_date is not None


@pytest.mark.asyncio
async def test_fetch_one_by_gstin() -> None:
    source = GSTFixtureSource()
    record = await source.fetch_one("27AAACI4567A1Z5")
    assert record is not None
    assert record.cin == "U45201MH2005PTC155294"


@pytest.mark.asyncio
async def test_fetch_one_returns_none_for_missing() -> None:
    record = await GSTFixtureSource().fetch_one("99XXXXX9999X9Z9")
    assert record is None


@pytest.mark.asyncio
async def test_all_pans_match_embedded_chars() -> None:
    records = await GSTFixtureSource().fetch_all()
    for r in records:
        assert r.pan == r.gstin[2:12]

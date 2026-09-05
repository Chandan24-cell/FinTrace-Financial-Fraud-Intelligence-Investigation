"""Tests for the data.gov.in bulk MCA master-data source (PRD §10 Phase C).

CI strategy: inject CSV strings via the `csv_texts` constructor arg —
the source never reads from disk. data.gov.in is not contacted.

Cases:
  - Empty source (no CSV, no cache dir) → list_available_cins == [].
  - Well-formed CSV → list_available_cins == sorted CIN universe.
  - fetch_bundle(known cin) → CompanyBundle with master-only fields populated.
  - fetch_bundle(unknown cin) → None.
  - Column-name aliases (DateOfRegistration vs Date_Of_Registration) parse
    identically — pins the resilience to MCA's snapshot drift.
  - Full-state-name 'MAHARASHTRA' normalizes to 'MH'.
  - Row missing date/NIC/state is dropped (logged, not raised).
  - Composite.list_available_cins() unions bulk + fixture so demo CINs
    remain reachable even after a real CSV refresh.
"""

from __future__ import annotations

import pytest

from backend.app.ingest.composite import CompositeCompanySource
from backend.app.ingest.data_gov_in import DataGovInBulkSource
from backend.app.ingest.schemas import CompanyBundle


_WELL_FORMED_CSV = """\
CORPORATE_IDENTIFICATION_NUMBER,COMPANY_NAME,DATE_OF_REGISTRATION,INDUSTRIAL_CLASS,REGISTERED_STATE,REGISTERED_OFFICE_ADDRESS
U45201MH2005PTC155294,IL&FS Engineering And Construction Co Ltd,2005-04-01,45201,MAHARASHTRA,"Plot 14, Bandra Kurla Complex"
U27101MH2010PTC215432,Synthetic Steels Pvt Ltd,15-06-2010,27101,MAHARASHTRA,"MIDC Andheri East, Mumbai"
U62013MH2016PTC267890,Demo IT Services Pvt Ltd,01/04/2016,62013 Computer Programming,MH,"Worli, Mumbai"
"""


_ALIAS_CSV = """\
CIN,Company Name,Date of Registration,NIC Code,State,Registered Office Address
U99999MH2024PTC900444,Free Scrape Co Pvt Ltd,2024-04-01,27101,Maharashtra,"Plot 44 MIDC Andheri East"
"""


_BROKEN_ROW_CSV = """\
CIN,COMPANY_NAME,DATE_OF_REGISTRATION,INDUSTRIAL_CLASS,REGISTERED_STATE
NOT-A-CIN,Junk Row,2020-01-01,12345,MH
U45201MH2005PTC155294,Good Row,,45201,MH
U27101MH2010PTC215432,Missing NIC,2010-01-01,,MH
U62013MH2016PTC267890,Missing State,2016-01-01,62013,
U29304MH2019PTC287654,Complete Row,2019-04-01,29304,MH
"""


@pytest.mark.asyncio
async def test_empty_source_returns_empty_universe(tmp_path) -> None:
    """No CSVs anywhere — composite must still work; data.gov.in source
    simply contributes nothing."""
    src = DataGovInBulkSource(cache_dir=tmp_path / "empty")
    assert await src.list_available_cins() == []
    assert await src.fetch_bundle("U45201MH2005PTC155294") is None


@pytest.mark.asyncio
async def test_well_formed_csv_enumerates_universe() -> None:
    src = DataGovInBulkSource(csv_texts=[_WELL_FORMED_CSV])
    cins = await src.list_available_cins()
    assert cins == [
        "U27101MH2010PTC215432",
        "U45201MH2005PTC155294",
        "U62013MH2016PTC267890",
    ]


@pytest.mark.asyncio
async def test_fetch_bundle_returns_master_only() -> None:
    src = DataGovInBulkSource(csv_texts=[_WELL_FORMED_CSV])
    bundle = await src.fetch_bundle("U45201MH2005PTC155294")
    assert isinstance(bundle, CompanyBundle)
    assert bundle.company.name == "IL&FS Engineering And Construction Co Ltd"
    assert bundle.company.state == "MH"
    assert bundle.company.nic_code == 45201
    # data.gov.in master snapshot carries no director / charge / financial rows.
    assert bundle.directors == []
    assert bundle.charges == []
    assert bundle.financials == []


@pytest.mark.asyncio
async def test_fetch_bundle_unknown_cin_returns_none() -> None:
    src = DataGovInBulkSource(csv_texts=[_WELL_FORMED_CSV])
    assert await src.fetch_bundle("U00000XX0000PTC000000") is None


@pytest.mark.asyncio
async def test_column_alias_variants_parse_identically() -> None:
    """MCA renames columns subtly between snapshots — drift must not
    silently corrupt the dataset."""
    src = DataGovInBulkSource(csv_texts=[_ALIAS_CSV])
    cins = await src.list_available_cins()
    assert cins == ["U99999MH2024PTC900444"]
    bundle = await src.fetch_bundle("U99999MH2024PTC900444")
    assert bundle is not None
    assert bundle.company.nic_code == 27101


@pytest.mark.asyncio
async def test_full_state_name_normalizes_to_two_letter_code() -> None:
    src = DataGovInBulkSource(csv_texts=[_WELL_FORMED_CSV])
    bundle = await src.fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    assert bundle.company.state == "MH"  # from 'MAHARASHTRA'


@pytest.mark.asyncio
async def test_nic_with_trailing_description_extracts_leading_digits() -> None:
    """data.gov.in rows like '62013 Computer Programming' must still yield
    a valid NIC integer — RawCompany requires `nic_code: int`."""
    src = DataGovInBulkSource(csv_texts=[_WELL_FORMED_CSV])
    bundle = await src.fetch_bundle("U62013MH2016PTC267890")
    assert bundle is not None
    assert bundle.company.nic_code == 62013


@pytest.mark.asyncio
async def test_broken_rows_dropped_complete_row_kept() -> None:
    src = DataGovInBulkSource(csv_texts=[_BROKEN_ROW_CSV])
    cins = await src.list_available_cins()
    # Essential fields = incorporation_date + state. Missing either drops
    # the row. NIC is non-essential after the TN snapshot work — the
    # entire TN universe ships nic_code='NA' with industry info in a
    # separate text column, so a missing NIC now maps to sentinel 99999
    # ("Other") instead of dropping the row. M5 peer deviation cleanly
    # skips sentinel rows since no BSE benchmark exists for nic 99999.
    assert cins == ["U27101MH2010PTC215432", "U29304MH2019PTC287654"]
    missing_nic = await src.fetch_bundle("U27101MH2010PTC215432")
    assert missing_nic is not None
    assert missing_nic.company.nic_code == 99999


@pytest.mark.asyncio
async def test_composite_lists_union_of_bulk_and_fixture() -> None:
    """When the operator has loaded a real data.gov.in snapshot, the
    composite's list must include both the bulk universe and the demo
    fixture CINs — so the storyboard never breaks because someone ran
    `--refresh` and the IL&FS bundle vanished from a list."""
    bulk = DataGovInBulkSource(csv_texts=[_ALIAS_CSV])  # one extra CIN
    composite = CompositeCompanySource(bulk_universe=bulk)
    cins = await composite.list_available_cins()
    assert "U99999MH2024PTC900444" in cins  # from data.gov.in
    assert "U45201MH2005PTC155294" in cins  # from fixture (IL&FS demo)


@pytest.mark.asyncio
async def test_composite_falls_back_to_fixture_when_bulk_empty() -> None:
    """No CSV loaded (operator hasn't run --refresh yet) → composite
    behaves identically to pre-Phase-C."""
    composite = CompositeCompanySource()  # default DataGovInBulkSource() with no CSV
    cins = await composite.list_available_cins()
    # Identical to fixture's listing.
    assert "U45201MH2005PTC155294" in cins

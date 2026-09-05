"""DataConfidence tier tests — PRD §7.1 exact thresholds.

| Data Available                        | Score |
|---------------------------------------|-------|
| MCA master only                       | 25%   |
| MCA + 1 year financials               | 45%   |
| MCA + 2+ years financials             | 65%   |
| Above + GST upload                    | 80%   |
| Above + bank statements               | 92%   |
"""

from datetime import date

from backend.app.ingest.data_confidence import compute_data_confidence
from backend.app.ingest.schemas import CompanyBundle, RawCompany, RawFinancialStatement


def _bundle(*, financials_count: int, gst: bool = False, bank: bool = False) -> CompanyBundle:
    company = RawCompany(
        cin="U45201MH2005PTC155294",
        name="Test Co",
        incorporation_date=date(2010, 1, 1),
        nic_code=27101,
        state="MH",
    )
    financials = [
        RawFinancialStatement(cin=company.cin, year=2020 + i, revenue=1000.0)
        for i in range(financials_count)
    ]
    return CompanyBundle(
        company=company,
        directors=[],
        financials=financials,
        charges=[],
        has_gst_upload=gst,
        has_bank_upload=bank,
    )


def test_mca_only_is_25() -> None:
    assert compute_data_confidence(_bundle(financials_count=0)) == 25


def test_mca_plus_one_year_is_45() -> None:
    assert compute_data_confidence(_bundle(financials_count=1)) == 45


def test_mca_plus_two_years_is_65() -> None:
    assert compute_data_confidence(_bundle(financials_count=2)) == 65


def test_mca_plus_three_years_is_still_65_without_gst() -> None:
    assert compute_data_confidence(_bundle(financials_count=3)) == 65


def test_two_years_plus_gst_is_80() -> None:
    assert compute_data_confidence(_bundle(financials_count=2, gst=True)) == 80


def test_two_years_plus_gst_and_bank_is_92() -> None:
    assert compute_data_confidence(_bundle(financials_count=2, gst=True, bank=True)) == 92


def test_gst_alone_does_not_promote_above_65_without_2plus_years() -> None:
    # PRD: GST only matters if you already have 2+ years of financials.
    assert compute_data_confidence(_bundle(financials_count=1, gst=True)) == 45


def test_bank_without_gst_does_not_jump_to_92() -> None:
    # PRD ordering: GST gates the path from 65 to 80; bank gates 80 to 92.
    assert compute_data_confidence(_bundle(financials_count=2, gst=False, bank=True)) == 65

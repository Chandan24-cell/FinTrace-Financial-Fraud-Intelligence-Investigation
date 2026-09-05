"""Schema validation tests — Pydantic rejects malformed payloads with clear messages."""

from datetime import date

import pytest
from pydantic import ValidationError

from backend.app.ingest.schemas import (
    RawCharge,
    RawCompany,
    RawDirector,
    RawFinancialStatement,
)


class TestRawCompany:
    def _base(self, **overrides) -> dict:
        d: dict = {
            "cin": "U45201MH2005PTC155294",
            "name": "Example Pvt Ltd",
            "incorporation_date": "2010-01-01",
            "nic_code": 27101,
            "state": "MH",
        }
        d.update(overrides)
        return d

    def test_accepts_valid_company(self) -> None:
        c = RawCompany.model_validate(self._base())
        assert c.cin == "U45201MH2005PTC155294"

    def test_rejects_malformed_cin(self) -> None:
        with pytest.raises(ValidationError, match="Invalid CIN"):
            RawCompany.model_validate(self._base(cin="NOT-A-CIN"))

    def test_rejects_cin_wrong_length(self) -> None:
        with pytest.raises(ValidationError):
            RawCompany.model_validate(self._base(cin="U27101MH2005PTC15529"))  # 20 chars

    def test_rejects_invalid_gstin(self) -> None:
        with pytest.raises(ValidationError, match="Invalid GSTIN"):
            RawCompany.model_validate(self._base(gstin="NOT-A-GSTIN"))

    def test_accepts_valid_gstin(self) -> None:
        c = RawCompany.model_validate(self._base(gstin="27AAACI4567A1Z5"))
        assert c.gstin == "27AAACI4567A1Z5"

    def test_rejects_invalid_auditor_din(self) -> None:
        with pytest.raises(ValidationError, match="Invalid auditor DIN"):
            RawCompany.model_validate(self._base(auditor_din="1234"))  # too short

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            RawCompany.model_validate(self._base(unknown_field="boom"))

    def test_rejects_state_not_two_chars(self) -> None:
        with pytest.raises(ValidationError):
            RawCompany.model_validate(self._base(state="MAH"))


class TestRawDirector:
    def _base(self, **overrides) -> dict:
        d: dict = {
            "din": "00009876",
            "name": "Test Director",
            "appointment_date": "2010-01-01",
        }
        d.update(overrides)
        return d

    def test_accepts_valid_director(self) -> None:
        d = RawDirector.model_validate(self._base())
        assert d.din == "00009876"

    def test_rejects_short_din(self) -> None:
        with pytest.raises(ValidationError, match="Invalid DIN"):
            RawDirector.model_validate(self._base(din="123"))

    def test_rejects_cessation_before_appointment(self) -> None:
        with pytest.raises(ValidationError, match="cessation_date"):
            RawDirector.model_validate(self._base(
                appointment_date="2020-01-01",
                cessation_date="2019-12-31",
            ))


class TestRawFinancialStatement:
    def _base(self, **overrides) -> dict:
        d: dict = {
            "cin": "U45201MH2005PTC155294",
            "year": 2023,
        }
        d.update(overrides)
        return d

    def test_accepts_minimal_statement(self) -> None:
        f = RawFinancialStatement.model_validate(self._base())
        assert f.year == 2023
        assert f.revenue == 0.0

    def test_rejects_negative_total_assets(self) -> None:
        with pytest.raises(ValidationError, match="total_assets"):
            RawFinancialStatement.model_validate(self._base(total_assets=-100.0))

    def test_rejects_year_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            RawFinancialStatement.model_validate(self._base(year=1850))


class TestRawCharge:
    def test_rejects_invalid_ifsc(self) -> None:
        with pytest.raises(ValidationError, match="Invalid IFSC"):
            RawCharge.model_validate({
                "cin": "U45201MH2005PTC155294",
                "charge_id": "X-1",
                "lender_name": "Test Bank",
                "bank_branch_ifsc": "NOTANIFSC",
                "amount": 100.0,
                "creation_date": "2020-01-01",
            })

    def test_accepts_valid_ifsc(self) -> None:
        ch = RawCharge.model_validate({
            "cin": "U45201MH2005PTC155294",
            "charge_id": "X-1",
            "lender_name": "Test Bank",
            "bank_branch_ifsc": "SBIN0000300",
            "amount": 100.0,
            "creation_date": "2020-01-01",
        })
        assert ch.bank_branch_ifsc == "SBIN0000300"

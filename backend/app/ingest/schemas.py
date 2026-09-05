"""Pydantic schemas for raw ingestion payloads.

These mirror the shapes returned by MCA21 V3 / CERSAI / GST APIs. Property names
match the Neo4j node labels in PRD §3 so the writer layer can map them directly.

Validation errors are surfaced as ValueError -> the pipeline catches them and writes
DataQualityError nodes instead of crashing on malformed data.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# PRD §3 — CIN format: e.g. "U45201MH2005PTC155294"
# Letter + 5-digit NIC + 2-letter state + 4-digit year + 3-letter type + 6-digit serial
_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
# Director Identification Number — 8 digits
_DIN_RE = re.compile(r"^\d{8}$")
# GSTIN — 15 chars: 2 state code + 10 PAN + 1 entity + 1 'Z' + 1 checksum
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}$")
# IFSC — 4 alpha + 0 + 6 alphanum
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


# --- Company master (from MCA21 V3 'GET /v3/companies/{cin}') --------------
class RawCompany(BaseModel):
    """Company master record. CIN is the natural key."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    # CIN length is enforced by _CIN_RE; keeping min/max here would short-circuit
    # the custom validator and produce a less useful error message.
    cin: str
    name: Annotated[str, Field(min_length=1, max_length=300)]
    incorporation_date: date
    nic_code: Annotated[int, Field(ge=1, le=99999)]
    state: Annotated[str, Field(min_length=2, max_length=2)]
    gstin: str | None = None
    employee_count_reported: int | None = Field(default=None, ge=0)
    registered_address: str | None = None
    contact_phone: str | None = None
    auditor_din: str | None = None  # CA firm partner DIN — for hypergraph module 10

    @field_validator("cin")
    @classmethod
    def _validate_cin(cls, v: str) -> str:
        if not _CIN_RE.match(v):
            raise ValueError(f"Invalid CIN format: {v!r}")
        return v

    @field_validator("gstin")
    @classmethod
    def _validate_gstin(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GSTIN_RE.match(v):
            raise ValueError(f"Invalid GSTIN format: {v!r}")
        return v

    @field_validator("auditor_din")
    @classmethod
    def _validate_auditor_din(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _DIN_RE.match(v):
            raise ValueError(f"Invalid auditor DIN: {v!r}")
        return v


# --- Director row (from MCA21 'GET /v3/companies/{cin}/directors') ---------
class RawDirector(BaseModel):
    """Director row. (din, cin) is the natural key when paired with a relationship."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    din: str
    name: Annotated[str, Field(min_length=1, max_length=200)]
    dob: date | None = None
    designation: Literal["director", "managing director", "wholetime director", "nominee"] = "director"
    appointment_date: date
    cessation_date: date | None = None
    is_disqualified: bool = False
    num_directorships: Annotated[int, Field(ge=0, le=200)] = 1

    @field_validator("din")
    @classmethod
    def _validate_din(cls, v: str) -> str:
        if not _DIN_RE.match(v):
            raise ValueError(f"Invalid DIN: {v!r}")
        return v

    @model_validator(mode="after")
    def _check_cessation_after_appointment(self) -> "RawDirector":
        if self.cessation_date and self.cessation_date < self.appointment_date:
            raise ValueError(
                f"cessation_date ({self.cessation_date}) is before "
                f"appointment_date ({self.appointment_date})"
            )
        return self


# --- Financial statement (from MCA21 AOC-4 PDFs / future XBRL) -------------
class RawFinancialStatement(BaseModel):
    """One financial statement per company per FY. PRD §3 keys verbatim."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    cin: str
    year: Annotated[int, Field(ge=2000, le=2099)]

    # P&L (in INR, full rupees — not lakhs/crores)
    revenue: float = 0.0
    other_income: float = 0.0
    cost_of_materials: float = 0.0
    employee_cost: float = 0.0
    finance_costs: float = 0.0
    depreciation: float = 0.0
    other_expenses: float = 0.0
    pbt: float = 0.0
    tax: float = 0.0
    pat: float = 0.0

    # Balance sheet
    total_assets: float = 0.0
    fixed_assets: float = 0.0
    cwip: float = 0.0  # Capital Work In Progress (PRD §4.2 check)
    current_assets: float = 0.0
    cash_and_equivalents: float = 0.0
    receivables: float = 0.0
    inventory: float = 0.0
    total_liabilities: float = 0.0
    long_term_borrowings: float = 0.0
    short_term_borrowings: float = 0.0
    trade_payables: float = 0.0

    # Cash flow
    cf_operating: float = 0.0
    cf_investing: float = 0.0
    cf_financing: float = 0.0

    # Audit opinion (Module 7 inputs)
    audit_opinion_text: str = ""
    auditor_name: str = ""
    auditor_din: str | None = None
    auditor_type: Literal["sole_proprietor", "partnership", "llp"] = "partnership"
    emphasis_count: int = 0
    going_concern_flag: bool = False
    adverse_flag: bool = False
    notes_word_count: int = 0

    # Document forensics (Module 8 inputs)
    pdf_creation_software: str | None = None
    pdf_metadata_anomaly: bool = False

    @field_validator("cin")
    @classmethod
    def _validate_cin(cls, v: str) -> str:
        if not _CIN_RE.match(v):
            raise ValueError(f"Invalid CIN: {v!r}")
        return v

    @model_validator(mode="after")
    def _bs_sanity(self) -> "RawFinancialStatement":
        if self.total_assets < 0:
            raise ValueError("total_assets cannot be negative")
        return self


# --- CERSAI charge (Day 5 actually, but the shape is useful now) -----------
class RawCharge(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    cin: str
    charge_id: str
    lender_name: str
    bank_branch_ifsc: str | None = None
    amount: float = Field(ge=0.0)
    creation_date: date
    satisfaction_date: date | None = None
    charge_type: Literal["fixed", "floating", "mortgage", "hypothecation", "pledge", "other"] = "other"

    @field_validator("cin")
    @classmethod
    def _validate_cin(cls, v: str) -> str:
        if not _CIN_RE.match(v):
            raise ValueError(f"Invalid CIN: {v!r}")
        return v

    @field_validator("bank_branch_ifsc")
    @classmethod
    def _validate_ifsc(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _IFSC_RE.match(v):
            raise ValueError(f"Invalid IFSC: {v!r}")
        return v


# --- The bundle a source returns for one company --------------------------
class CompanyBundle(BaseModel):
    """All data pulled from sources for a single company."""

    model_config = ConfigDict(extra="forbid")

    company: RawCompany
    directors: list[RawDirector] = Field(default_factory=list)
    financials: list[RawFinancialStatement] = Field(default_factory=list)
    charges: list[RawCharge] = Field(default_factory=list)
    has_gst_upload: bool = False
    has_bank_upload: bool = False
    fetched_at: datetime = Field(default_factory=lambda: datetime.utcnow())

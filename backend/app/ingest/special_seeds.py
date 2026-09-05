"""Typed loaders for the Day-6 demo seeds (DHFL evergreening + ITC carousel).

These datasets have shapes that don't fit the per-company FixtureSource because
they cross multiple node types (LoanRepayment + FUNDED_REPAYMENT_OF edges, or
GSTEntity + ITCClaim + CLAIMS_ITC_FROM cycles). One JSON per demo scenario.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.ingest.schemas import RawCharge, RawCompany, RawDirector


SEEDS_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds"

_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}$")


# --- DHFL evergreening cluster --------------------------------------------
class RawLoanRepayment(BaseModel):
    """One repayment event. Together with LoanDisbursement these form the
    evergreening 'round-trip' pattern (PRD §4.4 P13)."""

    model_config = ConfigDict(extra="forbid")

    loan_id: str          # Matches LoanDisbursement.loan_id (PRD §3)
    cin: str              # Repaying company
    repayment_date: date
    amount: float = Field(ge=0.0)
    source_account_ifsc: str | None = None
    days_to_repayment: int | None = None

    @field_validator("cin")
    @classmethod
    def _validate_cin(cls, v: str) -> str:
        if not _CIN_RE.match(v):
            raise ValueError(f"Invalid CIN: {v!r}")
        return v


class RawFundedRepaymentOf(BaseModel):
    """The core evergreening edge: 'a new loan to B funded the repayment of A'."""

    model_config = ConfigDict(extra="forbid")

    funding_loan_id: str      # The fresh loan that supplied the cash
    funded_repayment_loan_id: str  # The repayment it covered
    days_between: int = Field(ge=0)
    amount_overlap_pct: float = Field(ge=0.0, le=100.0)


class DHFLClusterSeed(BaseModel):
    """One JSON describes an entire evergreening cluster — DHFL + shell conduits."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    description: str = ""
    companies: list[RawCompany]
    directors: list[RawDirector]
    director_cin_links: list[tuple[str, str]] = Field(default_factory=list)  # (din, cin)
    charges: list[RawCharge]  # Disbursements — reuse RawCharge schema
    repayments: list[RawLoanRepayment]
    funded_repayments: list[RawFundedRepaymentOf]


def load_dhfl_cluster(path: Path | None = None) -> DHFLClusterSeed:
    path = path or SEEDS_ROOT / "dhfl" / "dhfl_cluster.json"
    return DHFLClusterSeed.model_validate(json.loads(path.read_text(encoding="utf-8")))


# --- ITC carousel ring ----------------------------------------------------
class RawITCClaim(BaseModel):
    """ITC claim per period per GSTIN (PRD §3)."""

    model_config = ConfigDict(extra="forbid")

    gstin: str
    period: str  # 'YYYYMM' e.g. '202309'
    claimed_amount: float = Field(ge=0.0)
    eligible_amount: float = Field(ge=0.0)
    source_gstin: str | None = None  # Counterparty whose invoice supplied the ITC

    @field_validator("gstin", "source_gstin")
    @classmethod
    def _validate_gstin(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GSTIN_RE.match(v):
            raise ValueError(f"Invalid GSTIN: {v!r}")
        return v


class RawClaimsITCFrom(BaseModel):
    """Directed edge for the carousel ring (PRD §3)."""

    model_config = ConfigDict(extra="forbid")

    from_gstin: str
    to_gstin: str
    period: str
    amount: float = Field(ge=0.0)
    invoice_count: int = Field(ge=1)
    risk_flag: Literal["NORMAL", "SUSPICIOUS", "CARROUSEL"] = "NORMAL"

    @field_validator("from_gstin", "to_gstin")
    @classmethod
    def _validate_gstin(cls, v: str) -> str:
        if not _GSTIN_RE.match(v):
            raise ValueError(f"Invalid GSTIN: {v!r}")
        return v


class RawCarouselGSTEntity(BaseModel):
    """Lightweight GSTEntity for the synthetic ring (no Company back-link required)."""

    model_config = ConfigDict(extra="forbid")

    gstin: str
    name: str
    pan: str
    registration_date: date
    is_cancelled: bool = False
    cancellation_date: date | None = None  # PRD §4.4 P10 — cancelled GSTIN still claiming ITC
    taxpayer_type: Literal["regular", "composition", "casual", "sez", "isd"] = "regular"
    aggregate_turnover: float = Field(default=0.0, ge=0.0)
    tax_paid_ytd: float = Field(default=0.0, ge=0.0)
    is_missing_trader: bool = False  # tax_paid/itc_generated < 0.05 per PRD §4.4 P9
    # Day-23: optional CIN back-link, used by P12 (multi-hop ITC + director overlap)
    cin: str | None = None


class CarouselDirectorOverlap(BaseModel):
    """Day-23 P12 evidence: shared directors connecting ring GSTINs to outside CINs."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    shared_directors: list[dict] = Field(default_factory=list)


class ITCCarouselSeed(BaseModel):
    """ITC carousel ring reconstructed from public DGGI Zonal Unit press
    release pattern (PRD §10 Day 6 + §14 demo). Real amount / zone /
    sector / period; company names redacted by DGGI per active-
    investigation protocol."""

    model_config = ConfigDict(extra="forbid")

    ring_id: str
    description: str = ""
    gst_entities: list[RawCarouselGSTEntity]
    itc_claims: list[RawITCClaim]
    edges: list[RawClaimsITCFrom]
    director_overlap: CarouselDirectorOverlap | None = None
    # Lineage attribution — populated from the source CBIC press release,
    # surfaced on the /sources page. Optional so legacy fixtures still
    # load; the weekly DGGI refresh job always writes these fields.
    source_url: str | None = None
    source_type: str | None = None
    dggi_zone: str | None = None
    redaction_status: str | None = None
    entity_disclosure: str | None = None
    verified_date: str | None = None


def load_itc_carousel(path: Path | None = None) -> ITCCarouselSeed:
    path = path or SEEDS_ROOT / "itc_carousel" / "ring.json"
    return ITCCarouselSeed.model_validate(json.loads(path.read_text(encoding="utf-8")))

"""/upload/* — PRD §10 Day 16.

Three endpoints that fold user-supplied evidence into the per-CIN overlay so
the next /analyse call sees it merged onto the FixtureSource bundle:

  POST /upload/financials/{cin}  — multipart PDF (AOC-4). pdfplumber parses
                                    one RawFinancialStatement + PDFForensics.
  POST /upload/gst/{cin}          — JSON RawGSTEntity row.
  POST /upload/bank/{cin}         — JSON {credits_total: float} bank summary.

The Day-16 contract is intentionally minimal: parse, validate, stash. The
RiskScorer rebuilds its evidence chain from the merged bundle, so we don't
re-score here.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.api.upload_store import get_upload_store
from backend.app.api.validators import CIN_PATH
from backend.app.ingest.data_confidence import compute_data_confidence
from backend.app.ingest.gst import RawGSTEntity, pan_from_gstin
from backend.app.ingest.schemas import CompanyBundle, RawFinancialStatement
from backend.app.ingest.sources import FixtureSource
from backend.app.parse.pdf_parser import parse_financial_pdf

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/upload",
    tags=["upload"],
)

_fixture_source = FixtureSource()


class BankCreditsIn(BaseModel):
    """Body for /upload/bank — bank-statement reconstruction summary."""

    model_config = ConfigDict(extra="forbid")
    credits_total: float = Field(ge=0.0)


class UploadAck(BaseModel):
    """Generic response for upload acceptance."""

    cin: str
    accepted: bool
    detail: str
    extra: dict = Field(default_factory=dict)


class UploadPreviewState(BaseModel):
    """Snapshot of overlay coverage that drives the DC ladder (PRD §7.1)."""

    n_financials: int
    has_gst_upload: bool
    has_bank_upload: bool


class UploadPreview(BaseModel):
    """Day-21 DC improvement preview — what each upload would do to DC."""

    cin: str
    current_data_confidence: int
    state: UploadPreviewState
    if_financials_added: int
    if_gst_added: int
    if_bank_added: int


async def _require_known_cin(cin: str) -> None:
    bundle = await _fixture_source.fetch_bundle(cin)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CIN {cin} not registered in source-of-truth — upload after MCA lookup",
        )


def _project_dc(bundle: CompanyBundle, *, add_financial_year: bool = False,
                add_gst: bool = False, add_bank: bool = False) -> int:
    """Hypothetical DC after one upload — mutates a bundle copy, never the real one."""
    fs_list = list(bundle.financials)
    if add_financial_year:
        existing_years = {f.year for f in fs_list}
        next_year = (max(existing_years) + 1) if existing_years else 2024
        # compute_data_confidence only counts financial rows — all non-key
        # fields default to zero on RawFinancialStatement, so a stub row is fine.
        fs_list = fs_list + [RawFinancialStatement(
            cin=bundle.company.cin, year=next_year,
        )]
    projected = bundle.model_copy(update={
        "financials": fs_list,
        "has_gst_upload": bundle.has_gst_upload or add_gst,
        "has_bank_upload": bundle.has_bank_upload or add_bank,
    })
    return compute_data_confidence(projected)


@router.get("/{cin}/preview", response_model=UploadPreview)
async def upload_preview(cin: CIN_PATH) -> UploadPreview:
    """PRD §10 Day-21 — show the analyst what each upload would do to DataConfidence."""
    bundle = await _fixture_source.fetch_bundle(cin)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CIN {cin} not registered in source-of-truth",
        )
    overlay = get_upload_store().get(cin)
    merged = overlay.merge_into(bundle)
    return UploadPreview(
        cin=cin,
        current_data_confidence=compute_data_confidence(merged),
        state=UploadPreviewState(
            n_financials=len(merged.financials),
            has_gst_upload=merged.has_gst_upload,
            has_bank_upload=merged.has_bank_upload,
        ),
        if_financials_added=_project_dc(merged, add_financial_year=True),
        if_gst_added=_project_dc(merged, add_gst=True),
        if_bank_added=_project_dc(merged, add_bank=True),
    )


@router.post("/financials/{cin}", response_model=UploadAck)
async def upload_financials(cin: CIN_PATH, file: UploadFile = File(...)) -> UploadAck:
    """Accept an AOC-4 PDF and overlay it onto the fixture bundle."""
    await _require_known_cin(cin)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .pdf uploads accepted for /upload/financials",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty PDF body",
        )
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        try:
            statement, forensics = parse_financial_pdf(tmp_path, cin_override=cin)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PDF parse failed: {exc}",
            ) from exc
        except Exception as exc:
            # pdfplumber / pdfminer raise their own exception hierarchies
            # (PdfminerException, PDFSyntaxError, etc.) that don't inherit
            # from ValueError. Treat any parser failure on user-uploaded
            # bytes as a 422 — the only alternative is a 500 the client
            # can't act on. Log + surface the message so the analyst sees
            # what's wrong with their PDF.
            logger.warning("PDF parse failed on /upload/financials/%s: %s", cin, exc)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PDF parse failed: {exc.__class__.__name__}: {exc}",
            ) from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete temp PDF %s", tmp_path)

    get_upload_store().upsert_financials(cin, statement, forensics)
    return UploadAck(
        cin=cin,
        accepted=True,
        detail=f"AOC-4 FY{statement.year} parsed",
        extra={
            "year": statement.year,
            "revenue": statement.revenue,
            "pdf_creation_software": forensics.pdf_creation_software,
            "pdf_metadata_anomaly": forensics.creation_mod_gap_days is not None
                                    and forensics.creation_mod_gap_days < 3,
        },
    )


@router.post("/gst/{cin}", response_model=UploadAck)
async def upload_gst(cin: CIN_PATH, payload: dict) -> UploadAck:
    """Accept a GST entity payload and overlay it for the next /analyse call.

    Expected JSON shape (F6 fix — LOCAL_TEST_REPORT.md §F6):

        {
          "gstin": "27AAACX1234A1Z5",        # required — 15-char GSTIN
          "pan": "AAACX1234A",                # optional — derived from GSTIN[2:12] if omitted
          "registration_date": "2019-04-01",  # optional — defaults to today
          "is_cancelled": false,              # optional
          "taxpayer_type": "regular",         # optional — one of regular/composition/casual/sez/isd
          "aggregate_turnover": 5200000.0,    # optional — annual turnover INR
          "tax_paid_ytd": 312000.0            # optional — tax remitted YTD INR
        }

    `cin` is taken from the URL path; sending a different cin in the body
    returns 400. The intuitive `{gstin, aggregate_turnover}` minimal form
    now works because `pan` is auto-derived and `registration_date`
    defaults — both used to be required.
    """
    await _require_known_cin(cin)
    payload = dict(payload)  # tolerate Pydantic-strict callers
    payload.setdefault("cin", cin)
    payload.setdefault("registration_date", date.today().isoformat())
    gstin = payload.get("gstin")
    if isinstance(gstin, str) and "pan" not in payload:
        try:
            payload["pan"] = pan_from_gstin(gstin)
        except ValueError:
            # Let Pydantic surface the precise GSTIN validation message below.
            pass
    try:
        entity = RawGSTEntity.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"GST payload validation failed: {exc.errors()}",
        ) from exc
    if entity.cin != cin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payload CIN {entity.cin} does not match path CIN {cin}",
        )
    get_upload_store().upsert_gst(cin, entity)
    return UploadAck(
        cin=cin,
        accepted=True,
        detail=f"GST entity {entity.gstin} overlaid",
        extra={"aggregate_turnover": entity.aggregate_turnover},
    )


@router.post("/bank/{cin}", response_model=UploadAck)
async def upload_bank(cin: CIN_PATH, payload: BankCreditsIn) -> UploadAck:
    """Accept a bank-reconstruction summary {credits_total}."""
    await _require_known_cin(cin)
    get_upload_store().upsert_bank(cin, payload.credits_total)
    return UploadAck(
        cin=cin,
        accepted=True,
        detail="Bank credits total overlaid",
        extra={"credits_total": payload.credits_total},
    )

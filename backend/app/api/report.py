"""/report/{cin} — PDF export of the dual-output payload (PRD §10 Day 19).

reportlab builds a single-page A4 report carrying:

  - UUID + UTC timestamp (audit trail; PRD §10 Day-19 verbatim)
  - Company CIN + PRD §7.1 dual-output block (score, band, DC, override,
    ensemble disagreement, propagation band)
  - Per-module Tier-1 score breakdown
  - Evidence chain (truncated to fit the page; full chain available in
    /analyse/{cin}/provenance)
  - PRD §13 disclaimer block at the foot

The report inherits the same protected status as /analyse: callers must
present a valid JWT. No PDFs leak unauthenticated.
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.app.api.upload_store import get_upload_store
from backend.app.api.validators import CIN_PATH
from backend.app.auth.deps import require_roles
from backend.app.ingest.benchmarks import BSEFixtureBenchmark
from backend.app.ingest.composite import CompositeCompanySource
from backend.app.ingest.mca_public import MCAPublicScraper
from backend.app.ingest.mca_public_playwright import MCAPublicPlaywrightFetcher
from backend.app.ingest.nclt import NCLTFixtureSource
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource
from backend.app.scorer import ScoringContext, score

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/report",
    tags=["report"],
    dependencies=[Depends(require_roles(
        "credit_officer", "investigator", "auditor", "admin"
    ))],
)

# Phase-A free-source wiring — see analyse.py:42 for the rationale.
_company_source = CompositeCompanySource(
    secondary=MCAPublicScraper(fetcher=MCAPublicPlaywrightFetcher()),
)
_benchmark_source = BSEFixtureBenchmark()
_nclt_source = NCLTFixtureSource()
_wilful_source = WilfulDefaulterFixtureSource()


DISCLAIMER = (
    "DISCLAIMER. This automated risk report is generated from public-record "
    "ingestion (MCA21, CERSAI, NCLT) plus user-uploaded evidence. The "
    "fraud_risk_score is a Tier-1 weighted aggregate over deterministic "
    "modules per PRD §7.2 and is not a regulatory finding. Override floors "
    "(PRD §7.3) reflect NCLT/CIRP or wilful-defaulter registrations and do "
    "not imply a finding of fraud against any individual. Calibrated "
    "probabilities and conformal intervals are produced on out-of-fold data "
    "and inherit the limitations of the labelled training set. Use "
    "alongside, not in place of, human due-diligence."
)


async def _build_scoring_context(overlay):
    return ScoringContext(
        benchmarks=await _benchmark_source.fetch_all(),
        nclt=await _nclt_source.fetch_all(),
        wilful=await _wilful_source.fetch_all(),
        gst_entity=overlay.gst_entity,
        bank_credits_total=overlay.bank_credits_total,
    )


def _render_pdf(report_dict: dict, report_uuid: str, generated_at: datetime) -> bytes:
    """Build the A4 PDF body. Returns the raw bytes (caller streams them)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Sentinel-G — {report_dict['cin']}",
        author="Sentinel-G",
    )
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=colors.grey)
    disclaimer_style = ParagraphStyle(
        "disclaimer", parent=body, fontSize=7.5, leading=9.5,
        textColor=colors.grey, alignment=4,
    )

    flow = []
    flow.append(Paragraph("Sentinel-G — Risk Report", h1))
    flow.append(Paragraph(
        f"Report ID: <b>{report_uuid}</b><br/>"
        f"Generated (UTC): <b>{generated_at.isoformat(timespec='seconds')}</b><br/>"
        f"Subject CIN: <b>{report_dict['cin']}</b>",
        small,
    ))
    flow.append(Spacer(1, 6 * mm))

    flow.append(Paragraph("Risk profile (PRD §7.1)", h2))
    table_rows = [
        ["fraud_risk_score", f"{report_dict['fraud_risk_score']:.1f}"],
        ["risk_band", report_dict["risk_band"]],
        ["data_confidence", f"{report_dict['data_confidence']}%"],
        ["override_applied", "yes (NCLT/WD)" if report_dict["override_applied"] else "no"],
        ["ensemble_disagreement_flag", "yes" if report_dict["ensemble_disagreement_flag"] else "no"],
        ["propagation_band", f"{report_dict['propagation_band']} ({report_dict['propagation_score']:.1f})"],
        ["p_fraud_calibrated", str(report_dict["p_fraud_calibrated"])],
        ["p_fraud_interval", str(report_dict["p_fraud_interval"])],
    ]
    table = Table(table_rows, colWidths=[55 * mm, 110 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flow.append(table)
    flow.append(Spacer(1, 5 * mm))

    flow.append(Paragraph("Per-module Tier-1 contributions", h2))
    module_rows = [["module", "score"]] + [
        [k, f"{v:.1f}"] for k, v in sorted(
            report_dict["module_breakdown"].items(),
            key=lambda kv: -kv[1],
        )
    ]
    mod_table = Table(module_rows, colWidths=[55 * mm, 30 * mm])
    mod_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    flow.append(mod_table)
    flow.append(Spacer(1, 5 * mm))

    flow.append(Paragraph(
        f"Evidence chain ({len(report_dict['evidence_chain'])} signals)", h2,
    ))
    for sig in report_dict["evidence_chain"][:12]:
        flow.append(Paragraph(
            f"<b>[{sig['severity']}] {sig['signal_type']}</b> "
            f"<font color='grey'>· {sig['module_name']} · "
            f"+{sig['score_contribution']:.1f}</font>",
            body,
        ))
        flow.append(Paragraph(sig["evidence_string"], body))
        flow.append(Spacer(1, 2 * mm))
    if len(report_dict["evidence_chain"]) > 12:
        flow.append(Paragraph(
            f"… {len(report_dict['evidence_chain']) - 12} additional signals "
            f"available via /analyse/{report_dict['cin']}/provenance.",
            small,
        ))

    flow.append(Spacer(1, 6 * mm))
    flow.append(Paragraph(DISCLAIMER, disclaimer_style))

    doc.build(flow)
    return buf.getvalue()


@router.get("/{cin}")
async def get_report(
    cin: CIN_PATH,
) -> StreamingResponse:
    """Render the PRD §7.1 payload for {cin} as a PDF.

    RBAC: all four authenticated roles can download a dossier — credit
    officers need the PDF for their borrower compliance file, auditors for
    sample reviews, investigators / admin for case files. The frontend
    Login persona description (credit_officer access includes "Reports")
    is the source of truth.
    """
    bundle = await _company_source.fetch_bundle(cin)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CIN {cin} not resolvable from MCA21 or fixture source",
        )
    overlay = get_upload_store().get(cin)
    bundle = overlay.merge_into(bundle)
    ctx = await _build_scoring_context(overlay)
    report = await score(bundle, ctx)
    report_dict = report.to_dict()

    report_uuid = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc)
    pdf_bytes = _render_pdf(report_dict, report_uuid, generated_at)
    filename = f"sentinel-g-{cin}-{report_uuid[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-Id": report_uuid,
            "X-Report-Generated-At": generated_at.isoformat(timespec="seconds"),
        },
    )

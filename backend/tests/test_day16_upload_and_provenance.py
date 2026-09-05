"""Tests for Day-16 surfaces: /upload/*, /analyse/{cin}/provenance,
belief-propagation lift in /analyse (PRD §10 Day 16)."""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.analyse import router as analyse_router
from backend.app.api.upload import router as upload_router
from backend.app.api.upload_store import get_upload_store


def _make_pdf_bytes(body_lines: list[str]) -> bytes:
    """Build a tiny ReportLab PDF in memory for /upload/financials tests."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    y = 800
    for line in body_lines:
        c.drawString(40, y, line)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def client() -> TestClient:
    from backend.tests.conftest import bypass_auth
    app = FastAPI()
    app.include_router(analyse_router)
    app.include_router(upload_router)
    bypass_auth(app)  # F4: /analyse now requires get_current_user
    # Each test starts from a clean overlay state.
    get_upload_store().reset()
    return TestClient(app)


def test_upload_financials_accepts_known_cin(client: TestClient) -> None:
    """PRD §10 Day-16: 'Three uploads ingest end-to-end.'"""
    pdf_bytes = _make_pdf_bytes([
        "CIN: U14101MH2019PTC298765",
        "Financial Year: 2024",
        "Revenue from operations: 5,200,000",
        "Total assets: 7,800,000",
    ])
    resp = client.post(
        "/upload/financials/U14101MH2019PTC298765",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["extra"]["year"] == 2024
    assert body["extra"]["revenue"] == 5_200_000.0


def test_upload_financials_rejects_unknown_cin(client: TestClient) -> None:
    pdf_bytes = _make_pdf_bytes(["CIN: U99999XX9999PTC999999", "Financial Year: 2024"])
    resp = client.post(
        "/upload/financials/U99999XX9999PTC999999",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 404


def test_upload_financials_rejects_non_pdf(client: TestClient) -> None:
    resp = client.post(
        "/upload/financials/U14101MH2019PTC298765",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_gst_stores_overlay_and_flows_into_analyse(client: TestClient) -> None:
    """Day-16: '/analyse on uploaded CIN merges into the report.'

    XYZ Garments has revenue ~1.2 cr in FY23. We upload a GST entity with
    way-off turnover so M2 Check #1 fires."""
    gst_payload = {
        "gstin": "27AAACX1234A1Z5",
        "pan": "AAACX1234A",
        "cin": "U14101MH2019PTC298765",
        "registration_date": "2019-04-01",
        "is_cancelled": False,
        "taxpayer_type": "regular",
        "aggregate_turnover": 100_000.0,    # absurdly below P&L revenue
        "tax_paid_ytd": 5_000.0,
    }
    resp = client.post("/upload/gst/U14101MH2019PTC298765", json=gst_payload)
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    analyse_resp = client.get("/analyse/U14101MH2019PTC298765")
    assert analyse_resp.status_code == 200
    body = analyse_resp.json()
    sig_types = {s["signal_type"] for s in body["evidence_chain"]}
    assert "CROSS_STMT_REVENUE_VS_GST" in sig_types


def test_upload_bank_stores_overlay_and_flows_into_analyse(client: TestClient) -> None:
    # XYZ Garments FY23 revenue = 9.68 cr. We upload bank credits at 25 cr
    # so the 20% Module 2 #7 threshold is comfortably exceeded.
    resp = client.post(
        "/upload/bank/U14101MH2019PTC298765",
        json={"credits_total": 250_000_000.0},
    )
    assert resp.status_code == 200

    body = client.get("/analyse/U14101MH2019PTC298765").json()
    sig_types = {s["signal_type"] for s in body["evidence_chain"]}
    assert "CROSS_STMT_BANK_VS_REVENUE" in sig_types
    # Per PRD §7.1 ladder, bank upload alone (no GST) keeps DC at 65.
    # Verifies the upload didn't *demote* DC below the fixture baseline.
    assert body["data_confidence"] >= 65


def test_upload_bank_rejects_negative_credits(client: TestClient) -> None:
    resp = client.post(
        "/upload/bank/U14101MH2019PTC298765",
        json={"credits_total": -1.0},
    )
    assert resp.status_code == 422


def test_upload_gst_mismatched_cin_in_body(client: TestClient) -> None:
    gst_payload = {
        "gstin": "27AAACX1234A1Z5",
        "pan": "AAACX1234A",
        "cin": "U27101MH2010PTC215432",  # different from path
        "registration_date": "2019-04-01",
    }
    resp = client.post("/upload/gst/U14101MH2019PTC298765", json=gst_payload)
    assert resp.status_code == 400


def test_provenance_returns_evidence_graph(client: TestClient) -> None:
    """PRD §10 Day-16: provenance endpoint returns FraudSignal + TRIGGERED_BY."""
    resp = client.get("/analyse/U45201MH2005PTC155294/provenance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cin"] == "U45201MH2005PTC155294"
    assert body["signal_count"] >= 5
    assert {"signal_id", "signal_type", "severity", "module_name",
            "score_contribution", "evidence_string"} <= set(body["signals"][0].keys())
    # Every TRIGGERED_BY edge must point back to a signal_id in the signals list.
    signal_ids = {s["signal_id"] for s in body["signals"]}
    for edge in body["triggered_by"]:
        assert edge["signal_id"] in signal_ids
        assert "label" in edge


def test_provenance_export_downloads_json(client: TestClient) -> None:
    resp = client.get("/analyse/U45201MH2005PTC155294/provenance/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "sentinel-g-U45201MH2005PTC155294-provenance.json" in resp.headers["content-disposition"]
    body = resp.json()
    assert body["cin"] == "U45201MH2005PTC155294"
    assert body["signal_count"] >= 5

def test_analyse_payload_surfaces_propagation_band(client: TestClient) -> None:
    body = client.get("/analyse/U45201MH2005PTC155294").json()
    assert "propagation_band" in body
    assert "propagation_score" in body
    assert body["propagation_band"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    # IL&FS is a CRITICAL seed; its propagation_band cannot demote below its own band.
    assert body["propagation_band"] == "CRITICAL"


def test_provenance_unknown_cin_returns_404(client: TestClient) -> None:
    resp = client.get("/analyse/U99999XX9999PTC999999/provenance")
    assert resp.status_code == 404


# --- Day-21: /upload/{cin}/preview ----------------------------------------
def test_upload_preview_shows_current_dc_and_projections(client: TestClient) -> None:
    """PRD §10 Day-21: 'Upload Portal with DataConfidence improvement preview.'

    XYZ Garments fixture ships 2+ years FS with no GST/bank uploads, so the
    baseline DC is 65. Adding GST should project to 80; adding bank only
    keeps it at 65 (per PRD §7.1 ladder — bank requires GST to register)."""
    resp = client.get("/upload/U14101MH2019PTC298765/preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cin"] == "U14101MH2019PTC298765"
    assert body["current_data_confidence"] == 65
    assert body["state"]["has_gst_upload"] is False
    assert body["state"]["has_bank_upload"] is False
    assert body["state"]["n_financials"] >= 2
    assert body["if_gst_added"] == 80
    assert body["if_bank_added"] == 65         # bank alone — no DC bump per ladder
    assert body["if_financials_added"] == 65   # already 2+ years; tier capped


def test_upload_preview_reflects_existing_overlay(client: TestClient) -> None:
    """A GST upload in this session should change subsequent preview projections."""
    client.post("/upload/gst/U14101MH2019PTC298765", json={
        "gstin": "27AAACX1234A1Z5",
        "pan": "AAACX1234A",
        "cin": "U14101MH2019PTC298765",
        "registration_date": "2019-04-01",
        "is_cancelled": False,
        "taxpayer_type": "regular",
        "aggregate_turnover": 1_200_000.0,
        "tax_paid_ytd": 50_000.0,
    })
    body = client.get("/upload/U14101MH2019PTC298765/preview").json()
    assert body["current_data_confidence"] == 80
    assert body["state"]["has_gst_upload"] is True
    # GST is in; bank upload is now the next ladder step (80 -> 92).
    assert body["if_bank_added"] == 92


def test_upload_preview_unknown_cin_returns_404(client: TestClient) -> None:
    resp = client.get("/upload/U99999XX9999PTC999999/preview")
    assert resp.status_code == 404

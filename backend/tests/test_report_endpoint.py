"""Tests for /report/{cin} (PRD §10 Day 19).

PDF is produced via reportlab; we don't parse it back, we just verify shape,
the auth gate, UUID + timestamp response headers, and the per-CIN content
length is non-trivial.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.report import router


def _public_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_anonymous_call_is_public() -> None:
    """Public demo contract: reports are available without JWT login."""
    with _public_client() as c:
        resp = c.get("/report/U45201MH2005PTC155294")
    assert resp.status_code == 200


def test_report_returns_pdf_with_audit_headers() -> None:
    with _public_client() as c:
        resp = c.get("/report/U45201MH2005PTC155294")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["x-report-id"]
    assert resp.headers["x-report-generated-at"]
    # PDF magic prefix
    assert resp.content[:4] == b"%PDF"
    # Non-trivial payload — at least a few KB for a real report.
    assert len(resp.content) > 2000


def test_content_disposition_attachment_with_cin() -> None:
    with _public_client() as c:
        resp = c.get("/report/U45201MH2005PTC155294")
    cd = resp.headers["content-disposition"]
    assert cd.startswith('attachment;')
    assert "U45201MH2005PTC155294" in cd


def test_unknown_cin_returns_404() -> None:
    with _public_client() as c:
        resp = c.get("/report/U99999XX9999PTC999999")
    assert resp.status_code == 404


def test_two_reports_have_distinct_uuids() -> None:
    """UUID + timestamp invariant: each request gets a fresh audit ID."""
    with _public_client() as c:
        a = c.get("/report/U45201MH2005PTC155294")
        b = c.get("/report/U45201MH2005PTC155294")
    assert a.headers["x-report-id"] != b.headers["x-report-id"]

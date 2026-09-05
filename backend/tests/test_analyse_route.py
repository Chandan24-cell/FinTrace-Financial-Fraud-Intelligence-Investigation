"""Tests for /analyse endpoint (PRD §10 Day 15 + §7.1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.api.analyse import router
from backend.tests.conftest import bypass_auth


def _client() -> TestClient:
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    bypass_auth(app)  # F4: /analyse now requires get_current_user
    return TestClient(app)


def test_ilfs_returns_full_dual_output() -> None:
    """PRD §10 Day-15 acceptance line."""
    with _client() as c:
        resp = c.get("/analyse/U45201MH2005PTC155294")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "cin", "fraud_risk_score", "risk_band", "p_fraud_calibrated",
        "p_fraud_interval", "data_confidence", "ensemble_disagreement_flag",
        "evidence_chain", "module_breakdown", "override_applied",
    ):
        assert key in body, f"missing PRD §7.1 key: {key}"
    assert body["risk_band"] == "CRITICAL"
    assert body["fraud_risk_score"] >= 75.0
    assert body["override_applied"] is True
    assert body["ensemble_disagreement_flag"] is True


def test_unknown_cin_returns_404() -> None:
    with _client() as c:
        resp = c.get("/analyse/U99999XX9999PTC999999")
    assert resp.status_code == 404


def test_clean_company_returns_low_band() -> None:
    with _client() as c:
        resp = c.get("/analyse/U14101MH2019PTC298765")
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_band"] in {"LOW", "MEDIUM"}
    assert body["override_applied"] is False

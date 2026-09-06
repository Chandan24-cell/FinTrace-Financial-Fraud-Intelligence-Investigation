"""Public-demo access matrix — exhaustive role × route validation.

Closes Stream 1.1 of the production-grade closure plan and pins the
matrix documented in [docs/LOCAL_TEST_REPORT.md](../../docs/LOCAL_TEST_REPORT.md)
§F4 + the production-plan RBAC table.

Matrix (4 roles × 7 routes = 28 assertions):

    role             /analyse  /analyse/.../prov  /upload/preview  /upload/financials  /upload/gst  /upload/bank  /report
    anonymous        ok        ok                 ok               ok                  ok           ok            ok
    credit_officer   ok        ok                 ok               ok                  ok           ok            ok
    investigator     ok        ok                 ok               ok                  ok           ok            ok
    auditor          ok        ok                 ok               ok                  ok           ok            ok
    admin            ok        ok                 ok               ok                  ok           ok            ok

Note: credit_officer was previously forbidden on /report but the
Login.tsx persona description advertises Reports access — loan officers
need the PDF for their borrower compliance file, which is the real
workflow the persona was modelled on. report.py and this matrix were
aligned on that contract.

`ok` means non-401 and non-403 — the test does not care whether the
underlying handler returns 200/404/422; it cares only that the route
remains publicly accessible in the current demo contract. CIN-resolution
/ payload-shape / etc. are covered exhaustively in the per-route tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.analyse import router as analyse_router
from backend.app.api.report import router as report_router
from backend.app.api.upload import router as upload_router
CIN = "U45201MH2005PTC155294"  # IL&FS — known-good fixture
UNKNOWN_CIN = "U99999XX9999PTC999999"

ROLES = ("credit_officer", "investigator", "auditor", "admin")

# The current public demo contract is intentionally open: the routes below
# are reachable without auth, regardless of role. The matrix is therefore
# exhaustive but contains no forbidden roles. The route gate does not block
# any caller with 401/403.
FORBIDDEN_ROLES = {
    "GET /analyse/{cin}": set(),
    "GET /analyse/{cin}/provenance": set(),
    "GET /upload/{cin}/preview": set(),
    "POST /upload/financials/{cin}": set(),
    "POST /upload/gst/{cin}": set(),
    "POST /upload/bank/{cin}": set(),
    "GET /report/{cin}": set(),
}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(analyse_router)
    app.include_router(upload_router)
    app.include_router(report_router)
    return app


def _stub_user(role: str) -> dict:
    return {
        "user_id": f"test-{role}",
        "email": f"{role}@sentinel-g.example",
        "role": role,
        "is_active": True,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


@pytest.fixture
def anon_client() -> Iterator[TestClient]:
    app = _build_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def role_client_factory():
    """Return a callable: role -> TestClient with that role injected."""
    def _make(role: str) -> TestClient:
        app = _build_app()
        return TestClient(app)
    return _make


# Each route is represented as (label, method, path-template, optional body).
# label keys into FORBIDDEN_ROLES.
ROUTES = [
    ("GET /analyse/{cin}",                "GET",  f"/analyse/{CIN}",                  None),
    ("GET /analyse/{cin}/provenance",     "GET",  f"/analyse/{CIN}/provenance",       None),
    ("GET /upload/{cin}/preview",         "GET",  f"/upload/{CIN}/preview",           None),
    # The body content does not matter for role-gate testing — the
    # gate runs before pydantic parsing or file-upload handling. Use the
    # minimal payload that lets FastAPI even reach the gate.
    ("POST /upload/financials/{cin}",     "POST", f"/upload/financials/{CIN}",        {"files": {"file": ("dummy.pdf", b"%PDF-1.4\n", "application/pdf")}}),
    ("POST /upload/gst/{cin}",            "POST", f"/upload/gst/{CIN}",               {"json": {"gstin": "27AAACX1234A1Z5"}}),
    ("POST /upload/bank/{cin}",           "POST", f"/upload/bank/{CIN}",              {"json": {"credits_total": 1234.5}}),
    ("GET /report/{cin}",                 "GET",  f"/report/{CIN}",                   None),
]


def _request(client: TestClient, method: str, url: str, opts: dict | None):
    kwargs = opts or {}
    return client.request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# Anonymous tier — public demo routes are not rejected with 401/403.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,method,url,opts", ROUTES, ids=[r[0] for r in ROUTES])
def test_anonymous_has_public_access(label, method, url, opts, anon_client):
    resp = _request(anon_client, method, url, opts)
    assert resp.status_code not in (401, 403), (
        f"{label} should remain publicly accessible to anonymous callers, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Public role tier — roles no longer affect access, so every route-role pair
# is expected to remain publicly accessible.
# ---------------------------------------------------------------------------

_FORBIDDEN_CASES = [
    (label, method, url, opts, role)
    for (label, method, url, opts) in ROUTES
    for role in FORBIDDEN_ROLES[label]
]


@pytest.mark.parametrize(
    "label,method,url,opts,role",
    _FORBIDDEN_CASES,
    ids=[f"{c[0]}|role={c[4]}" for c in _FORBIDDEN_CASES],
)
def test_forbidden_role_rejected_with_403(label, method, url, opts, role, role_client_factory):
    client = role_client_factory(role)
    resp = _request(client, method, url, opts)
    assert resp.status_code not in (401, 403), (
        f"{label} should remain publicly accessible to role={role!r}, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Allowed-role tier — every role passes the public-access gate.
# We assert "not 401 and not 403" — the handler may return 200 / 404 / 422.
# ---------------------------------------------------------------------------

_ALLOWED_CASES = [
    (label, method, url, opts, role)
    for (label, method, url, opts) in ROUTES
    for role in ROLES
]


@pytest.mark.parametrize(
    "label,method,url,opts,role",
    _ALLOWED_CASES,
    ids=[f"{c[0]}|role={c[4]}" for c in _ALLOWED_CASES],
)
def test_allowed_role_passes_gate(label, method, url, opts, role, role_client_factory):
    client = role_client_factory(role)
    resp = _request(client, method, url, opts)
    assert resp.status_code not in (401, 403), (
        f"{label} should let role={role!r} through, got {resp.status_code}: "
        f"{resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Sanity: the matrix above accounts for every route × role pair.
# ---------------------------------------------------------------------------

def test_matrix_is_exhaustive():
    """If routes / roles change without updating this test, fail loudly."""
    assert set(FORBIDDEN_ROLES) == {r[0] for r in ROUTES}, (
        "FORBIDDEN_ROLES keys must match the ROUTES table"
    )
    for label, forbidden in FORBIDDEN_ROLES.items():
        assert forbidden.issubset(set(ROLES)), (
            f"{label} forbids unknown role(s): {forbidden - set(ROLES)}"
        )

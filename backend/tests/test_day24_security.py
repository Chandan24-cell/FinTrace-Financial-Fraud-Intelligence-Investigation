"""Day-24 security tests — PRD §10.

Locks the five Day-24 'Done When' conditions:
  - Malformed CIN returns 422 (every public route).
  - CORS allows only the explicit origin list (no wildcards).
  - Rate-limit middleware returns 429 + Retry-After on the 11th req/min.
  - JWT older than `jwt_expiry_hours` is rejected.
  - `scripts/scan_secrets.py` exits 0 on a clean tree (no real secrets
    in git-tracked files).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.api.analyse import router as analyse_router
from backend.app.api.upload import router as upload_router
from backend.app.api.validators import CIN_REGEX
from backend.app.auth.jwt import TokenError, create_access_token, decode_access_token
from backend.app.config import get_settings
from backend.app.middleware.rate_limit import RateLimitMiddleware

ROOT = Path(__file__).resolve().parents[2]


# --- Input sanitisation ---------------------------------------------------
@pytest.fixture
def client_no_rate_limit() -> TestClient:
    """Bare app with the public routers but no rate limit so the CIN-422
    test isn't accidentally throttled by repeated negative cases."""
    from backend.tests.conftest import bypass_auth
    app = FastAPI()
    app.include_router(analyse_router)
    app.include_router(upload_router)
    bypass_auth(app)  # F4: /analyse now requires get_current_user
    return TestClient(app)


@pytest.mark.parametrize("path", [
    "/analyse/not-a-cin",
    "/analyse/U45201MH2005PTC15529",        # 1 char short
    "/analyse/U45201MH2005PTC1552945",      # 1 char long
    "/analyse/u45201mh2005ptc155294",       # lowercase
    "/analyse/X45201MH2005PTC155294",       # bad leading letter
    "/analyse/U45201MH2005PTC155294/provenance",  # this one valid -> 200/404, not 422
])
def test_malformed_cin_returns_422_or_valid_handler(client_no_rate_limit, path) -> None:
    """PRD §10 Day-24 Done When: 'Malformed CIN returns 422.'"""
    resp = client_no_rate_limit.get(path)
    if path.endswith("/U45201MH2005PTC155294/provenance"):
        # Sanity: the *valid* CIN must NOT be 422 — proves the regex isn't
        # over-broad and rejecting legitimate CINs.
        assert resp.status_code != 422, resp.text
    else:
        assert resp.status_code == 422, f"Expected 422 for {path}, got {resp.status_code} ({resp.text})"


@pytest.mark.parametrize("route", [
    "/upload/not-a-cin/preview",
    "/upload/X45201MH2005PTC155294/preview",
])
def test_upload_routes_also_422_on_bad_cin(client_no_rate_limit, route) -> None:
    resp = client_no_rate_limit.get(route)
    assert resp.status_code == 422, resp.text


def test_cin_regex_matches_every_fixture_cin() -> None:
    import re
    fixture_cins = [p.stem for p in (ROOT / "infra" / "seeds" / "companies").glob("*.json")]
    pat = re.compile(CIN_REGEX)
    for cin in fixture_cins:
        assert pat.match(cin), f"Fixture CIN {cin} fails the regex"


# --- Rate limit -----------------------------------------------------------
def test_rate_limit_returns_429_with_retry_after() -> None:
    """PRD §10 Day-24: '10 req/min per JWT.' 11th request -> 429."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit_per_min=10)

    @app.get("/probe")
    async def probe() -> dict:
        return {"ok": True}

    client = TestClient(app)
    for i in range(10):
        r = client.get("/probe")
        assert r.status_code == 200, f"Request #{i+1} unexpectedly throttled: {r.text}"
        assert r.headers.get("X-RateLimit-Limit") == "10"
    r = client.get("/probe")
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1


def test_rate_limit_buckets_per_jwt() -> None:
    """Two different JWT subjects should each get their own 10-req budget."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit_per_min=3)

    @app.get("/probe")
    async def probe() -> dict:
        return {"ok": True}

    client = TestClient(app)
    tok_a = create_access_token("user-a")
    tok_b = create_access_token("user-b")

    # User A exhausts their budget
    for _ in range(3):
        assert client.get("/probe", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 200
    assert client.get("/probe", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 429

    # User B still has a fresh budget
    for _ in range(3):
        assert client.get("/probe", headers={"Authorization": f"Bearer {tok_b}"}).status_code == 200
    assert client.get("/probe", headers={"Authorization": f"Bearer {tok_b}"}).status_code == 429


def test_rate_limit_exempts_health() -> None:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit_per_min=2)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    client = TestClient(app)
    for _ in range(5):
        assert client.get("/health").status_code == 200


# --- CORS lockdown --------------------------------------------------------
def test_cors_default_origin_list_excludes_wildcards() -> None:
    settings = get_settings()
    origins = settings.cors_origin_list
    assert origins, "CORS allowed origins must not be empty"
    assert "*" not in origins, "CORS must not use the wildcard origin"
    # Vite dev server default. We don't pin prod URLs here — those are
    # supplied via the CORS_ALLOWED_ORIGINS env var on deploy.
    assert any("localhost:5173" in o for o in origins)


# --- JWT expiry -----------------------------------------------------------
def test_jwt_expires_after_configured_window() -> None:
    """PRD §10 Day-24: 'JWT expiry 24h.' Verify rejection of stale tokens."""
    settings = get_settings()
    payload = {
        "sub": "test-user",
        "iat": int((datetime.now(timezone.utc) - timedelta(hours=settings.jwt_expiry_hours + 1)).timestamp()),
        "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()),
    }
    stale_token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):
        decode_access_token(stale_token)


def test_jwt_expiry_window_matches_configured_hours() -> None:
    settings = get_settings()
    token = create_access_token("test-user")
    decoded = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    span_hours = (decoded["exp"] - decoded["iat"]) / 3600.0
    # Allow ±1s rounding tolerance.
    assert abs(span_hours - settings.jwt_expiry_hours) < 1e-3


# --- git grep for secrets -------------------------------------------------
def test_scan_secrets_exits_clean_on_current_tree() -> None:
    """PRD §10 Day-24 Done When: 'Zero secrets in git.'"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scan_secrets.py"), "--quiet"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, (
        f"scan_secrets.py found secrets in tracked files:\n{result.stdout}\n{result.stderr}"
    )

"""Auth endpoint tests using FastAPI TestClient with an in-memory user repository fake.

These exercise the FastAPI router, dependency injection, JWT issuance, and the
get_current_user path WITHOUT needing a live Neo4j. Day 2 integration tests against
the real Neo4j go in test_auth_integration.py once Neo4j is running locally.

The PRD §10 Day 2 'Done when: Auth endpoints work' is satisfied by both.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import auth as auth_pkg  # noqa: F401  (ensures pkg import)
from backend.app.auth import repository as repo_mod
from backend.app.auth.hashing import hash_password
from backend.app.main import create_app


# --- in-memory fake -------------------------------------------------------
_STORE: dict[str, dict[str, Any]] = {}


async def _fake_create(driver, email: str, password: str, role: str) -> dict:  # type: ignore[no-untyped-def]
    if any(u["email"] == email for u in _STORE.values()):
        raise repo_mod.UserAlreadyExistsError(email)
    user_id = str(uuid4())
    user = {
        "user_id": user_id,
        "email": email,
        "hashed_password": hash_password(password),
        "role": role,
        "created_at": datetime.now(timezone.utc),
        "is_active": True,
    }
    _STORE[user_id] = user
    return user


async def _fake_get_by_email(driver, email: str):  # type: ignore[no-untyped-def]
    return next((u for u in _STORE.values() if u["email"] == email), None)


async def _fake_get_by_id(driver, user_id: str):  # type: ignore[no-untyped-def]
    return _STORE.get(user_id)


@pytest.fixture(autouse=True)
def _clear_store() -> None:
    _STORE.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Patch the repository functions everywhere they are imported from.
    monkeypatch.setattr("backend.app.auth.repository.create_user", _fake_create)
    monkeypatch.setattr("backend.app.auth.repository.get_user_by_email", _fake_get_by_email)
    monkeypatch.setattr("backend.app.auth.repository.get_user_by_id", _fake_get_by_id)
    monkeypatch.setattr("backend.app.auth.routes.create_user", _fake_create)
    monkeypatch.setattr("backend.app.auth.routes.get_user_by_email", _fake_get_by_email)
    monkeypatch.setattr("backend.app.auth.deps.get_user_by_id", _fake_get_by_id)

    # The get_driver() dependency is called but the fake repo ignores it — patch it
    # so the auth endpoints don't fail with "driver not initialised" when running
    # without a Neo4j connection.
    monkeypatch.setattr("backend.app.auth.routes.get_driver", lambda: None)
    monkeypatch.setattr("backend.app.auth.deps.get_driver", lambda: None)

    return TestClient(create_app(), raise_server_exceptions=False)


def test_register_then_login_then_me(client: TestClient) -> None:
    register = client.post(
        "/auth/register",
        json={"email": "officer@nbfc.in", "password": "secret-pw-12345"},
    )
    assert register.status_code == 201, register.text
    user = register.json()
    assert user["email"] == "officer@nbfc.in"
    assert user["role"] == "credit_officer"
    assert "user_id" in user

    login = client.post(
        "/auth/login",
        json={"email": "officer@nbfc.in", "password": "secret-pw-12345"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"
    assert login.json()["expires_in_seconds"] == 24 * 3600

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "officer@nbfc.in"


def test_register_honors_valid_roles_and_blocks_admin(client: TestClient) -> None:
    # Non-admin roles can be self-selected; admin must be granted out-of-band.
    for role in ("investigator", "auditor", "credit_officer"):
        resp = client.post(
            "/auth/register",
            json={"email": f"user-{role}@nbfc.in", "password": "secret-pw-12345", "role": role},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["role"] == role, (
            f"expected role={role} to be honoured, got {resp.json()['role']}"
        )

    # Admin self-registration is blocked (422 Unprocessable Entity).
    admin_attempt = client.post(
        "/auth/register",
        json={"email": "evil-admin@nbfc.in", "password": "secret-pw-12345", "role": "admin"},
    )
    assert admin_attempt.status_code == 422, (
        f"admin self-registration should be rejected with 422, got {admin_attempt.status_code}"
    )


def test_duplicate_register_returns_409(client: TestClient) -> None:
    payload = {"email": "dup@nbfc.in", "password": "secret-pw-12345", "role": "auditor"}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 201
    second = client.post("/auth/register", json=payload)
    assert second.status_code == 409


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    client.post(
        "/auth/register",
        json={"email": "x@y.in", "password": "right-pw-12345", "role": "investigator"},
    )
    bad = client.post("/auth/login", json={"email": "x@y.in", "password": "wrong-pw-99999"})
    assert bad.status_code == 401


def test_me_requires_auth(client: TestClient) -> None:
    no_auth = client.get("/auth/me")
    assert no_auth.status_code == 401


def test_me_rejects_garbage_token(client: TestClient) -> None:
    bad = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401


def test_register_validates_password_length(client: TestClient) -> None:
    short = client.post(
        "/auth/register",
        json={"email": "a@b.in", "password": "short", "role": "auditor"},
    )
    assert short.status_code == 422


def test_register_validates_email_format(client: TestClient) -> None:
    bad_email = client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "longenoughpw", "role": "auditor"},
    )
    assert bad_email.status_code == 422


def test_create_app_refuses_insecure_jwt_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    # The source-tree default must never reach staging/prod. dev keeps booting
    # with it so local + CI work without any extra setup.
    from backend.app import config as cfg_mod
    from backend.app.main import _INSECURE_JWT_DEFAULT, create_app

    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", _INSECURE_JWT_DEFAULT)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app()

    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setenv("JWT_SECRET", "a-strong-random-secret-of-sufficient-length-xyz")
    create_app()  # must NOT raise once a real secret is supplied

    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

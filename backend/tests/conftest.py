"""Shared pytest fixtures + auth-bypass helper.

PRD §10 Day-19 hardened /analyse + /analyse/{cin}/provenance with
`Depends(get_current_user)` (filed as F4 in docs/LOCAL_TEST_REPORT.md).
Existing tests build their own minimal FastAPI app with just the
analyse router mounted; they don't go through /auth/login to mint a
real JWT. Use `bypass_auth(app)` after `include_router` to stub the
dependency so the tests stay focused on their original assertions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from backend.app.auth.deps import get_current_user

STUB_USER: dict[str, Any] = {
    "user_id": "test-user-0001",
    "email": "test-user@example.com",
    # admin role so role-gated routes (e.g. /report — auditor/investigator/admin;
    # /upload/* — credit_officer/investigator/admin) all pass via the same stub.
    # RBAC-specific tests should override get_current_user with a different role.
    "role": "admin",
    "is_active": True,
    "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
}


def bypass_auth(app: FastAPI) -> FastAPI:
    """Install a no-op get_current_user override on the given FastAPI app."""
    app.dependency_overrides[get_current_user] = lambda: STUB_USER
    return app

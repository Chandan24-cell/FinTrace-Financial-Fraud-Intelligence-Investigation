"""Unit tests for auth primitives — no Neo4j required.

PRD §10 Day 2 acceptance: JWT auth (python-jose) + endpoints work.
Integration tests against the live Neo4j go in test_auth_integration.py.
"""

import time

import pytest

from backend.app.auth.hashing import hash_password, verify_password
from backend.app.auth.jwt import TokenError, create_access_token, decode_access_token


class TestPasswordHashing:
    def test_hash_then_verify_roundtrip(self) -> None:
        plain = "SuperSecret123!"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_verify_rejects_wrong_password(self) -> None:
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("wrong-password", hashed) is False

    def test_two_hashes_of_same_password_differ(self) -> None:
        """bcrypt salts so two hashes of the same password must not collide."""
        p = "same-input"
        assert hash_password(p) != hash_password(p)


class TestJWT:
    def test_encode_then_decode_roundtrip_returns_subject(self) -> None:
        token = create_access_token(subject="user-abc-123")
        payload = decode_access_token(token)
        assert payload["sub"] == "user-abc-123"
        assert payload["exp"] > payload["iat"]

    def test_extra_claims_round_trip(self) -> None:
        token = create_access_token(
            subject="user-x",
            extra_claims={"email": "u@example.com", "role": "investigator"},
        )
        payload = decode_access_token(token)
        assert payload["email"] == "u@example.com"
        assert payload["role"] == "investigator"

    def test_decode_rejects_garbage(self) -> None:
        with pytest.raises(TokenError):
            decode_access_token("not.a.real.token")

    def test_decode_rejects_tampered_token(self) -> None:
        token = create_access_token(subject="u-1")
        tampered = token[:-4] + "AAAA"
        with pytest.raises(TokenError):
            decode_access_token(tampered)

    def test_expired_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Manually crafted past-expiry token must be rejected by python-jose."""
        from jose import jwt as jose_jwt

        from backend.app.config import get_settings

        s = get_settings()
        past_payload = {
            "sub": "u-old",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        stale = jose_jwt.encode(past_payload, s.jwt_secret, algorithm=s.jwt_algorithm)
        with pytest.raises(TokenError):
            decode_access_token(stale)

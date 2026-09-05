"""Per-env rate-limit defaults — Stream 1.2 of the production-grade closure plan.

Source-tree default of `rate_limit_per_min = 0` triggers an env-aware
fallback in Settings._apply_env_defaults: dev=200, staging=120, prod=60.
Explicit env overrides (RATE_LIMIT_PER_MIN=...) still win.
"""

from __future__ import annotations

from backend.app.config import Settings


def _settings(**kw) -> Settings:
    """Construct Settings without loading .env.local — the dev tree has
    RATE_LIMIT_PER_MIN=10 pinned there which would mask the per-env default."""
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_dev_default_is_200():
    s = _settings(app_env="dev")
    assert s.rate_limit_per_min == 200


def test_staging_default_is_120():
    s = _settings(app_env="staging", jwt_secret="x" * 64)
    assert s.rate_limit_per_min == 120


def test_prod_default_is_60():
    s = _settings(app_env="prod", jwt_secret="x" * 64)
    assert s.rate_limit_per_min == 60


def test_explicit_override_wins_over_env_default():
    """Operators pinning a value must always get exactly that value."""
    s = _settings(app_env="prod", rate_limit_per_min=15, jwt_secret="x" * 64)
    assert s.rate_limit_per_min == 15


def test_zero_means_use_default_not_zero():
    """rate_limit_per_min=0 should never deploy — defaults must kick in."""
    s = _settings(app_env="prod", rate_limit_per_min=0, jwt_secret="x" * 64)
    assert s.rate_limit_per_min > 0

"""Prewarm of analytics cache at FastAPI startup — Stream 1.3.

Without prewarm, the first /analyse caller after cold boot pays the
5–10 s build cost (D3 train, D4 fit, D5 load, D6 train, M10 batch).
The lifespan in `backend/app/deps.py` now calls
`prewarm_analytics_cache()` so subsequent requests hit a warm dict.

Test contract: after `prewarm_analytics_cache()` resolves, the module
state in `analytics_cache` reports `built=True` with a non-zero pool.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app import analytics_cache as analytics_cache_module
from backend.app.api.analyse import prewarm_analytics_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    analytics_cache_module.reset_for_tests()
    yield
    analytics_cache_module.reset_for_tests()


def test_prewarm_builds_the_cache_from_fixtures():
    status_before = analytics_cache_module.get_status()
    assert status_before == {"built": False}

    asyncio.run(prewarm_analytics_cache())

    status_after = analytics_cache_module.get_status()
    assert status_after["built"] is True, (
        f"prewarm should populate the cache; got {status_after!r}"
    )
    assert status_after["pool_size"] > 0, (
        "prewarm should ingest at least one fixture bundle"
    )


def test_prewarm_is_idempotent():
    """Calling prewarm twice must not rebuild — second call returns the same cache."""
    asyncio.run(prewarm_analytics_cache())
    pool_after_first = analytics_cache_module.get_status()["pool_size"]
    asyncio.run(prewarm_analytics_cache())
    pool_after_second = analytics_cache_module.get_status()["pool_size"]
    assert pool_after_first == pool_after_second


def test_prewarm_never_raises_even_on_failure(monkeypatch):
    """A broken bundles_provider must not crash the lifespan."""
    async def boom():  # noqa: ANN202
        raise RuntimeError("fixtures unreadable")

    import backend.app.api.analyse as analyse_mod
    monkeypatch.setattr(analyse_mod, "_all_fixture_bundles", boom)
    # Must not raise
    asyncio.run(prewarm_analytics_cache())
    # Cache should remain unbuilt (fall back to lazy build at first request)
    assert analytics_cache_module.get_status() == {"built": False}

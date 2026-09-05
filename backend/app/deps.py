"""Shared async dependencies: Neo4j driver lifecycle, request context."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from neo4j import AsyncDriver, AsyncGraphDatabase

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


_driver: AsyncDriver | None = None
_scheduler = None  # set in lifespan; tests bypass


async def _connect_neo4j() -> AsyncDriver:
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        max_connection_pool_size=50,
    )
    await driver.verify_connectivity()
    return driver


def get_driver() -> AsyncDriver:
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialised. Call inside an async lifespan.")
    return _driver


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan — open Neo4j driver on startup, close on shutdown.

    PRD §10 Day 20 also spawns the ScraperScheduler. The scheduler is
    skip-on-offline by design, so Neo4j outages don't take FastAPI down.
    If the Neo4j connect itself fails (cold demo path), we log + yield
    anyway so the FixtureSource-backed /analyse keeps working.
    """
    global _driver, _scheduler
    settings = get_settings()
    try:
        _driver = await _connect_neo4j()
    except Exception as exc:  # noqa: BLE001 — demo path must still serve
        logger.warning("lifespan: Neo4j connect failed (%s) — running degraded", exc)
        _driver = None

    # Apply schema constraints + indexes on every startup. Idempotent
    # (every statement uses IF NOT EXISTS). Without this, the User.email
    # unique constraint defined in graph/schema.py:23 is never created
    # in the running DB, and duplicate registrations slip through
    # (filed as F3 in docs/LOCAL_TEST_REPORT.md).
    if _driver is not None:
        try:
            from backend.app.graph.schema import apply_schema
            await apply_schema()
        except Exception as exc:  # noqa: BLE001 — still serve if schema fails
            logger.warning("lifespan: apply_schema failed (%s) — constraints may be missing", exc)

    if _driver is not None and settings.scheduler_enabled:
        # Local import keeps the FastAPI cold-start cost off the import graph.
        from backend.app.ingest.scheduler import ScraperScheduler
        _scheduler = ScraperScheduler(driver=_driver)
        await _scheduler.start()

    # Stream 1.3 — pre-warm analytics cache so the first /analyse caller
    # doesn't pay the 5–10 s D3/D4/D5/D6 build cost. Lazy import to keep
    # the import graph free of analyse->deps->analyse cycles.
    try:
        from backend.app.api.analyse import prewarm_analytics_cache
        await prewarm_analytics_cache()
    except Exception as exc:  # noqa: BLE001 — must not crash lifespan
        logger.warning("lifespan: analytics_cache prewarm failed (%s)", exc)

    try:
        yield
    finally:
        if _scheduler is not None:
            await _scheduler.stop()
            _scheduler = None
        if _driver is not None:
            await _driver.close()
            _driver = None

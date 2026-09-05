"""Tests for backend/app/ingest/scheduler.py (PRD §10 Day 20)."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ServiceUnavailable

from backend.app.config import Settings
from backend.app.ingest.nclt import RawNCLTProceeding
from backend.app.ingest.scheduler import (
    PollResult,
    ScraperScheduler,
    poll_cersai_refresh,
    poll_mca21_refresh,
    poll_nclt,
    poll_shared_attributes,
    poll_wilful_defaulter,
)
from backend.app.ingest.sources import MCA21KeyMissingError


def _driver_up() -> AsyncMock:
    """An AsyncMock driver whose verify_connectivity succeeds."""
    drv = AsyncMock()
    drv.verify_connectivity = AsyncMock(return_value=None)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    drv.session = MagicMock(return_value=session_cm)
    return drv


def _driver_down() -> AsyncMock:
    drv = AsyncMock()
    drv.verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("offline"))
    return drv


# --- Skip-when-offline contract -------------------------------------------
@pytest.mark.asyncio
async def test_mca21_skips_when_neo4j_unreachable() -> None:
    src = AsyncMock()
    result = await poll_mca21_refresh(_driver_down(), src)
    assert result.ok is False
    assert "neo4j" in result.detail.lower()
    src.fetch_bundle.assert_not_called()


@pytest.mark.asyncio
async def test_cersai_skips_when_neo4j_unreachable() -> None:
    src = AsyncMock()
    result = await poll_cersai_refresh(_driver_down(), src)
    assert result.ok is False
    src.fetch_all.assert_not_called()


@pytest.mark.asyncio
async def test_nclt_skips_when_neo4j_unreachable() -> None:
    src = AsyncMock()
    result = await poll_nclt(_driver_down(), src)
    assert result.ok is False
    src.fetch_all.assert_not_called()


@pytest.mark.asyncio
async def test_wilful_skips_when_neo4j_unreachable() -> None:
    src = AsyncMock()
    result = await poll_wilful_defaulter(_driver_down(), src)
    assert result.ok is False


@pytest.mark.asyncio
async def test_shared_attribute_skips_when_neo4j_unreachable() -> None:
    result = await poll_shared_attributes(_driver_down())
    assert result.ok is False


# --- MCA21 key-missing tolerance -----------------------------------------
@pytest.mark.asyncio
async def test_mca21_key_missing_is_treated_as_skip_not_failure() -> None:
    drv = _driver_up()
    # Stub the company-list query to return one CIN.
    session = AsyncMock()
    cursor = MagicMock()
    cursor.__aiter__.return_value = iter([{"cin": "U45201MH2005PTC155294"}])
    session.run = AsyncMock(return_value=cursor)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    drv.session = MagicMock(return_value=cm)

    src = AsyncMock()
    src.fetch_bundle = AsyncMock(side_effect=MCA21KeyMissingError())
    result = await poll_mca21_refresh(drv, src)
    assert result.ok is True
    assert "skipped" in result.detail.lower() or "missing" in result.detail.lower()


# --- NCLT happy path with stub source + stub writer ----------------------
@pytest.mark.asyncio
async def test_nclt_happy_path_writes_each_proceeding() -> None:
    drv = _driver_up()
    src = AsyncMock()
    src.fetch_all = AsyncMock(return_value=[
        RawNCLTProceeding(
            case_number="C.P. (IB) 1/MB/2026",
            cin="U45201MH2005PTC155294",
            petition_type="CIRP",
            filing_date=date(2026, 1, 1),
            status="admitted",
            amount_claimed=1_000_000.0,
            bench="NCLT Mumbai",
        ),
    ])

    with patch("backend.app.ingest.scheduler.upsert_nclt_proceeding", new=AsyncMock()) as stub:
        result = await poll_nclt(drv, src)
    assert result.ok is True
    assert result.rows_ingested == 1
    stub.assert_awaited_once()


# --- Scheduler lifecycle --------------------------------------------------
@pytest.mark.asyncio
async def test_scheduler_disabled_setting_skips_start() -> None:
    settings = Settings(scheduler_enabled=False)
    drv = _driver_up()
    sched = ScraperScheduler(driver=drv, settings=settings)
    await sched.start()
    assert sched._tasks == []
    await sched.stop()  # no-op


@pytest.mark.asyncio
async def test_scheduler_start_stop_lifecycle_is_idempotent() -> None:
    settings = Settings(
        scheduler_enabled=True,
        scheduler_mca21_refresh_sec=3600,
        scheduler_cersai_refresh_sec=3600,
        scheduler_nclt_poll_sec=3600,
        scheduler_wilful_poll_sec=3600,
        scheduler_shared_attr_sec=3600,
    )
    drv = _driver_down()  # so the first cycle is a skip, no DB writes
    sched = ScraperScheduler(driver=drv, settings=settings)
    await sched.start()
    assert len(sched._tasks) == 5
    # Re-start should be a no-op.
    await sched.start()
    assert len(sched._tasks) == 5
    # Let the startup cycles run once.
    await asyncio.sleep(0.05)
    await sched.stop()
    assert sched._tasks == []
    # Stop again should be a no-op.
    await sched.stop()


@pytest.mark.asyncio
async def test_scheduler_invokes_on_cycle_callback() -> None:
    """A consumer (ops dashboard / test harness) can observe each cycle."""
    settings = Settings(
        scheduler_enabled=True,
        scheduler_mca21_refresh_sec=3600,
        scheduler_cersai_refresh_sec=3600,
        scheduler_nclt_poll_sec=3600,
        scheduler_wilful_poll_sec=3600,
        scheduler_shared_attr_sec=3600,
    )
    drv = _driver_down()
    seen: list[PollResult] = []
    sched = ScraperScheduler(driver=drv, settings=settings, on_cycle=seen.append)
    await sched.start()
    # Allow the once-on-startup cycles to fire.
    await asyncio.sleep(0.1)
    await sched.stop()
    # Five sources × one startup cycle each — all skipped (Neo4j down) but
    # still observed.
    sources_seen = {r.source for r in seen}
    assert sources_seen == {
        "mca21", "cersai", "nclt", "wilful_defaulter", "shared_attribute",
    }
    assert all(r.ok is False for r in seen)


def test_poll_result_dataclass_shape() -> None:
    r = PollResult(source="x", ok=True, rows_ingested=3, detail="ok")
    assert r.source == "x" and r.rows_ingested == 3

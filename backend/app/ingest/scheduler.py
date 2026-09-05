"""Live source-side polling scheduler (PRD §10 Day 20 + §2.1).

Async polling tasks driven by `asyncio.create_task` from FastAPI's lifespan
context manager. One task per source; each runs its own cadence:

  - MCA21 refresh of known CINs   — every 24h
  - CERSAI refresh of known CINs  — every 24h
  - NCLT cause-list poll          — every 24h
  - Wilful-defaulter poll          — every 30 days (PRD "Monthly refresh")
  - SharedAttribute re-materialise — every 1h

PRD §2.1 forbids Celery/Redis. This is the only background-task pattern the
PRD allows — plain asyncio with idempotent MERGE writes through the existing
graph/writes.py helpers.

Skip-when-offline contract: each task wraps a `driver.verify_connectivity()`
probe; on failure it logs and re-sleeps. FastAPI keeps serving regardless.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from backend.app.config import Settings, get_settings
from backend.app.graph.shared_attribute import materialise_shared_attributes
from backend.app.graph.writes import (
    upsert_charge,
    upsert_nclt_proceeding,
    upsert_wilful_defaulter,
    write_bundle,
)
from backend.app.ingest.cersai import CERSAIFixtureSource
from backend.app.ingest.nclt import NCLTFixtureSource
from backend.app.ingest.sources import MCA21KeyMissingError, MCA21V3Source
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource

logger = logging.getLogger(__name__)


# Source-stamp Cypher. Lets the operator answer "when did we last see new
# data from source X?" without log-grepping.
_STAMP_SOURCE_CYPHER = """
MERGE (s:Source {name: $name})
SET s.last_polled_at = datetime(),
    s.last_status = $status,
    s.rows_ingested = coalesce($rows_ingested, 0)
RETURN s.name AS name
"""


@dataclass(frozen=True)
class PollResult:
    """Outcome of one poll cycle — used by tests + ops dashboards."""

    source: str
    ok: bool
    rows_ingested: int
    detail: str


async def _stamp_source(driver: AsyncDriver, name: str, status: str, rows: int) -> None:
    settings = get_settings()
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            await session.run(_STAMP_SOURCE_CYPHER, name=name, status=status, rows_ingested=rows)
    except (ServiceUnavailable, Neo4jError) as exc:
        logger.warning("scheduler.stamp_source(%s) failed: %s", name, exc)


async def _verify(driver: AsyncDriver) -> bool:
    """Quick connectivity check. Returns True on success, False on any error."""
    try:
        await driver.verify_connectivity()
        return True
    except Exception as exc:  # noqa: BLE001 — any error means "Neo4j down, skip"
        logger.info("scheduler: Neo4j unreachable (%s) — skipping cycle", exc.__class__.__name__)
        return False


# --- Per-source pollers ----------------------------------------------------
async def poll_mca21_refresh(driver: AsyncDriver, source: MCA21V3Source) -> PollResult:
    """Iterate known CINs in Neo4j, re-fetch each via MCA21 V3, MERGE deltas."""
    settings = get_settings()
    if not await _verify(driver):
        return PollResult("mca21", False, 0, "neo4j unreachable")

    rows = 0
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            result = await session.run("MATCH (c:Company) RETURN c.cin AS cin LIMIT 1000")
            cins = [r["cin"] async for r in result]
    except Neo4jError as exc:
        await _stamp_source(driver, "mca21", "error", 0)
        return PollResult("mca21", False, 0, f"company-list query failed: {exc}")

    for cin in cins:
        try:
            bundle = await source.fetch_bundle(cin)
        except MCA21KeyMissingError:
            await _stamp_source(driver, "mca21", "skipped:no-key", 0)
            return PollResult("mca21", True, 0, "MCA21 key missing — skipped")
        except Exception as exc:  # noqa: BLE001 — network / parse robustness
            logger.warning("mca21 fetch failed for %s: %s", cin, exc)
            continue
        if bundle is None:
            continue
        try:
            await write_bundle(driver, bundle)
            rows += 1
        except Neo4jError as exc:
            logger.warning("mca21 write_bundle(%s) failed: %s", cin, exc)

    await _stamp_source(driver, "mca21", "ok", rows)
    return PollResult("mca21", True, rows, f"refreshed {rows} bundles")


async def poll_cersai_refresh(
    driver: AsyncDriver, source: CERSAIFixtureSource,
) -> PollResult:
    """Re-fetch CERSAI charges for known CINs; MERGE deltas."""
    settings = get_settings()
    if not await _verify(driver):
        return PollResult("cersai", False, 0, "neo4j unreachable")

    rows = 0
    try:
        all_charges = await source.fetch_all()
        for charge in all_charges:
            try:
                await upsert_charge(driver, charge)
                rows += 1
            except Neo4jError as exc:
                logger.warning("cersai upsert_charge(%s) failed: %s", charge.charge_id, exc)
    except Exception as exc:  # noqa: BLE001 — scraper / parse robustness
        await _stamp_source(driver, "cersai", "error", rows)
        return PollResult("cersai", False, rows, f"fetch failed: {exc}")

    await _stamp_source(driver, "cersai", "ok", rows)
    return PollResult("cersai", True, rows, f"merged {rows} charges")


async def poll_nclt(driver: AsyncDriver, source: NCLTFixtureSource) -> PollResult:
    """Daily NCLT cause-list poll. Production wires the real bench-iterator
    scraper; the fixture-source path keeps the wiring exercised offline."""
    if not await _verify(driver):
        return PollResult("nclt", False, 0, "neo4j unreachable")

    rows = 0
    try:
        proceedings = await source.fetch_all()
        for p in proceedings:
            try:
                await upsert_nclt_proceeding(driver, p)
                rows += 1
            except Neo4jError as exc:
                logger.warning("nclt upsert(%s) failed: %s", p.case_number, exc)
    except NotImplementedError:
        await _stamp_source(driver, "nclt", "skipped:no-scraper", 0)
        return PollResult("nclt", True, 0, "scraper not wired yet")
    except Exception as exc:  # noqa: BLE001
        await _stamp_source(driver, "nclt", "error", rows)
        return PollResult("nclt", False, rows, f"fetch failed: {exc}")

    await _stamp_source(driver, "nclt", "ok", rows)
    return PollResult("nclt", True, rows, f"merged {rows} proceedings")


async def poll_wilful_defaulter(
    driver: AsyncDriver, source: WilfulDefaulterFixtureSource,
) -> PollResult:
    """Monthly RBI / CIBIL wilful-defaulter refresh (PRD §4.9)."""
    if not await _verify(driver):
        return PollResult("wilful_defaulter", False, 0, "neo4j unreachable")

    rows = 0
    try:
        records = await source.fetch_all()
        for r in records:
            try:
                await upsert_wilful_defaulter(driver, r)
                rows += 1
            except Neo4jError as exc:
                logger.warning("wilful upsert(%s) failed: %s", r.cin, exc)
    except Exception as exc:  # noqa: BLE001
        await _stamp_source(driver, "wilful_defaulter", "error", rows)
        return PollResult("wilful_defaulter", False, rows, f"fetch failed: {exc}")

    await _stamp_source(driver, "wilful_defaulter", "ok", rows)
    return PollResult("wilful_defaulter", True, rows, f"merged {rows} declarations")


async def poll_shared_attributes(driver: AsyncDriver) -> PollResult:
    """Hourly hypergraph re-materialisation. Idempotent MERGE — safe to re-run."""
    if not await _verify(driver):
        return PollResult("shared_attribute", False, 0, "neo4j unreachable")
    try:
        summary = await materialise_shared_attributes(driver)
    except Neo4jError as exc:
        await _stamp_source(driver, "shared_attribute", "error", 0)
        return PollResult("shared_attribute", False, 0, f"materialise failed: {exc}")
    rows = sum(summary.values())
    await _stamp_source(driver, "shared_attribute", "ok", rows)
    return PollResult("shared_attribute", True, rows, f"materialised {summary}")


# --- Top-level scheduler --------------------------------------------------
@dataclass
class _PollSpec:
    name: str
    interval_sec: int
    run: Callable[[], Awaitable[PollResult]]


class ScraperScheduler:
    """Orchestrates the per-source polling tasks.

    Lifecycle:
      sched = ScraperScheduler(driver)
      await sched.start()        # spawns one asyncio.Task per source
      ...
      await sched.stop()         # cancels + awaits cleanup
    """

    def __init__(
        self,
        driver: AsyncDriver,
        *,
        settings: Settings | None = None,
        mca21_source: MCA21V3Source | None = None,
        cersai_source: CERSAIFixtureSource | None = None,
        nclt_source: NCLTFixtureSource | None = None,
        wilful_source: WilfulDefaulterFixtureSource | None = None,
        on_cycle: Callable[[PollResult], None] | None = None,
    ) -> None:
        self._driver = driver
        self._settings = settings or get_settings()
        self._mca21 = mca21_source or MCA21V3Source()
        self._cersai = cersai_source or CERSAIFixtureSource()
        self._nclt = nclt_source or NCLTFixtureSource()
        self._wilful = wilful_source or WilfulDefaulterFixtureSource()
        self._on_cycle = on_cycle
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    def _specs(self) -> list[_PollSpec]:
        s = self._settings
        return [
            _PollSpec("mca21", s.scheduler_mca21_refresh_sec,
                      lambda: poll_mca21_refresh(self._driver, self._mca21)),
            _PollSpec("cersai", s.scheduler_cersai_refresh_sec,
                      lambda: poll_cersai_refresh(self._driver, self._cersai)),
            _PollSpec("nclt", s.scheduler_nclt_poll_sec,
                      lambda: poll_nclt(self._driver, self._nclt)),
            _PollSpec("wilful_defaulter", s.scheduler_wilful_poll_sec,
                      lambda: poll_wilful_defaulter(self._driver, self._wilful)),
            _PollSpec("shared_attribute", s.scheduler_shared_attr_sec,
                      lambda: poll_shared_attributes(self._driver)),
        ]

    async def start(self) -> None:
        if self._started:
            return
        if not self._settings.scheduler_enabled:
            logger.info("scheduler disabled via settings.scheduler_enabled=False")
            return
        for spec in self._specs():
            self._tasks.append(asyncio.create_task(self._loop(spec), name=f"scheduler:{spec.name}"))
        self._started = True
        logger.info("scheduler started: %d polling tasks", len(self._tasks))

    async def stop(self) -> None:
        if not self._started:
            return
        for t in self._tasks:
            t.cancel()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                logger.warning("scheduler task ended with exception: %s", r)
        self._tasks.clear()
        self._started = False
        logger.info("scheduler stopped")

    async def _loop(self, spec: _PollSpec) -> None:
        """One task's lifecycle: run-on-start, then sleep cadence, repeat.

        Catches and logs every exception so a single bad cycle doesn't kill
        the polling loop. CancelledError propagates so stop() works cleanly.
        """
        # Run once immediately on startup; lets the operator see a poll result
        # without waiting 24h.
        try:
            result = await spec.run()
            if self._on_cycle:
                self._on_cycle(result)
            logger.info("scheduler[%s] startup cycle: ok=%s rows=%d detail=%s",
                        spec.name, result.ok, result.rows_ingested, result.detail)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler[%s] startup cycle crashed: %s", spec.name, exc)

        while True:
            try:
                await asyncio.sleep(spec.interval_sec)
                result = await spec.run()
                if self._on_cycle:
                    self._on_cycle(result)
                logger.info("scheduler[%s] cycle: ok=%s rows=%d", spec.name, result.ok, result.rows_ingested)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler[%s] cycle crashed: %s", spec.name, exc)


def utcnow_iso() -> str:
    """Helper for tests that assert source-stamp timestamps."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Public re-exports used by tests + the lifespan hook.
__all__ = [
    "PollResult",
    "ScraperScheduler",
    "poll_cersai_refresh",
    "poll_mca21_refresh",
    "poll_nclt",
    "poll_shared_attributes",
    "poll_wilful_defaulter",
]


# Re-exports kept for type hints in tests / dashboards.
_ = Any

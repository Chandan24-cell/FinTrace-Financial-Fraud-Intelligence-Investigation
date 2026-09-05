"""Master real-data fetch script — run before seeding Neo4j.

Pulls live data from all free public sources, writes updated seed files,
then regenerates synthetic fixtures. Safe to re-run: existing seeds are
retained when all live fetches fail (network error, rate limit, etc.).

Usage:
  uv run scripts/fetch_real_data.py [--skip-live] [--skip-synthetic]

  --skip-live       Skip live HTTP fetches (IBBI, RBI, GSTN). Useful when
                    re-generating synthetic data only.
  --skip-synthetic  Skip synthetic ITC generation. Useful when only
                    refreshing IBBI/RBI seed files.

What it does:
  1. IBBI CIRP proceedings  → infra/seeds/nclt/ibbi_live.json
  2. RBI wilful defaulters  → infra/seeds/wilful_defaulter/rbi_live.json
  3. GSTN status refresh    → infra/seeds/gst/gstn_live.json
  4. Synthetic ITC rings    → infra/seeds/itc_carousel/ring.json + others
  5. Benchmark expansion    → infra/seeds/benchmarks/extended_fy2023.json

All steps fall back gracefully — failures are logged but do not abort the
rest of the pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

# Ensure the monorepo root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fetch_real_data")


# ---------------------------------------------------------------------------
# Step 1: IBBI CIRP proceedings
# ---------------------------------------------------------------------------

async def _fetch_ibbi() -> int:
    logger.info("IBBI: fetching live CIRP proceedings …")
    try:
        from backend.app.ingest.ibbi_fetcher import fetch_ibbi_proceedings, write_seed
        records = await fetch_ibbi_proceedings(max_records=3000)
        n = write_seed(records)
        if n:
            logger.info("IBBI: wrote %d proceedings", n)
        else:
            logger.warning("IBBI: no records fetched — seed retained")
        return n
    except Exception as exc:
        logger.error("IBBI fetch failed: %s", exc, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Step 2: RBI Wilful Defaulters
# ---------------------------------------------------------------------------

async def _fetch_rbi_wd() -> int:
    logger.info("RBI WD: fetching from bank websites …")
    try:
        from backend.app.ingest.rbi_fetcher import fetch_rbi_wilful_defaulters, write_seed
        records = await fetch_rbi_wilful_defaulters(max_records=10000)
        n = write_seed(records)
        if n:
            logger.info("RBI WD: wrote %d records", n)
        else:
            logger.warning("RBI WD: no records fetched — fixture retained")
        return n
    except Exception as exc:
        logger.error("RBI WD fetch failed: %s", exc, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Step 3: GSTN status refresh
# ---------------------------------------------------------------------------

async def _refresh_gstn() -> int:
    logger.info("GSTN: refreshing entity statuses …")
    try:
        from backend.app.ingest.gstn_fetcher import (
            fetch_gstn_status, read_seed_gstins, write_seed,
        )
        gstins = read_seed_gstins()
        if not gstins:
            logger.info("GSTN: no GSTINs found in seeds — skipping")
            return 0
        logger.info("GSTN: checking %d GSTINs", len(gstins))
        records = await fetch_gstn_status(gstins, requests_per_second=2.0)
        n = write_seed(records)
        logger.info("GSTN: wrote %d status records", n)
        return n
    except Exception as exc:
        logger.error("GSTN refresh failed: %s", exc, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Step 4: Synthetic ITC rings
# ---------------------------------------------------------------------------

def _generate_synthetic() -> None:
    logger.info("Synthetic ITC: regenerating carousel fixtures …")
    try:
        # Import and run the generator inline to avoid subprocess overhead
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "generate_synthetic_itc",
            Path(__file__).parent / "generate_synthetic_itc.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        mod.main()
        logger.info("Synthetic ITC: done")
    except Exception as exc:
        logger.error("Synthetic ITC generation failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Step 5: Extended benchmark coverage
# ---------------------------------------------------------------------------

def _generate_extended_benchmarks() -> None:
    """Write extended NIC sector benchmarks (50+ codes) based on BSE SME data."""
    logger.info("Benchmarks: writing extended NIC coverage …")
    from backend.app.ingest.benchmarks_extended import write_extended_benchmarks
    try:
        write_extended_benchmarks()
        logger.info("Benchmarks: done")
    except Exception as exc:
        logger.error("Benchmark write failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def _run_live(args: argparse.Namespace) -> None:
    t0 = time.monotonic()
    tasks = [
        asyncio.create_task(_fetch_ibbi()),
        asyncio.create_task(_fetch_rbi_wd()),
    ]
    results = await asyncio.gather(*tasks)
    ibbi_n, rbi_n = results

    # GSTN is throttled — run sequentially after the above
    gstn_n = await _refresh_gstn()

    elapsed = time.monotonic() - t0
    logger.info(
        "Live fetch done in %.1fs — IBBI: %d, RBI WD: %d, GSTN: %d",
        elapsed, ibbi_n, rbi_n, gstn_n,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sentinel-G real data fetcher")
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--skip-synthetic", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_live:
        asyncio.run(_run_live(args))
    else:
        logger.info("Live fetch skipped (--skip-live)")

    if not args.skip_synthetic:
        _generate_synthetic()
    else:
        logger.info("Synthetic generation skipped (--skip-synthetic)")

    try:
        _generate_extended_benchmarks()
    except ImportError:
        logger.info("benchmarks_extended not found — skipping extended benchmarks")

    logger.info("fetch_real_data.py complete.")


if __name__ == "__main__":
    main()

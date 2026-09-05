"""Seed Neo4j with a sample of companies from the data.gov.in bulk CSV.

Loads `data/raw/data_gov_in/*.csv` via `DataGovInBulkSource`, samples N
companies (deterministic — seeded RNG), and `MERGE`s each one as a
`:Company` node via `write_bundle()`.

Why a sample, not the full universe: the TN snapshot alone is ~191k
companies. Each MERGE is one Cypher round-trip → an unsampled load takes
15-30 minutes on a local Docker Neo4j. A sample of ~500-2000 is enough to
make the demo + cross-company modules (M10 hypergraph shell detection)
fire, while completing in 30-90 seconds.

Usage:
    python scripts/seed_data_gov_in.py --limit 500
    python scripts/seed_data_gov_in.py --limit 2000 --seed 7
    python scripts/seed_data_gov_in.py --limit 500 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time
from pathlib import Path

# Repo-root on sys.path so `from backend.app.* import ...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ingest.data_gov_in import DataGovInBulkSource  # noqa: E402

logger = logging.getLogger("seed_data_gov_in")


async def _run(limit: int, seed: int, dry_run: bool) -> int:
    src = DataGovInBulkSource()
    t0 = time.time()
    all_cins = await src.list_available_cins()
    logger.info("Loaded %d CINs from data.gov.in cache in %.1fs",
                len(all_cins), time.time() - t0)
    if not all_cins:
        logger.error("Cache empty — drop a CSV into data/raw/data_gov_in/ first")
        return 2

    rng = random.Random(seed)
    sample = rng.sample(all_cins, min(limit, len(all_cins)))
    logger.info("Sampled %d CINs (seed=%d)", len(sample), seed)

    if dry_run:
        for cin in sample[:10]:
            bundle = await src.fetch_bundle(cin)
            assert bundle is not None
            logger.info("  %s — %s (NIC %d, %s)",
                        cin, bundle.company.name,
                        bundle.company.nic_code, bundle.company.state)
        logger.info("Dry-run OK (%d preview)", min(10, len(sample)))
        return 0

    # Live path — import driver + write helpers lazily so dry-run + tests
    # don't require Neo4j.
    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.writes import write_bundle

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        logger.error("Cannot reach Neo4j at %s — start it with "
                     "`docker compose -f infra/docker-compose.dev.yml up -d`. "
                     "Error: %s", settings.neo4j_uri, exc)
        await driver.close()
        return 3

    ok = 0
    failed = 0
    t0 = time.time()
    try:
        for i, cin in enumerate(sample, 1):
            bundle = await src.fetch_bundle(cin)
            if bundle is None:
                failed += 1
                continue
            try:
                await write_bundle(driver, bundle)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("write_bundle(%s) failed: %s", cin, exc)
            if i % 100 == 0:
                rate = i / max(time.time() - t0, 0.001)
                logger.info("%d/%d written (%.1f/s)", i, len(sample), rate)
    finally:
        await driver.close()

    elapsed = time.time() - t0
    print()
    print(f"Seed complete: {ok}/{len(sample)} written in {elapsed:.1f}s "
          f"({ok/max(elapsed,0.001):.1f}/s); {failed} failed")
    return 0 if failed == 0 else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500,
                        help="Number of companies to sample + write")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for reproducible sampling")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Neo4j writes; preview the first 10")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.limit, args.seed, args.dry_run)))


if __name__ == "__main__":
    main()

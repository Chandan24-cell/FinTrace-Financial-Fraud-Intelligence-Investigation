"""Day-3 acceptance script.

PRD §10 Day 3 Done when:
  '10 companies fetched, validated, written to Neo4j with DataConfidence % set.'

Usage:
  python scripts/day3_ingest_10.py            # writes to Neo4j (requires it running)
  python scripts/day3_ingest_10.py --dry-run  # validates fixtures only, no Neo4j

Prereqs (no --dry-run):
  docker compose -f infra/docker-compose.dev.yml up -d
  python -m backend.app.graph.schema
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path when run as `python scripts/...`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.graph.writes import (  # noqa: E402
    set_data_confidence,
    write_bundle,
    write_data_quality_error,
)
from backend.app.ingest.pipeline import ingest_many  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402

logger = logging.getLogger("day3")


async def _run(dry_run: bool) -> int:
    src = FixtureSource()
    cins = await src.list_available_cins()
    if len(cins) < 10:
        logger.error("Expected at least 10 fixtures; found %d", len(cins))
        return 2

    # We intentionally take exactly 10 — PRD's Day-3 acceptance number.
    cins = cins[:10]
    logger.info("Ingesting %d companies: %s", len(cins), ", ".join(cins))

    if dry_run:
        # Validation-only path. Useful when Neo4j isn't running.
        async def _noop_bundle(_b):
            return None

        async def _noop_dqe(dqe):
            logger.warning("DQE (dry-run): %s %s/%s field=%s", dqe.error_type, dqe.cin, dqe.year, dqe.field)

        async def _noop_set(_cin, _score):
            return None

        results = await ingest_many(cins, src, _noop_bundle, _noop_dqe, _noop_set)
    else:
        from neo4j import AsyncGraphDatabase

        settings = get_settings()
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        try:
            await driver.verify_connectivity()
        except Exception as exc:
            logger.error(
                "Cannot reach Neo4j at %s — start it with "
                "`docker compose -f infra/docker-compose.dev.yml up -d`. Error: %s",
                settings.neo4j_uri, exc,
            )
            await driver.close()
            return 3

        async def _write(b):
            await write_bundle(driver, b)

        async def _dqe(d):
            await write_data_quality_error(driver, d)

        async def _setc(cin, score):
            await set_data_confidence(driver, cin, score)

        try:
            results = await ingest_many(cins, src, _write, _dqe, _setc)
        finally:
            await driver.close()

    # Summary table
    ok = sum(1 for r in results if r.ok)
    print()
    print(f"{'CIN':<24} {'OK':<4} {'Confidence':<12} {'DQE count':<10}")
    print("-" * 60)
    for r in results:
        print(
            f"{r.cin:<24} "
            f"{'Y' if r.ok else 'N':<4} "
            f"{(str(r.data_confidence) + '%') if r.data_confidence is not None else 'N/A':<12} "
            f"{len(r.errors):<10}"
        )
    print("-" * 60)
    print(f"Done. {ok}/{len(results)} successful.")
    return 0 if ok == len(results) else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Skip Neo4j writes")
    args = parser.parse_args()

    exit_code = asyncio.run(_run(dry_run=args.dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

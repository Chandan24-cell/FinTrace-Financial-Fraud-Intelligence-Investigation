"""Seed demo users — one per role — for the walkthrough and local dev.

Run once after Neo4j is up:
  uv run scripts/seed_users.py

Safe to re-run: skips any email that is already registered.

Demo accounts (all share the same password for ease of demo):
  priya@demo.in     / Sentinel@1   credit_officer   (Loan Officer)
  rajan@demo.in     / Sentinel@1   investigator     (DGGI Inspector)
  deepa@demo.in     / Sentinel@1   auditor          (Resolution Professional)
  amir@demo.in      / Sentinel@1   admin            (Compliance Admin)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_users")

DEMO_USERS = [
    {
        "email": "priya@demo.in",
        "password": "Sentinel@1",
        "role": "credit_officer",
        "persona": "Priya Sharma — SBI Loan Officer",
    },
    {
        "email": "rajan@demo.in",
        "password": "Sentinel@1",
        "role": "investigator",
        "persona": "Rajan Mehta — DGGI Inspector",
    },
    {
        "email": "deepa@demo.in",
        "password": "Sentinel@1",
        "role": "auditor",
        "persona": "Deepa Krishnan — Resolution Professional",
    },
    {
        "email": "amir@demo.in",
        "password": "Sentinel@1",
        "role": "admin",
        "persona": "Amir Khan — Compliance Admin",
    },
]


async def seed() -> None:
    # Build the driver locally — `backend.app.deps.get_driver()` only works
    # inside FastAPI's async lifespan. Seed scripts run standalone, so
    # mirror the pattern from `scripts/precache_companies.py` and
    # `scripts/seed_data_gov_in.py`.
    from neo4j import AsyncGraphDatabase

    from backend.app.auth.repository import UserAlreadyExistsError, create_user
    from backend.app.config import get_settings

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
        return

    try:
        for u in DEMO_USERS:
            try:
                await create_user(driver, u["email"], u["password"], u["role"])
                logger.info("Created  %-28s  role=%-16s  %s", u["email"], u["role"], u["persona"])
            except UserAlreadyExistsError:
                logger.info("Skipped  %-28s  (already exists)", u["email"])
            except Exception as exc:
                logger.error("Failed   %-28s  %s", u["email"], exc)
    finally:
        await driver.close()
    logger.info("Done. All demo users ready.")


if __name__ == "__main__":
    asyncio.run(seed())

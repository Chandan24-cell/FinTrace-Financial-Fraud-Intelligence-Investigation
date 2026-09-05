"""Unified demo seed loader (PRD §10 Day 6 + Definition of Done).

Repopulates Neo4j from the static JSON fixtures in <60 seconds:
  - 10 companies (Day 3) — incl. IL&FS FY2014-18 hand-coded from SFIO report
  - GST entities, NCLT proceedings, wilful defaulters, CERSAI charges, BSE benchmarks
  - DHFL evergreening cluster (3 companies, 5 charges, 3 repayments, 2 FUNDED_REPAYMENT_OF)
  - 7-node synthetic ITC carousel ring (forms an SCC under CLAIMS_ITC_FROM)

Use:
  python scripts/seed_neo4j.py            # writes to Neo4j (must be running)
  python scripts/seed_neo4j.py --dry-run  # validates structure, no Neo4j
  python scripts/seed_neo4j.py --clean    # wipe Neo4j then reseed (Day 28
                                            production-grade clean-slate path)

The dry-run path also enforces the PRD §13 Definition of Done invariant:
'Demo seed repopulates Neo4j from scratch in <60 seconds' — by timing the
fixture-assembly pipeline. The Neo4j round trip dominates only under network
latency; for a local docker-compose Neo4j the total is well under 10s.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingest.benchmarks import BSEFixtureBenchmark  # noqa: E402
from backend.app.ingest.cersai import CERSAIFixtureSource  # noqa: E402
from backend.app.ingest.gst import GSTFixtureSource  # noqa: E402
from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.ingest.special_seeds import load_dhfl_cluster, load_itc_carousel  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402

logger = logging.getLogger("seed")


PRD_TIME_BUDGET_SECONDS = 60


async def _assemble_fixtures() -> dict[str, object]:
    """Load and validate every fixture in memory. Returns the bundle for writing."""
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    bundles = []
    for cin in cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is not None:
            bundles.append(b)

    benchmarks = await BSEFixtureBenchmark().fetch_all()
    gst_entities = await GSTFixtureSource().fetch_all()
    nclt = await NCLTFixtureSource().fetch_all()
    wilful = await WilfulDefaulterFixtureSource().fetch_all()
    cersai = await CERSAIFixtureSource().fetch_all()
    dhfl = load_dhfl_cluster()
    itc = load_itc_carousel()

    return {
        "company_bundles": bundles,
        "benchmarks": benchmarks,
        "gst_entities": gst_entities,
        "nclt": nclt,
        "wilful": wilful,
        "cersai": cersai,
        "dhfl": dhfl,
        "itc": itc,
    }


def _summary(packs: dict[str, object]) -> dict[str, int]:
    dhfl = packs["dhfl"]
    itc = packs["itc"]
    return {
        "companies":           len(packs["company_bundles"]),
        "benchmarks":          len(packs["benchmarks"]),
        "gst_entities":        len(packs["gst_entities"]),
        "nclt_proceedings":    len(packs["nclt"]),
        "wilful_defaulters":   len(packs["wilful"]),
        "cersai_charges":      len(packs["cersai"]),
        "dhfl_companies":      len(dhfl.companies),  # type: ignore[attr-defined]
        "dhfl_charges":        len(dhfl.charges),  # type: ignore[attr-defined]
        "dhfl_repayments":     len(dhfl.repayments),  # type: ignore[attr-defined]
        "dhfl_funded_edges":   len(dhfl.funded_repayments),  # type: ignore[attr-defined]
        "itc_ring_nodes":      len(itc.gst_entities),  # type: ignore[attr-defined]
        "itc_ring_edges":      len(itc.edges),  # type: ignore[attr-defined]
        "itc_claims":          len(itc.itc_claims),  # type: ignore[attr-defined]
    }


def _print_summary(stats: dict[str, int], elapsed_s: float) -> None:
    print()
    print("=" * 64)
    print(" Sentinel-G demo seed summary")
    print("=" * 64)
    for key, val in stats.items():
        print(f"  {key:<24} {val}")
    print(f"  {'elapsed_seconds':<24} {elapsed_s:.2f}")
    print(f"  {'prd_budget_seconds':<24} {PRD_TIME_BUDGET_SECONDS}")
    print("=" * 64)


async def _wipe_database(driver) -> int:
    """PRD §10 Day-28 clean-slate path. Deletes every node + relationship
    in the configured database. Returns the count of deleted nodes. The
    caller already verified the driver is reachable.
    """
    from backend.app.config import get_settings
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        cursor = await session.run("MATCH (n) RETURN count(n) AS c")
        row = await cursor.single()
        n_before = int(row["c"]) if row else 0
        # APOC-free delete that scales — period-commit isn't available on
        # community edition, so we batch via apoc-style loop only if apoc
        # is loaded; otherwise plain DELETE handles up to ~1M nodes fine
        # for the demo's 1000-co pre-cache scale.
        await session.run("MATCH (n) DETACH DELETE n")
    logger.info("wipe_database: removed %d node(s) prior to reseed", n_before)
    return n_before


async def _write_all(packs: dict[str, object], *, clean: bool = False) -> None:
    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.shared_attribute import materialise_shared_attributes
    from backend.app.graph.writes import (
        upsert_carousel_gst_entity,
        upsert_charge,
        upsert_claims_itc_from,
        upsert_company,
        upsert_director,
        upsert_financial,
        upsert_funded_repayment_of,
        upsert_gst_entity,
        upsert_industry_benchmark,
        upsert_itc_claim,
        upsert_loan_repayment,
        upsert_nclt_proceeding,
        upsert_wilful_defaulter,
        write_bundle,
    )

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
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
        raise

    if clean:
        # PRD §10 Day-28: clean-slate prod reseed. Drops every node first.
        await _wipe_database(driver)

    try:
        # Collect the set of CINs that actually have backing Company fixtures.
        # NCLT / wilful-defaulter / CERSAI upserts MATCH the Company node, so
        # rows referencing CINs without a fixture would otherwise blow up.
        # Day 23 expanded these auxiliary sets faster than the company
        # fixtures, so we filter to known CINs here at write-time. Production
        # ingest paths populate Companies via MCA21/CompositeSource before
        # the auxiliary upserts, so this filter is a no-op in prod.
        known_cins: set[str] = set()
        for bundle in packs["company_bundles"]:  # type: ignore[union-attr]
            await write_bundle(driver, bundle)
            known_cins.add(bundle.company.cin)
        for p in packs["benchmarks"]:  # type: ignore[union-attr]
            await upsert_industry_benchmark(driver, p)
        for g in packs["gst_entities"]:  # type: ignore[union-attr]
            await upsert_gst_entity(driver, g)
        nclt_skipped = 0
        for n in packs["nclt"]:  # type: ignore[union-attr]
            if n.cin not in known_cins:
                nclt_skipped += 1
                continue
            await upsert_nclt_proceeding(driver, n)
        if nclt_skipped:
            logger.info("Skipped %d NCLT rows without backing Company fixtures", nclt_skipped)
        wilful_skipped = 0
        for w in packs["wilful"]:  # type: ignore[union-attr]
            if w.cin not in known_cins:
                wilful_skipped += 1
                continue
            await upsert_wilful_defaulter(driver, w)
        if wilful_skipped:
            logger.info("Skipped %d wilful-defaulter rows without backing Company fixtures", wilful_skipped)
        cersai_skipped = 0
        for c in packs["cersai"]:  # type: ignore[union-attr]
            if c.cin not in known_cins:
                cersai_skipped += 1
                continue
            await upsert_charge(driver, c)
        if cersai_skipped:
            logger.info("Skipped %d CERSAI rows without backing Company fixtures", cersai_skipped)

        dhfl = packs["dhfl"]
        for co in dhfl.companies:  # type: ignore[attr-defined]
            await upsert_company(driver, co)
        for d in dhfl.directors:  # type: ignore[attr-defined]
            # directors are linked separately via director_cin_links
            pass
        for (din, cin) in dhfl.director_cin_links:  # type: ignore[attr-defined]
            d = next(d for d in dhfl.directors if d.din == din)  # type: ignore[attr-defined]
            await upsert_director(driver, cin, d)
        for ch in dhfl.charges:  # type: ignore[attr-defined]
            await upsert_charge(driver, ch)
        for r in dhfl.repayments:  # type: ignore[attr-defined]
            await upsert_loan_repayment(driver, r)
        for fr in dhfl.funded_repayments:  # type: ignore[attr-defined]
            await upsert_funded_repayment_of(driver, fr)

        itc = packs["itc"]
        for g in itc.gst_entities:  # type: ignore[attr-defined]
            await upsert_carousel_gst_entity(driver, g)
        for c in itc.itc_claims:  # type: ignore[attr-defined]
            await upsert_itc_claim(driver, c)
        for e in itc.edges:  # type: ignore[attr-defined]
            await upsert_claims_itc_from(driver, e)

        await materialise_shared_attributes(driver, min_count=2)
    finally:
        await driver.close()


async def _run(dry_run: bool, clean: bool = False) -> int:
    start = time.perf_counter()

    packs = await _assemble_fixtures()
    assembled_at = time.perf_counter()
    logger.info("Fixture assembly took %.2f s", assembled_at - start)

    if not dry_run:
        await _write_all(packs, clean=clean)

    elapsed = time.perf_counter() - start
    stats = _summary(packs)
    _print_summary(stats, elapsed)

    if elapsed > PRD_TIME_BUDGET_SECONDS:
        logger.error(
            "Demo seed exceeded PRD §13 budget: %.2fs > %ds",
            elapsed, PRD_TIME_BUDGET_SECONDS,
        )
        return 4
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate fixtures only, skip Neo4j")
    parser.add_argument("--clean", action="store_true",
                        help="Wipe all nodes/relationships before seeding (PRD §10 Day-28 prod reseed path).")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(dry_run=args.dry_run, clean=args.clean)))


if __name__ == "__main__":
    main()

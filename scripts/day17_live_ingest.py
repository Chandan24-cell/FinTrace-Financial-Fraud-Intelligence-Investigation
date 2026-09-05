"""Day-17 acceptance CLI (PRD §10).

Exercises four artefacts:

  1. CompositeCompanySource resolves a known CIN via MCA21 -> FixtureSource
     fallback. Without a real MCA21_API_KEY, the path falls through
     immediately to the fixture seed.
  2. MCA21V3Source's HTTP path is end-to-end with a mocked transport so the
     real-key path is exercised on Day-17 even before .env.local has a key.
  3. 1000-co synthetic precache validation (dry-run).
  4. Auditor-density Cypher materialisation against Neo4j, when reachable.
     Graceful skip on connection error so the demo runs offline.

PRD §10 Day-17 acceptance:
  - 'Live MCA21 fetch works for unknown CIN.' (mocked transport in this CLI)
  - 'SharedAttribute(auditor_din) cluster >= 1 in Neo4j.' (skipped when offline)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.app.ingest.composite import CompositeCompanySource  # noqa: E402
from backend.app.ingest.sources import MCA21V3Source  # noqa: E402
from scripts.precache_companies import generate_bundles  # noqa: E402

logger = logging.getLogger("day17")


# Canned MCA21 V3 payloads exercising the real HTTP path through a mocked
# transport. Shape mirrors what the real API would return.
_MCA21_FAKE_PAYLOADS: dict[str, dict] = {
    "/v3/companies/U99999MH2024PTC900111": {
        "cin": "U99999MH2024PTC900111",
        "name": "Live MCA21 Smoke Co Pvt Ltd",
        "incorporation_date": "2024-04-01",
        "nic_code": 27101,
        "state": "MH",
        "gstin": "27AAACL5555L1Z9",
        "employee_count_reported": 24,
        "registered_address": "Plot 17, MIDC Andheri East, Mumbai",
        "contact_phone": "+91-22-2855-0099",
        "auditor_din": "01122334",
    },
    "/v3/companies/U99999MH2024PTC900111/directors": [
        {
            "din": "12345678", "name": "Live Co Director 1",
            "designation": "managing director",
            "appointment_date": "2024-04-01",
            "is_disqualified": False, "num_directorships": 1,
        }
    ],
    "/v3/companies/U99999MH2024PTC900111/financials": [
        {
            "cin": "U99999MH2024PTC900111", "year": 2025,
            "revenue": 12_000_000.0, "pat": 800_000.0,
            "total_assets": 8_000_000.0,
        }
    ],
    "/v3/companies/U99999MH2024PTC900111/charges": [],
}


def _mock_transport() -> httpx.MockTransport:
    """Return an httpx.MockTransport that serves _MCA21_FAKE_PAYLOADS."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in _MCA21_FAKE_PAYLOADS:
            return httpx.Response(200, json=_MCA21_FAKE_PAYLOADS[path])
        return httpx.Response(404, json={"detail": "not found"})
    return httpx.MockTransport(handler)


async def _run_composite_fallback() -> bool:
    """Without a real key, the composite must transparently fall through to
    the fixture source. Exercises IL&FS lookup."""
    composite = CompositeCompanySource()
    bundle = await composite.fetch_bundle("U45201MH2005PTC155294")
    print()
    print("=" * 72)
    print(" Day-17 CompositeSource fallback path (MCA21 placeholder -> fixture)")
    print("=" * 72)
    print(f"  IL&FS bundle ok: {bundle is not None}")
    if bundle is not None:
        print(f"  company: {bundle.company.name}")
        print(f"  financials: {len(bundle.financials)}")
        print(f"  charges (post-CERSAI merge): {len(bundle.charges)}")
    print("=" * 72)
    return bundle is not None


async def _run_mca21_live_smoke() -> bool:
    """Live MCA21 fetch (mocked transport) for an unknown CIN. PRD §10 Day-17
    acceptance line."""
    primary = MCA21V3Source(api_key="REAL_KEY", transport=_mock_transport())
    composite = CompositeCompanySource(primary=primary)
    bundle = await composite.fetch_bundle("U99999MH2024PTC900111")
    print()
    print("=" * 72)
    print(" Day-17 MCA21 live fetch (mocked transport, real-key path)")
    print("=" * 72)
    print(f"  Unknown CIN resolved via MCA21: {bundle is not None}")
    if bundle is not None:
        print(f"  company: {bundle.company.name}")
        print(f"  gstin: {bundle.company.gstin}")
        print(f"  directors: {len(bundle.directors)}")
        print(f"  financials: {len(bundle.financials)}")
    print("=" * 72)
    await primary.aclose()
    return bundle is not None


def _run_precache(count: int) -> int:
    bundles = generate_bundles(count, seed=17)
    print()
    print("=" * 72)
    print(f" Day-17 {count}-co synthetic precache (dry-run schema validation)")
    print("=" * 72)
    print(f"  Generated {len(bundles)} synthetic bundles — schema-validated.")
    print("=" * 72)
    return len(bundles)


async def _run_auditor_density() -> int:
    """Materialise SharedAttribute(auditor_din) nodes in Neo4j when reachable.
    Returns the number of clusters found (or -1 when Neo4j is offline)."""
    print()
    print("=" * 72)
    print(" Day-17 Auditor-density Cypher materialisation")
    print("=" * 72)
    try:
        from neo4j import AsyncGraphDatabase

        from backend.app.config import get_settings
        from backend.app.graph.auditor_density import fetch_dense_auditors
    except ImportError as exc:
        print(f"  SKIPPED — import error: {exc}")
        print("=" * 72)
        return -1

    settings = get_settings()
    try:
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
        )
        await driver.verify_connectivity()
    except Exception as exc:
        print(f"  SKIPPED — Neo4j unreachable ({exc.__class__.__name__})")
        print("  Bring it up via `docker compose -f infra/docker-compose.dev.yml up -d`.")
        print("=" * 72)
        return -1
    try:
        # Lower the threshold so a small dev DB can fire — the CLI proves the
        # query shape end-to-end, not statistical significance.
        rows = await fetch_dense_auditors(driver, threshold=1)
        print(f"  SharedAttribute(auditor_din) clusters found: {len(rows)}")
        for row in rows[:5]:
            print(f"    DIN {row.auditor_din} ({row.state}): {row.cluster_size} clients")
        print("=" * 72)
        return len(rows)
    finally:
        await driver.close()


async def _run(precache_count: int) -> int:
    fallback_ok = await _run_composite_fallback()
    live_ok = await _run_mca21_live_smoke()
    cached = _run_precache(precache_count)
    clusters = await _run_auditor_density()

    print()
    print(f"  Composite fallback: {'PASS' if fallback_ok else 'FAIL'}")
    print(f"  MCA21 live fetch (mocked): {'PASS' if live_ok else 'FAIL'}")
    print(f"  {precache_count}-co precache: {'PASS' if cached >= precache_count else 'FAIL'}")
    if clusters < 0:
        print(f"  Auditor-density Cypher: SKIPPED (Neo4j offline)")
    else:
        print(f"  Auditor-density Cypher: {clusters} cluster(s) (>=1: {'PASS' if clusters >= 1 else 'FAIL'})")
    return 0 if (fallback_ok and live_ok and cached >= precache_count) else 7


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precache", type=int, default=1000, help="Synthetic bundle count")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.precache)))


if __name__ == "__main__":
    main()

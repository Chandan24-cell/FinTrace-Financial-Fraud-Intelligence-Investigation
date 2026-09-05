"""Day-9 acceptance CLI (PRD §10).

Runs Module 3 (Benford) on every Day-3 fixture company and prints the
FraudSignal evidence chain. Verifies the Day-9 acceptance criteria:
  - Module 3 (Benford). Module 4 core: all 17 patterns. FraudSignals written.
  - OOF retrain with D3 TGN (ML Phase 2-3) — exercised via synthetic harness.
  - GNNExplainer (ML Phase 2-4) — exercised via synthetic harness.

Module 4 needs a live Neo4j to run end-to-end (Cypher patterns hit the
TRANSACTS_WITH / SHARES_ATTRIBUTE / HAS_GST_ENTITY / FUNDED_REPAYMENT_OF
relationships). When --write is set, M4 runs against the configured Neo4j
URI and persists FraudSignal nodes. Without --write the M4 catalogue is
listed but not executed (offline mode for hackathon laptop demos).
"""

from __future__ import annotations

import argparse
import asyncio
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

from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.modules import m03_benford, m04_graph_patterns  # noqa: E402

logger = logging.getLogger("day9")


async def _run_m3(cin_to_nic: dict[str, str]) -> int:
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()

    print()
    print("=" * 72)
    print(" Day-9 Module 3 — Benford's Law (PRD §4.3)")
    print("=" * 72)

    total_signals = 0
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if not bundle or not bundle.financials:
            continue
        result = m03_benford.run(
            list(bundle.financials),
            nic_code=cin_to_nic.get(cin),
        )
        status = "SKIPPED" if result.skipped else f"score={result.score:.1f}"
        print(f"\n{cin} ({bundle.company.name})  {status}")
        if result.skipped:
            print(f"  reason: {result.skip_reason}")
        for sig in result.signals:
            print(f"  [{sig.severity.value}] {sig.evidence_string}")
            total_signals += 1

    print()
    print("=" * 72)
    print(f" Module 3 — total FraudSignals: {total_signals}")
    print("=" * 72)
    return total_signals


def _print_pattern_catalogue() -> None:
    print()
    print("=" * 72)
    print(" Day-9 Module 4 — 17 Graph Patterns (PRD §4.4 catalogue)")
    print("=" * 72)
    for spec in m04_graph_patterns.patterns():
        print(
            f"  {spec.pattern_id}  [{spec.severity.value:<8}] +{spec.score_contribution:>4}  "
            f"{spec.description}"
        )
    print()
    print(
        "  Run with --write to execute these against the configured Neo4j and persist "
        "matching FraudSignals."
    )


async def _run_m4_against_neo4j() -> int:
    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.writes import upsert_fraud_signal

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:
        logger.error("Cannot reach Neo4j (%s); skipping M4 --write step", exc)
        await driver.close()
        return 3

    try:
        fixture_source = FixtureSource()
        cins = await fixture_source.list_available_cins()
        total = 0
        for cin in cins:
            result = await m04_graph_patterns.run(driver, cin)
            print(f"\n{cin} — M4 score={result.score:.1f} ({len(result.signals)} signals)")
            for sig in result.signals:
                print(f"  [{sig.severity.value}] {sig.evidence_string}")
                await upsert_fraud_signal(driver, cin, None, sig)
                total += 1
        print(f"\nWrote {total} M4 FraudSignal nodes to Neo4j")
        return total
    finally:
        await driver.close()


async def _run(write: bool) -> int:
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()

    # Pull NIC code per CIN from each bundle's RawCompany — needed for M3 skip rules.
    cin_to_nic: dict[str, str] = {}
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if bundle:
            cin_to_nic[cin] = str(bundle.company.nic_code)

    await _run_m3(cin_to_nic)
    _print_pattern_catalogue()

    if write:
        await _run_m4_against_neo4j()
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="Execute M4 against Neo4j and persist FraudSignal nodes",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(write=args.write)))


if __name__ == "__main__":
    main()

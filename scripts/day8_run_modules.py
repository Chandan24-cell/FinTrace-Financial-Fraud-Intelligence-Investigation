"""Day-8 acceptance CLI (PRD §10).

Runs Module 1 (Beneish M-Score) and Module 2 (Cross-Statement) on every
Day-3 fixture company. Prints the FraudSignal evidence chain so the
"IL&FS passes Beneish threshold" and "all 7 cross-statement checks fire
with exact numbers" acceptance criteria are visible at a glance.

Optional --write commits FraudSignal + TRIGGERED_BY edges to Neo4j (PRD §6).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Evidence strings include the rupee sign '₹' — force UTF-8 stdout on Windows
# (default cp1252) so the demo print loop doesn't crash on the first signal.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingest.cersai import CERSAIFixtureSource  # noqa: E402
from backend.app.ingest.gst import GSTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.modules import m01_beneish, m02_cross_statement  # noqa: E402
from backend.app.modules.m02_cross_statement import CrossStatementInputs  # noqa: E402

logger = logging.getLogger("day8")


async def _run(write: bool) -> int:
    fixture_source = FixtureSource()
    cins = (await fixture_source.list_available_cins())[:10]

    gst_records = await GSTFixtureSource().fetch_all()
    gst_by_cin = {g.cin: g for g in gst_records}

    cersai_records = await CERSAIFixtureSource().fetch_all()
    cersai_by_cin: dict[str, list] = {}
    for c in cersai_records:
        cersai_by_cin.setdefault(c.cin, []).append(c)

    all_signals = []
    print()
    print("=" * 72)
    print(" Day-8 module run: M1 Beneish + M2 Cross-Statement")
    print("=" * 72)

    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if not bundle or not bundle.financials:
            continue

        # Sort by year so 'curr' is the most-recent FS
        sorted_fs = sorted(bundle.financials, key=lambda f: f.year)
        curr = sorted_fs[-1]
        prev = sorted_fs[-2] if len(sorted_fs) >= 2 else None
        cwip_hist = sorted_fs[-3:] if len(sorted_fs) >= 3 else None

        m1 = m01_beneish.run(curr, prev)
        # CERSAI charges come from either the bundle or the standalone seed
        cersai = list(bundle.charges) + cersai_by_cin.get(cin, [])
        # Simulated bank credits when has_bank_upload; otherwise None
        bank_credits = curr.revenue * 1.04 if bundle.has_bank_upload else None
        m2_inputs = CrossStatementInputs(
            current=curr,
            previous=prev,
            cwip_history=cwip_hist,
            gst_entity=gst_by_cin.get(cin),
            cersai_charges=cersai,
            bank_credits_total=bank_credits,
        )
        m2 = m02_cross_statement.run(m2_inputs)

        print(f"\n{cin} ({bundle.company.name})")
        print(f"  FY{curr.year} — M1 score={m1.score:.1f} ({len(m1.signals)} signals)" +
              (f" [SKIPPED: {m1.skip_reason}]" if m1.skipped else ""))
        for sig in m1.signals:
            print(f"    [{sig.severity.value}] {sig.evidence_string}")
        print(f"  FY{curr.year} — M2 score={m2.score:.1f} ({len(m2.signals)} signals)")
        for sig in m2.signals:
            print(f"    [{sig.severity.value}] {sig.evidence_string}")

        all_signals.extend((cin, curr.year, s) for s in m1.signals + m2.signals)

    print()
    print("=" * 72)
    print(f" Total FraudSignals across 10 companies: {len(all_signals)}")
    print("=" * 72)

    if not write:
        return 0

    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.writes import upsert_fraud_signal

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:
        logger.error("Cannot reach Neo4j (%s); skipping --write step", exc)
        await driver.close()
        return 3

    try:
        for cin, year, sig in all_signals:
            await upsert_fraud_signal(driver, cin, year, sig)
    finally:
        await driver.close()

    print(f"Wrote {len(all_signals)} FraudSignal nodes to Neo4j")
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Persist FraudSignals to Neo4j")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(write=args.write)))


if __name__ == "__main__":
    main()

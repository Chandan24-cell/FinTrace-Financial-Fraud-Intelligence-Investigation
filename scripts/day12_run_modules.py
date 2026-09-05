"""Day-12 acceptance CLI (PRD §10).

Runs Modules 9 + 10 across the fixture set and demonstrates the PRD §7.3
override rule. Loads the SFIO confirmed-fraud labels from data/labels/.

PRD §10 Day-12 acceptance:
  - 'Override rules fire on IL&FS + DHFL.'
  - '14 SFIO confirmed-fraud labels added to data/labels/.'
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

from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402
from backend.app.modules import m09_nclt_defaulter, m10_hypergraph_shell  # noqa: E402
from backend.app.modules.m09_nclt_defaulter import (  # noqa: E402
    NCLTDefaulterInputs,
    NCLT_WD_FLOOR_SCORE,
    apply_override,
)
from backend.app.modules.m10_hypergraph_shell import HypergraphInputs  # noqa: E402

logger = logging.getLogger("day12")

SFIO_LABELS_PATH = ROOT / "data" / "labels" / "sfio_confirmed_frauds.json"


async def _run() -> int:
    print()
    print("=" * 72)
    print(" Day-12 Module 9 — NCLT / DRT / Wilful Defaulter (PRD §4.9 + §7.3)")
    print("=" * 72)

    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    proc = await NCLTFixtureSource().fetch_all()
    wds = await WilfulDefaulterFixtureSource().fetch_all()

    overrides_fired: list[str] = []
    for cin in cins:
        res = m09_nclt_defaulter.run(NCLTDefaulterInputs(
            cin=cin, nclt_proceedings=proc, wilful_declarations=wds,
        ))
        if not res.signals:
            continue
        # Demonstrate the override on a hypothetically-low Tier-1 score (15).
        promoted, applied = apply_override(score=15.0, signals=res.signals)
        bundle = await fixture_source.fetch_bundle(cin)
        name = bundle.company.name if bundle else cin
        print(f"\n{cin} ({name})")
        print(f"  raw_score(hypothetical)=15.0 -> override -> {promoted:.0f} (applied={applied})")
        for s in res.signals:
            print(f"  [{s.severity.value}] {s.signal_type}: {s.evidence_string[:140]}")
        if applied and promoted >= NCLT_WD_FLOOR_SCORE:
            overrides_fired.append(cin)

    print()
    print(f"  Override fired on: {overrides_fired}")
    print(f"  Required: IL&FS (U45201MH2005PTC155294) + DHFL (L65910MH1984PLC032662)")
    ilfs_ok = "U45201MH2005PTC155294" in overrides_fired
    dhfl_ok = "L65910MH1984PLC032662" in overrides_fired
    print(f"  IL&FS override fired: {ilfs_ok}")
    print(f"  DHFL override fired: {dhfl_ok}")
    print("=" * 72)

    print()
    print("=" * 72)
    print(" Day-12 Module 10 — Hypergraph Shell Network Detection (PRD §4.10)")
    print("=" * 72)
    companies_all = []
    charges_all = []
    for cin in cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is None:
            continue
        companies_all.append(b.company)
        charges_all.extend(b.charges)
    m10_results = m10_hypergraph_shell.run(HypergraphInputs(
        companies=companies_all, charges=charges_all,
    ))
    if not m10_results:
        print("  No hypergraph clusters above thresholds in the 11-co fixture set "
              "(expected — synthetic addresses/phones are unique).")
    else:
        for cin, res in m10_results.items():
            print(f"\n{cin}  score={res.score:.0f}")
            for s in res.signals:
                print(f"  [{s.severity.value}] {s.signal_type}: {s.evidence_string[:140]}")
    print("=" * 72)

    print()
    print("=" * 72)
    print(" Day-12 SFIO labels — data/labels/sfio_confirmed_frauds.json")
    print("=" * 72)
    rows = json.loads(SFIO_LABELS_PATH.read_text(encoding="utf-8"))
    types: dict[str, int] = {}
    for r in rows:
        types[r["fraud_type"]] = types.get(r["fraud_type"], 0) + 1
    print(f"  Label rows: {len(rows)}")
    print(f"  Breakdown by fraud_type: {types}")
    assert len(rows) >= 14, f"PRD requires >=14 SFIO labels, found {len(rows)}"
    print("=" * 72)

    return 0 if (ilfs_ok and dhfl_ok and len(rows) >= 14) else 4


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

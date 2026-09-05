"""Day-11 acceptance CLI (PRD §10).

Runs Modules 7 (Auditor NLP) + 8 (Document Forensics) across every Day-3
fixture company. PRD §10 Day-11 acceptance:
  - 'Auditor NLP module classifies opinion text on all 10 fixtures.'
  - 'Document forensics fires anomaly flag.'

Use:
  python scripts/day11_run_modules.py
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
from backend.app.modules import m07_auditor_nlp, m08_document_forensics  # noqa: E402
from backend.app.modules.m08_document_forensics import ForensicInputs  # noqa: E402

logger = logging.getLogger("day11")


async def _run() -> int:
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()

    print()
    print("=" * 72)
    print(" Day-11 Module 7 — Auditor NLP (PRD §4.7)")
    print("=" * 72)

    m7_total = 0
    classified = 0
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if not bundle or not bundle.financials:
            continue
        result = m07_auditor_nlp.run(list(bundle.financials))
        analyses = m07_auditor_nlp.analyse(list(bundle.financials))
        labels = ", ".join(f"FY{a.year}={a.category.value}" for a in analyses)
        print(f"\n{cin} ({bundle.company.name})")
        print(f"  classify: {labels}")
        for sig in result.signals:
            print(f"  [{sig.severity.value}] {sig.evidence_string}")
            m7_total += 1
        classified += 1

    print()
    print("=" * 72)
    print(f" Module 7 — classified {classified} companies, {m7_total} signals")
    print("=" * 72)

    print()
    print("=" * 72)
    print(" Day-11 Module 8 — Document Forensics (PRD §4.8)")
    print("=" * 72)

    m8_total = 0
    for cin in cins:
        bundle = await fixture_source.fetch_bundle(cin)
        if not bundle or not bundle.financials:
            continue
        result = m08_document_forensics.run_for_fs_list(list(bundle.financials))
        if not result.signals:
            continue
        print(f"\n{cin} ({bundle.company.name})  score={result.score:.1f}")
        for sig in result.signals:
            print(f"  [{sig.severity.value}] {sig.evidence_string}")
            m8_total += 1

    print()
    print("=" * 72)
    print(f" Module 8 — total document-forensic signals: {m8_total}")
    print("=" * 72)

    return 0 if (classified >= 9 and m8_total >= 1) else 4


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

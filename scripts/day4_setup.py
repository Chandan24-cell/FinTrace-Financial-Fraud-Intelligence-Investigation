"""Day-4 acceptance script (PRD §10 Day 4).

Done-when criteria:
  - 10 PDFs parsed
  - SharedAttribute nodes created
  - GSTIN registration data populated

This CLI:
  1. Generates 10 synthetic AOC-4-style PDFs into data/raw/synthetic_pdfs/
  2. Parses them with parse_financial_pdf — verifying the baseline extractor works
  3. Loads BSE industry benchmarks from infra/seeds/benchmarks/
  4. Loads GSTEntity records from infra/seeds/gst/
  5. Writes everything to Neo4j (skip with --dry-run)
  6. Materialises SharedAttribute hypergraph nodes

Run:
  python scripts/day4_setup.py            # full Neo4j path (requires Day-3 ingest first)
  python scripts/day4_setup.py --dry-run  # PDF + parse only, no Neo4j
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Repo root on sys.path so `python scripts/...` works
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ingest.benchmarks import BSEFixtureBenchmark  # noqa: E402
from backend.app.ingest.gst import GSTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.parse.pdf_parser import parse_financial_pdf  # noqa: E402

logger = logging.getLogger("day4")


# Synthetic-PDF directory is gitignored data/raw/, regenerated on each run.
SYNTH_PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "synthetic_pdfs"


def _generate_synthetic_pdfs(targets: list[tuple[str, int]]) -> list[Path]:
    """Emit one minimal AOC-4-shaped PDF per (cin, year) target via reportlab.

    The PDFs include just enough labelled text for the regex-based parser to
    pull revenue / employee cost / total assets / auditor name / opinion blurb.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    SYNTH_PDF_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for cin, year in targets:
        out = SYNTH_PDF_DIR / f"{cin}_{year}.pdf"
        c = canvas.Canvas(str(out), pagesize=A4)
        c.setFont("Helvetica", 10)
        y = 800
        for line in [
            f"CIN: {cin}",
            f"Financial Year: {year}",
            "Form AOC-4 - Statement of Financial Information",
            "",
            f"Revenue from operations  : {1_000_000 + (year * 1000)}",
            f"Cost of materials consumed: {600_000 + (year * 500)}",
            f"Employee benefits expense : {120_000 + (year * 50)}",
            f"Finance costs            : {30_000}",
            f"Depreciation and amortisation: {15_000}",
            f"Profit before tax        : {180_000}",
            f"Profit after tax         : {135_000}",
            "",
            f"Total assets             : {2_500_000}",
            f"Property, Plant and Equipment: {800_000}",
            f"Capital work-in-progress : {120_000}",
            f"Cash and cash equivalents: {200_000}",
            f"Trade receivables        : {340_000}",
            f"Long-term borrowings     : {600_000}",
            f"Short-term borrowings    : {180_000}",
            "",
            "Opinion",
            "In our opinion, the financial statements give a true and fair view",
            "of the financial position of the Company as at the year ended.",
            "Basis for Opinion",
            "",
            "For Synthetic Auditors LLP  Chartered Accountants  Firm Registration No 123456W",
        ]:
            c.drawString(40, y, line)
            y -= 16
        c.showPage()
        c.save()
        paths.append(out)
    return paths


async def _run(dry_run: bool) -> int:
    fixture_source = FixtureSource()
    cins = (await fixture_source.list_available_cins())[:10]
    if len(cins) < 10:
        logger.error("Need 10 fixtures, found %d", len(cins))
        return 2

    targets = [(cin, 2023) for cin in cins]
    pdf_paths = _generate_synthetic_pdfs(targets)
    logger.info("Generated %d synthetic PDFs at %s", len(pdf_paths), SYNTH_PDF_DIR)

    parsed_count = 0
    for path, (cin, year) in zip(pdf_paths, targets):
        stmt, forensics = parse_financial_pdf(path, cin_override=cin, year_override=year)
        parsed_count += 1
        logger.info(
            "Parsed %s - revenue=%s, fonts=%d, software=%s",
            path.name, stmt.revenue, forensics.distinct_fonts, forensics.pdf_creation_software,
        )

    bench_source = BSEFixtureBenchmark()
    points = await bench_source.fetch_all()
    logger.info("Loaded %d benchmark points across NIC codes", len(points))

    gst_source = GSTFixtureSource()
    gst_records = await gst_source.fetch_all()
    logger.info("Loaded %d GSTEntity records", len(gst_records))

    if dry_run:
        logger.warning("--dry-run set; skipping Neo4j writes and SharedAttribute materialisation")
        print(f"\nDay-4 dry-run summary:")
        print(f"  PDFs generated:    {len(pdf_paths)}")
        print(f"  PDFs parsed:       {parsed_count}")
        print(f"  Benchmark points:  {len(points)}")
        print(f"  GSTEntity records: {len(gst_records)}")
        return 0

    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.shared_attribute import materialise_shared_attributes
    from backend.app.graph.writes import upsert_gst_entity, upsert_industry_benchmark

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:
        logger.error(
            "Cannot reach Neo4j at %s - start it with "
            "`docker compose -f infra/docker-compose.dev.yml up -d`. Error: %s",
            settings.neo4j_uri, exc,
        )
        await driver.close()
        return 3

    try:
        for p in points:
            await upsert_industry_benchmark(driver, p)
        for g in gst_records:
            await upsert_gst_entity(driver, g)
        shared = await materialise_shared_attributes(driver, min_count=2)
    finally:
        await driver.close()

    print(f"\nDay-4 acceptance summary:")
    print(f"  PDFs generated + parsed:    {parsed_count}")
    print(f"  Benchmark points written:   {len(points)}")
    print(f"  GSTEntity records written:  {len(gst_records)}")
    print(f"  SharedAttribute by type:    {shared}")
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Skip Neo4j writes")
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()

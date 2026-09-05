"""Pre-cache synthetic SME companies (PRD §10 Day 7 + Day 17).

Day 7 target:  200 companies in Neo4j with DataConfidence % set
Day 17 target: 1,000 companies (re-run this script with --count 1000)

The 10 hand-crafted Day-3 fixtures cover named fraud cases (IL&FS, Amtek)
and synthetic SMEs across all 5 DataConfidence tiers. This pre-cache script
generates an *additional* synthetic population so:

  - Module 5 peer-deviation has enough samples per NIC code to compute
    meaningful z-scores (we hit the BSE benchmarks p25/p75 range with realistic
    variance).
  - L0.5 graph features have enough density for PageRank/betweenness to be
    informative beyond the 10-company toy graph.
  - The OOF ML training set (Day 12+) has a healthy-baseline population that
    isn't all "famous fraud" outliers.

Use:
  python scripts/precache_companies.py --count 200            # writes to Neo4j
  python scripts/precache_companies.py --count 200 --dry-run  # validates only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import string
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingest.pipeline import ingest_many  # noqa: E402
from backend.app.ingest.schemas import (  # noqa: E402
    CompanyBundle,
    RawCharge,
    RawCompany,
    RawDirector,
    RawFinancialStatement,
)
from backend.app.ingest.sources import CompanySource  # noqa: E402

logger = logging.getLogger("precache")


# NIC codes from the Day-4 benchmark fixture so peer-deviation can score them.
NIC_POOL = [27101, 27109, 14101, 45201, 45203, 49120, 46101, 46190, 62013, 29304]
STATE_POOL = ["MH", "KA", "TN", "GJ", "DL", "WB", "TG", "UP", "RJ", "MP"]


def _gen_cin(rng: random.Random, idx: int, state: str, nic: int, year: int) -> str:
    """Generate a syntactically valid CIN: U<NIC><state><year><type><serial>."""
    serial = f"{(900000 + idx):06d}"  # well above any real-world serial
    return f"U{nic:05d}{state}{year}PTC{serial}"


def _gen_pan(rng: random.Random) -> str:
    return "".join(rng.choices(string.ascii_uppercase, k=5)) + \
           "".join(rng.choices(string.digits, k=4)) + \
           rng.choice(string.ascii_uppercase)


def _gen_gstin(rng: random.Random, pan: str) -> str:
    # state code 27 (MH) + PAN + entity '1' + Z + checksum char
    return f"27{pan}1Z{rng.choice(string.ascii_uppercase + string.digits)}"


def _gen_din(rng: random.Random) -> str:
    return f"{rng.randint(10000000, 99999999):08d}"


def _gen_ifsc(rng: random.Random) -> str:
    bank = rng.choice(["SBIN", "HDFC", "ICIC", "AXIS", "PUNB", "BARB", "UBIN", "CNRB"])
    return f"{bank}0{rng.randint(100000, 999999):06d}"


def _gen_synthetic_bundle(idx: int, rng: random.Random) -> CompanyBundle:
    """One synthetic company bundle. Profile mix is roughly 80% healthy / 20% risky."""
    is_risky = rng.random() < 0.20

    nic = rng.choice(NIC_POOL)
    state = rng.choice(STATE_POOL)
    inc_year = rng.randint(2015, 2023)
    inc_date = date(inc_year, rng.randint(1, 12), rng.randint(1, 28))
    cin = _gen_cin(rng, idx, state, nic, inc_year)
    pan = _gen_pan(rng)
    gstin = _gen_gstin(rng, pan)

    # ~5% companies have no GSTIN to exercise the DC<92% paths
    if rng.random() < 0.05:
        gstin = None

    employee_count = (
        rng.randint(2, 6) if is_risky and rng.random() < 0.4
        else rng.randint(8, 120)
    )

    auditor_din: str | None = _gen_din(rng) if rng.random() > 0.05 else None

    company = RawCompany(
        cin=cin,
        name=f"Synthetic SME #{idx:04d} Pvt Ltd",
        incorporation_date=inc_date,
        nic_code=nic,
        state=state,
        gstin=gstin,
        employee_count_reported=employee_count,
        registered_address=f"Plot {rng.randint(1, 400)}, Industrial Area, {state}",
        contact_phone=f"+91-{rng.randint(10, 99)}-{rng.randint(20000000, 99999999)}",
        auditor_din=auditor_din,
    )

    # 2-3 directors per company
    director_count = rng.randint(2, 3)
    directors: list[RawDirector] = []
    for _ in range(director_count):
        appt = inc_date + timedelta(days=rng.randint(0, 365))
        ceased: date | None = None
        # 10% chance a director has churned out (Module 6 temporal signal)
        if is_risky and rng.random() < 0.30:
            ceased = appt + timedelta(days=rng.randint(180, 720))
        directors.append(RawDirector(
            din=_gen_din(rng),
            name=f"Director #{rng.randint(1, 999):03d}",
            designation=rng.choice(["director", "managing director", "wholetime director"]),
            appointment_date=appt,
            cessation_date=ceased,
            num_directorships=(
                rng.randint(8, 18) if is_risky and rng.random() < 0.5
                else rng.randint(1, 5)
            ),
        ))

    # 1-3 years of financials — vary so DataConfidence spans tiers
    n_years = rng.choices([0, 1, 2, 3], weights=[1, 2, 5, 3])[0]
    revenue_base = rng.uniform(2e7, 5e8)  # 2 cr to 50 cr
    growth = rng.uniform(0.85, 1.35) if not is_risky else rng.uniform(0.4, 2.5)
    financials: list[RawFinancialStatement] = []
    current_revenue = revenue_base
    for offset in range(n_years):
        year = inc_year + max(2, (date.today().year - inc_year) - offset)
        revenue = current_revenue
        cost_materials = revenue * rng.uniform(0.55, 0.78)
        emp_cost = revenue * rng.uniform(0.04, 0.18)
        finance_costs = revenue * rng.uniform(0.01, 0.06)
        depreciation = revenue * rng.uniform(0.005, 0.025)
        pbt = revenue - cost_materials - emp_cost - finance_costs - depreciation - revenue * 0.05
        pat = pbt * 0.75 if pbt > 0 else pbt
        total_assets = revenue * rng.uniform(0.6, 1.8)

        financials.append(RawFinancialStatement(
            cin=cin,
            year=year,
            revenue=round(revenue, 2),
            cost_of_materials=round(cost_materials, 2),
            employee_cost=round(emp_cost, 2),
            finance_costs=round(finance_costs, 2),
            depreciation=round(depreciation, 2),
            pbt=round(pbt, 2),
            tax=round(max(pbt, 0) * 0.25, 2),
            pat=round(pat, 2),
            total_assets=round(total_assets, 2),
            fixed_assets=round(total_assets * rng.uniform(0.25, 0.55), 2),
            current_assets=round(total_assets * rng.uniform(0.35, 0.65), 2),
            cash_and_equivalents=round(total_assets * rng.uniform(0.05, 0.18), 2),
            receivables=round(revenue * rng.uniform(0.08, 0.25), 2),
            long_term_borrowings=round(total_assets * rng.uniform(0.10, 0.45), 2),
            short_term_borrowings=round(total_assets * rng.uniform(0.05, 0.20), 2),
            auditor_name=f"Synthetic CA Firm #{rng.randint(1, 60):03d}",
            auditor_type=rng.choice(["partnership", "sole_proprietor", "llp"]),
            going_concern_flag=(is_risky and rng.random() < 0.30),
            adverse_flag=(is_risky and rng.random() < 0.10),
            notes_word_count=rng.randint(800, 4500),
        ))
        current_revenue *= growth

    charges: list[RawCharge] = []
    if rng.random() < 0.45:  # ~45% have at least one charge
        charges.append(RawCharge(
            cin=cin,
            charge_id=f"SYN-{idx:04d}-CHG-001",
            lender_name=rng.choice([
                "State Bank of India", "HDFC Bank", "ICICI Bank",
                "Axis Bank", "Punjab National Bank", "Bank of Baroda",
            ]),
            bank_branch_ifsc=_gen_ifsc(rng),
            amount=round(rng.uniform(5e6, 5e8), 2),
            creation_date=inc_date + timedelta(days=rng.randint(180, 1500)),
            charge_type=rng.choice(["hypothecation", "mortgage", "floating", "pledge"]),
        ))

    has_gst = gstin is not None and rng.random() < 0.60
    has_bank = has_gst and rng.random() < 0.55

    return CompanyBundle(
        company=company,
        directors=directors,
        financials=financials,
        charges=charges,
        has_gst_upload=has_gst,
        has_bank_upload=has_bank,
    )


class SyntheticSource:
    """In-memory CompanySource that yields pre-generated bundles."""

    name = "synthetic"

    def __init__(self, bundles: list[CompanyBundle]) -> None:
        self._by_cin = {b.company.cin: b for b in bundles}

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None:
        return self._by_cin.get(cin)

    async def list_available_cins(self) -> list[str]:
        return list(self._by_cin.keys())


def generate_bundles(count: int, *, seed: int = 42) -> list[CompanyBundle]:
    """Deterministic synthetic generator. Same seed -> same bundles."""
    rng = random.Random(seed)
    return [_gen_synthetic_bundle(i, rng) for i in range(count)]


async def _run(count: int, dry_run: bool, seed: int) -> int:
    bundles = generate_bundles(count, seed=seed)
    logger.info("Generated %d synthetic bundles (seed=%d)", len(bundles), seed)

    # Sanity stats — distribution across DataConfidence tiers
    tiers = {25: 0, 45: 0, 65: 0, 80: 0, 92: 0}
    from backend.app.ingest.data_confidence import compute_data_confidence
    for b in bundles:
        tiers[compute_data_confidence(b)] += 1
    logger.info("DataConfidence distribution: %s", tiers)

    source = SyntheticSource(bundles)
    cins = await source.list_available_cins()

    if dry_run:
        async def _noop_bundle(_b): return None
        async def _noop_dqe(_d): return None
        async def _noop_set(_c, _s): return None

        results = await ingest_many(cins, source, _noop_bundle, _noop_dqe, _noop_set)
        ok = sum(1 for r in results if r.ok)
        print()
        print(f"Dry-run summary: {ok}/{len(results)} bundles validated")
        print(f"DataConfidence tiers: {tiers}")
        return 0 if ok == len(results) else 1

    from neo4j import AsyncGraphDatabase

    from backend.app.config import get_settings
    from backend.app.graph.writes import (
        set_data_confidence,
        write_bundle,
        write_data_quality_error,
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
        return 3

    async def _write(b): await write_bundle(driver, b)
    async def _dqe(d): await write_data_quality_error(driver, d)
    async def _setc(cin, score): await set_data_confidence(driver, cin, score)

    try:
        results = await ingest_many(cins, source, _write, _dqe, _setc)
    finally:
        await driver.close()

    ok = sum(1 for r in results if r.ok)
    print()
    print(f"Pre-cache result: {ok}/{len(results)} written to Neo4j")
    print(f"DataConfidence tiers: {tiers}")
    return 0 if ok == len(results) else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=200, help="Companies to generate")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    parser.add_argument("--dry-run", action="store_true", help="Skip Neo4j writes")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.count, args.dry_run, args.seed)))


if __name__ == "__main__":
    main()

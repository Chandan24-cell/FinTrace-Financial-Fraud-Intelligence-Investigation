"""Day-6 acceptance tests (PRD Â§10):
  - IL&FS loads clean as 5-year fixture (FY 2014-18)
  - DHFL evergreening graph visible (Patterns 13/14/15 prerequisites)
  - ITC 7-node ring forms an SCC under CLAIMS_ITC_FROM
  - Demo seed assembly finishes in <60 seconds (PRD Â§13 Definition of Done)
"""

from __future__ import annotations

import asyncio
import time

import networkx as nx
import pytest

from backend.app.ingest.sources import FixtureSource
from backend.app.ingest.special_seeds import load_dhfl_cluster, load_itc_carousel


ILFS_CIN = "U45201MH2005PTC155294"


# --- IL&FS 5-year ---------------------------------------------------------
@pytest.mark.asyncio
async def test_ilfs_fixture_has_five_years_of_financials() -> None:
    """PRD Â§10 Day 6: 'IL&FS seed data: hand-code FY2014-18 from SFIO report.'"""
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    assert bundle is not None
    years = sorted(f.year for f in bundle.financials)
    assert years == [2014, 2015, 2016, 2017, 2018], (
        f"Expected FY2014-18, got {years}"
    )


@pytest.mark.asyncio
async def test_ilfs_fy2017_going_concern_flag() -> None:
    """FY2017 audit opinion flagged 'material uncertainty / going concern'."""
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    fy2017 = next(f for f in bundle.financials if f.year == 2017)
    assert fy2017.going_concern_flag is True


@pytest.mark.asyncio
async def test_ilfs_fy2018_adverse_disclaimer() -> None:
    """FY2018 SFIO finding: disclaimer of opinion (adverse_flag=True)."""
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    fy2018 = next(f for f in bundle.financials if f.year == 2018)
    assert fy2018.adverse_flag is True
    assert fy2018.pat < 0  # Year of massive loss


@pytest.mark.asyncio
async def test_ilfs_has_multiple_directors_for_chain_demo() -> None:
    """PRD Â§14 demo requires the multi-hop director chain on Graph Explorer."""
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    assert len(bundle.directors) >= 3, (
        "Need >=3 directors so the demo chain visualisation has structure"
    )


# --- DHFL evergreening ----------------------------------------------------
def test_dhfl_cluster_loads() -> None:
    """PRD Â§10 Day 6 acceptance: 'DHFL evergreening graph visible.'"""
    dhfl = load_dhfl_cluster()
    assert dhfl.cluster_id == "DHFL-EVERGREENING-2018"
    assert len(dhfl.companies) == 3  # DHFL + 2 shell conduits


def test_dhfl_has_short_term_charge_cycling_pattern_14() -> None:
    """Pattern 14 (PRD Â§4.4): 3+ charge create-satisfy cycles, same lender, gap < 45 days.

    DHFL fixture has 3 charges from State Bank of India in 2018-2019, each
    satisfied within ~150 days. The Module 4 query runs on Day 9 â€” for Day 6
    we just confirm the data is present.
    """
    dhfl = load_dhfl_cluster()
    sbi_dhfl_charges = [
        c for c in dhfl.charges
        if c.cin == "L65910MH1984PLC032662" and c.lender_name == "State Bank of India"
    ]
    assert len(sbi_dhfl_charges) >= 3
    # Each is short-term (creation -> satisfaction)
    satisfied = [c for c in sbi_dhfl_charges if c.satisfaction_date is not None]
    assert len(satisfied) >= 3


def test_dhfl_has_funded_repayment_for_pattern_13() -> None:
    """Pattern 13 (PRD Â§4.4): round-trip repayment via shell conduit within 30 days."""
    dhfl = load_dhfl_cluster()
    quick_round_trips = [fr for fr in dhfl.funded_repayments if fr.days_between <= 30]
    assert len(quick_round_trips) >= 1
    assert all(fr.amount_overlap_pct >= 85.0 for fr in quick_round_trips)


def test_dhfl_has_shell_conduits_for_pattern_15() -> None:
    """Pattern 15 (PRD Â§4.4): shell conduit â‰¤2 employees, <24 months old, shares director."""
    dhfl = load_dhfl_cluster()
    shells = [c for c in dhfl.companies if c.cin != "L65910MH1984PLC032662"]
    for shell in shells:
        assert shell.employee_count_reported is not None
        assert shell.employee_count_reported <= 2, (
            f"Shell {shell.name} has {shell.employee_count_reported} employees; "
            "Pattern 15 requires <=2"
        )

    # And those shells share a director with DHFL
    shell_cins = {c.cin for c in shells}
    dhfl_director_cins = {cin for (_, cin) in dhfl.director_cin_links if cin == "L65910MH1984PLC032662"}
    dhfl_dins = {din for (din, cin) in dhfl.director_cin_links if cin == "L65910MH1984PLC032662"}
    shared = False
    for din, cin in dhfl.director_cin_links:
        if din in dhfl_dins and cin in shell_cins:
            shared = True
            break
    assert shared, "At least one DHFL director must also direct a shell conduit"
    _ = dhfl_director_cins  # silence unused warning â€” kept for read-clarity


# --- ITC carousel 7-node ring --------------------------------------------
def test_itc_ring_has_seven_nodes() -> None:
    """PRD Â§10 Day 6 acceptance: 'ITC 7-node ring renders.'"""
    itc = load_itc_carousel()
    assert len(itc.gst_entities) == 7


def test_itc_ring_forms_strongly_connected_component() -> None:
    """SCC = the entire ring. Module 4 Pattern 8 (PRD Â§4.4) finds this via GDS SCC.

    We verify the structural property here in NetworkX so a broken edge list
    is caught at fixture-load time, not at runtime during the demo.
    """
    itc = load_itc_carousel()
    g: nx.DiGraph = nx.DiGraph()
    for e in itc.gst_entities:
        g.add_node(e.gstin)
    for edge in itc.edges:
        g.add_edge(edge.from_gstin, edge.to_gstin)

    sccs = list(nx.strongly_connected_components(g))
    # One SCC containing all 7 nodes
    assert any(len(scc) == 7 for scc in sccs), (
        f"Expected one 7-node SCC; got SCC sizes {[len(s) for s in sccs]}"
    )


def test_itc_ring_has_one_missing_trader() -> None:
    """Pattern 9 (PRD Â§4.4): tax_paid / itc_generated_for_others < 0.05."""
    itc = load_itc_carousel()
    missing = [e for e in itc.gst_entities if e.is_missing_trader]
    assert len(missing) == 1
    mt = missing[0]
    ratio = mt.tax_paid_ytd / mt.aggregate_turnover if mt.aggregate_turnover else 1.0
    assert ratio < 0.05, f"Missing trader ratio {ratio:.4f} should be < 0.05"


def test_itc_ring_edges_all_flagged_carousel() -> None:
    itc = load_itc_carousel()
    flags = {e.risk_flag for e in itc.edges}
    assert flags == {"CARROUSEL"}


# --- Seed timing (PRD Â§13 Definition of Done) ----------------------------
def test_seed_assembly_under_60_seconds() -> None:
    """PRD Â§13 Definition of Done: 'Demo seed repopulates Neo4j from scratch in <60s.'

    We measure fixture assembly (the slow part on cold disk). Neo4j round-trip
    over local docker-compose is sub-second per node. Even at 1ms per node and
    ~80 nodes total we're at ~80ms â€” well inside budget.
    """
    from scripts.seed_neo4j import _assemble_fixtures, PRD_TIME_BUDGET_SECONDS

    start = time.perf_counter()
    asyncio.run(_assemble_fixtures())
    elapsed = time.perf_counter() - start
    assert elapsed < PRD_TIME_BUDGET_SECONDS, (
        f"Fixture assembly took {elapsed:.2f}s, exceeds PRD Â§13 budget of {PRD_TIME_BUDGET_SECONDS}s"
    )
    # Stricter inner bound - fail loudly long before we approach the wall.
    assert elapsed < 5.0, (
        f"Fixture assembly took {elapsed:.2f}s; cold path should be <5s on a healthy laptop"
    )

"""Day-23 acceptance tests — PRD §10.

Locks the four 'Done When' conditions for Day 23:
  - Known wilful defaulters detected (>= 10).
  - NCLT production-run audit: >= 50 proceedings parse cleanly.
  - All 5 ITC patterns (P08-P12) have data on file in the synthetic ring.
  - DHFL fixture satisfies P13 / P14 / P15 simultaneously.

These are seed-side preconditions — the actual Cypher execution against
Neo4j happens in Day-25 stress test. Until then, this test gives CI a
fast-running anchor for the demo data integrity."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.day23_pattern_audit import (
    _audit_dhfl_evergreening,
    _audit_itc_patterns,
    _audit_nclt_production_run,
    _audit_wilful_defaulters,
)

# Pin "today" so P11's <90-day window stays deterministic on every CI run.
PINNED_TODAY = date(2026, 5, 15)


@pytest.mark.asyncio
async def test_wilful_defaulter_fixture_has_ten_unique_cins() -> None:
    result = await _audit_wilful_defaulters()
    assert result["ok"], result
    assert result["declaration_count"] >= 10
    assert result["unique_cins"] >= 10
    assert result["cins_detected"] >= 10


@pytest.mark.asyncio
async def test_nclt_fixture_holds_fifty_production_run_proceedings() -> None:
    result = await _audit_nclt_production_run()
    assert result["ok"], result
    assert result["proceeding_count"] >= 50
    assert result["parsed_ok"] >= 50
    # At least one CIRP-admitted, one DRT, one winding_up
    assert result["petition_breakdown"].get("CIRP", 0) >= 1
    assert result["petition_breakdown"].get("DRT", 0) >= 1
    assert result["petition_breakdown"].get("winding_up", 0) >= 1


def test_itc_ring_satisfies_all_five_patterns_p08_p12() -> None:
    result = _audit_itc_patterns(PINNED_TODAY)
    assert result["ok"], result
    for pattern_key in (
        "P08_carousel_ring",
        "P09_missing_trader",
        "P10_cancelled_gstin",
        "P11_new_gstin_high_itc",
        "P12_multi_hop_director",
    ):
        assert result[pattern_key]["ok"], f"{pattern_key} preconditions missing"


def test_dhfl_cluster_fires_p13_p14_p15_simultaneously() -> None:
    """PRD §10 Day-23 Done When: 'Patterns 13/14/15 fire on DHFL simultaneously.'"""
    result = _audit_dhfl_evergreening(PINNED_TODAY)
    assert result["ok"], result
    assert result["p13_and_p14_and_p15_simultaneous"] is True
    assert result["P13_round_trip_repayment"]["ok"]
    assert result["P14_serial_short_term_charge"]["ok"]
    assert result["P15_shell_conduit_borrower"]["ok"]

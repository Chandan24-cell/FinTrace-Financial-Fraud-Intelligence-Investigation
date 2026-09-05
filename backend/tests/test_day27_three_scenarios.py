"""Day-27 three-scenario acceptance tests — PRD §10.

PRD §10 Day-27 Done When: 'All three demo scenarios work end-to-end.'

Re-uses the helpers in scripts/day27_rehearsal_multi.py so the budgets
and expected bands stay in lockstep. Each scenario must:
  - GET /analyse/{cin} -> 200 with the expected CRITICAL band
  - GET /analyse/{cin}/provenance -> 200 with at least one signal
  - GET /report/{cin} -> 200 PDF bytes

The ITC ring fixture is also asserted to be structurally intact (7
nodes / 7 edges / director_overlap block present).
"""

from __future__ import annotations

import pytest

from scripts.day27_rehearsal_multi import (
    AMTEK_CIN,
    ILFS_CIN,
    ITC_CAROUSEL_CINS,
    TOTAL_BUDGET_SEC,
    _build_client,
    _load_itc_ring,
    _scenario_full_flow,
)


@pytest.mark.asyncio
async def test_scenario_1_ilfs_clears_full_demo_flow() -> None:
    async with await _build_client() as client:
        result = await _scenario_full_flow(client, ILFS_CIN, expected_band="CRITICAL")
    assert result["ok"], result
    h = result["headline"]
    assert h["risk_band"] == "CRITICAL"
    assert h["fraud_risk_score"] >= 60.0     # CRITICAL_FLAG_FLOOR_SCORE
    assert h["evidence_signal_count"] >= 5
    assert h["report_bytes"] > 1000          # non-trivial PDF


@pytest.mark.asyncio
async def test_scenario_2_amtek_clears_full_demo_flow() -> None:
    async with await _build_client() as client:
        result = await _scenario_full_flow(client, AMTEK_CIN, expected_band="CRITICAL")
    assert result["ok"], result
    h = result["headline"]
    # Amtek is on NCLT CIRP + wilful-defaulter list — M9 forces score ≥ 75.
    assert h["fraud_risk_score"] >= 75.0
    assert h["risk_band"] == "CRITICAL"
    assert h["evidence_signal_count"] >= 2


@pytest.mark.asyncio
async def test_scenario_3_itc_carousel_three_member_cards_render() -> None:
    async with await _build_client() as client:
        for cin in ITC_CAROUSEL_CINS:
            result = await _scenario_full_flow(client, cin, expected_band="CRITICAL")
            assert result["ok"], (cin, result)
            assert result["headline"]["risk_band"] == "CRITICAL"


def test_scenario_3_itc_ring_fixture_loads_with_full_shape() -> None:
    ring = _load_itc_ring()
    assert ring["ok"], ring
    assert ring["node_count"] == 7
    # Multi-period seed: 7 directed-cycle (from, to) pairs × 2 periods = 14.
    # The structural cycle is still 7-node; day23_pattern_audit dedupes
    # before checking the SCC invariant.
    assert ring["edge_count"] == 14
    assert ring["director_overlap_present"] is True


@pytest.mark.asyncio
async def test_three_scenarios_fit_total_budget() -> None:
    """Sanity belt: all three scenarios should clear the 3-minute total."""
    import time
    started = time.perf_counter()
    async with await _build_client() as client:
        await _scenario_full_flow(client, ILFS_CIN, expected_band="CRITICAL")
        await _scenario_full_flow(client, AMTEK_CIN, expected_band="CRITICAL")
        for cin in ITC_CAROUSEL_CINS:
            await _scenario_full_flow(client, cin, expected_band="CRITICAL")
    elapsed = time.perf_counter() - started
    assert elapsed <= TOTAL_BUDGET_SEC, f"Total {elapsed:.2f}s exceeded {TOTAL_BUDGET_SEC}s"

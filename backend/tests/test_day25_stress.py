"""Day-25 stress tests — PRD §10.

Locks the two Day-25 'Done When' conditions:
  - SCC time documented on a 10K-node synthetic graph (under the budget).
  - 10 concurrent /analyse requests complete without timeout or 5xx.

The actual measurement script lives at scripts/day25_stress_test.py; this
test re-imports its helpers so the budgets stay in lockstep."""

from __future__ import annotations

import pytest

from scripts.day25_stress_test import (
    CONCURRENT_REQUESTS,
    SCC_TIME_BUDGET_SEC,
    _run_concurrency_stress,
    _run_scc_stress,
)


def test_scc_on_10k_node_graph_under_budget() -> None:
    """PRD §10 Day-25 Done When: 'SCC time documented.'"""
    result = _run_scc_stress()
    assert result["node_count"] == 10_000
    assert result["ok"], result
    assert result["elapsed_sec"] < SCC_TIME_BUDGET_SEC
    # Sanity: the embedded 100 carousel rings (size 10 each) should
    # show up as ≥100 non-trivial SCCs.
    assert result["nontrivial_scc_count"] >= 100


@pytest.mark.asyncio
async def test_ten_concurrent_analyse_requests_complete_without_timeout() -> None:
    """PRD §10 Day-25 Done When: '10 concurrent requests complete without timeout.'"""
    result = await _run_concurrency_stress()
    assert result["ok"], result
    assert len(result["statuses"]) == CONCURRENT_REQUESTS
    # No 5xx, no errors.
    assert all(s < 500 for s in result["statuses"]), result["statuses"]
    assert result["errors"] == []
    # Each individual request should clear in well under the timeout.
    assert result["p99_sec"] < 5.0

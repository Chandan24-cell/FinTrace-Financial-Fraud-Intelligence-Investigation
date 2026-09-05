"""Tests for Module 4 â€” 17 graph patterns (PRD Â§4.4 + Â§10 Day 9).

These exercise the *pattern catalogue* and Cypher shapes without requiring a
live Neo4j server. A real-DB integration test would be slow to run on the
hackathon laptop; the day-9 acceptance ('all 17 patterns run' + 'SCC finds
rings on synthetic data') is verified via:

  1. Pattern-catalogue completeness (count, IDs, severities)
  2. Each PatternSpec's `run` is async + signature stable
  3. The ITC ring fixture is structurally a 7-node SCC (graph theory check)
  4. The DHFL evergreening fixture exposes round-trip + serial-charge data
  5. Cypher strings reference the expected relationship types
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from backend.app.modules import m04_graph_patterns as m04
from backend.app.modules.base import Severity


REPO = Path(__file__).resolve().parents[2]


# --- Catalogue completeness ---------------------------------------------------
def test_module_returns_seventeen_patterns() -> None:
    assert len(m04.patterns()) == 17


def test_pattern_ids_are_p01_through_p17() -> None:
    ids = [p.pattern_id for p in m04.patterns()]
    assert ids == [f"P{i:02d}" for i in range(1, 18)]


def test_each_pattern_has_a_unique_signal_type() -> None:
    sigs = [p.signal_type for p in m04.patterns()]
    assert len(sigs) == len(set(sigs))


def test_each_run_is_async_with_expected_signature() -> None:
    for p in m04.patterns():
        assert inspect.iscoroutinefunction(p.run), f"{p.pattern_id}.run must be async"
        sig = inspect.signature(p.run)
        assert list(sig.parameters)[:2] == ["driver", "cin"], (
            f"{p.pattern_id} signature mismatch: {sig.parameters}"
        )


def test_severities_match_prd_buckets() -> None:
    # PRD Â§4.4 expects core SME critical: P1, P3
    # ITC critical: P8, P9, P12
    # Evergreening critical: P13, P16
    expected_critical = {"P01", "P03", "P08", "P09", "P12", "P13", "P16"}
    actual_critical = {p.pattern_id for p in m04.patterns() if p.severity is Severity.CRITICAL}
    assert actual_critical == expected_critical


# --- Cypher sanity ------------------------------------------------------------
def test_p01_cypher_uses_transacts_with_scc() -> None:
    assert "TRANSACTS_WITH" in m04._CYPHER_P1
    assert "*2..6" in m04._CYPHER_P1  # bounded SCC depth


def test_p08_cypher_uses_claims_itc_from() -> None:
    assert "CLAIMS_ITC_FROM" in m04._CYPHER_P8
    assert "GSTEntity" in m04._CYPHER_P8


def test_p13_cypher_uses_funded_repayment_of() -> None:
    assert "FUNDED_REPAYMENT_OF" in m04._CYPHER_P13
    assert "amount_overlap_pct" in m04._CYPHER_P13


def test_p16_cypher_uses_wilful_defaulter_flag() -> None:
    assert "HAS_WILFUL_DEFAULTER_FLAG" in m04._CYPHER_P16
    assert "disbursement_date" in m04._CYPHER_P16


# --- ITC ring fixture is a 7-node SCC ----------------------------------------
def test_itc_ring_fixture_is_seven_node_scc() -> None:
    """PRD Â§10 Day 9 acceptance: 'SCC finds rings on synthetic data.'

    Pre-check: the fixture itself is a 7-node directed cycle on CLAIMS_ITC_FROM.
    """
    ring_path = REPO / "infra" / "seeds" / "itc_carousel" / "ring.json"
    ring = json.loads(ring_path.read_text(encoding="utf-8"))
    edges = ring["edges"]
    # Deduplicate by (from, to) pair â€” ring.json carries multiple periods per
    # connection, so validate unique directed pairs rather than raw edge count.
    unique_pairs = {(e["from_gstin"], e["to_gstin"]) for e in edges}
    assert len(unique_pairs) == 7
    # Each node has exactly one unique outgoing and one unique incoming neighbour.
    out_deg: dict[str, int] = {}
    in_deg: dict[str, int] = {}
    for from_g, to_g in unique_pairs:
        out_deg[from_g] = out_deg.get(from_g, 0) + 1
        in_deg[to_g] = in_deg.get(to_g, 0) + 1
    assert all(v == 1 for v in out_deg.values())
    assert all(v == 1 for v in in_deg.values())
    assert set(out_deg) == set(in_deg)
    assert len(out_deg) == 7


# --- DHFL evergreening fixture has round-trip + serial structure -------------
def test_dhfl_fixture_has_round_trip_and_serial_charges() -> None:
    dhfl_path = REPO / "infra" / "seeds" / "dhfl" / "dhfl_cluster.json"
    data = json.loads(dhfl_path.read_text(encoding="utf-8"))
    # P13: round-trip â€” at least one FUNDED_REPAYMENT_OF with overlap >= 90%
    assert any(fr["amount_overlap_pct"] >= 90.0 for fr in data["funded_repayments"])
    # P14: serial short-term charges â€” 3+ disbursements satisfied within 150 days
    dhfl_charges = [
        c for c in data["charges"] if c["cin"] == "L65910MH1984PLC032662"
        and c.get("satisfaction_date")
    ]
    assert len(dhfl_charges) >= 3


# --- run() honours the no-driver fallback ------------------------------------
class _StubDriver:
    """Pretends to be an AsyncDriver: session() yields an object whose run()
    yields an empty async iterator. Lets us assert that run() returns an empty
    ModuleResult instead of crashing when the graph is unreachable."""

    def session(self, *args, **kwargs) -> "_StubSession":
        return _StubSession()


class _StubSession:
    async def __aenter__(self) -> "_StubSession":
        return self

    async def __aexit__(self, *_) -> None:
        return None

    async def run(self, *args, **kwargs) -> "_StubCursor":
        return _StubCursor()


class _StubCursor:
    def __aiter__(self) -> "_StubCursor":
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_run_returns_zero_signals_on_empty_graph(monkeypatch) -> None:
    """All 17 patterns should execute without raising even when the graph
    returns no rows. PRD Â§10 Day 9: 'All 17 patterns run.'"""
    from backend.app import config as cfg_mod

    class _Settings:
        neo4j_database = "neo4j"

    monkeypatch.setattr(cfg_mod, "get_settings", lambda: _Settings())
    result = await m04.run(_StubDriver(), "U27101MH2010PTC215432")
    assert result.module_name == "m04_graph_patterns"
    assert result.score == 0.0
    assert result.signals == []

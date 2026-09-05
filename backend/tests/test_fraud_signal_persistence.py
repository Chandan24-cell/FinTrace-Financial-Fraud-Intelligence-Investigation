"""FraudSignal persistence + provenance round-trip — Stream 3.

The real Neo4j driver lives behind `AsyncDriver`. Spinning up a
container per test is too slow for the inner loop, so this test uses
a lightweight in-memory fake that records every Cypher invocation +
returns deterministic rows. Two contracts under test:

  1. `persist_fraud_signals(driver, cin, signals)` issues exactly one
     `_UPSERT_FRAUD_SIGNAL` per signal and one
     `_LINK_TRIGGERED_BY_*` per triggered_by ref. Idempotent calls
     don't double-write (verified by the MERGE semantics of the
     underlying Cypher — we assert call count, not graph state).

  2. `read_provenance_for_cin(driver, cin)` reconstructs the
     `{signal_count, signals, triggered_by}` shape from a fake row
     stream so the route can use it without re-running the scorer.

The driver-None fallback is covered by the existing /provenance
tests in test_analyse_route.py — those keep passing unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.graph.writes import (
    _year_from_signal,
    persist_fraud_signals,
    read_provenance_for_cin,
)
from backend.app.modules.base import FraudSignal, Severity


# ---------------------------------------------------------------------------
# Fake AsyncDriver — just enough to satisfy the calls we make.
# ---------------------------------------------------------------------------

class _FakeRow(dict):
    def data(self) -> dict:
        return dict(self)


class _FakeAsyncIter:
    """Async iterator over a fixed list of records (mimics neo4j Result)."""

    def __init__(self, rows: list[dict]):
        self._rows = list(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return _FakeRow(self._rows.pop(0))

    async def single(self):
        return _FakeRow(self._rows[0]) if self._rows else None


class _FakeSession:
    def __init__(self, parent: "_FakeDriver"):
        self._parent = parent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, cypher: str, **params) -> _FakeAsyncIter:
        self._parent.calls.append((cypher.strip()[:80], params))
        # Return whatever rows the test has staged for this Cypher prefix.
        for prefix, rows in self._parent.staged.items():
            if cypher.strip().startswith(prefix):
                return _FakeAsyncIter(rows)
        # Default: empty result.
        return _FakeAsyncIter([])


class _FakeDriver:
    """Minimal AsyncDriver substitute. Records every `session.run(...)`
    call as (cypher_prefix, params). Tests assert on `.calls`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.staged: dict[str, list[dict]] = {}

    def session(self, *, database: str | None = None) -> _FakeSession:
        return _FakeSession(self)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _signal(*, module: str, severity: Severity, evidence: str,
            triggered_by: list[dict] | None = None) -> FraudSignal:
    return FraudSignal(
        signal_type=f"{module.upper()}_DEMO",
        severity=severity,
        score_contribution=80.0,
        evidence_string=evidence,
        module_name=module,
        triggered_by=list(triggered_by or []),
    )


CIN = "U45201MH2005PTC155294"


# ---------------------------------------------------------------------------
# 1. `_year_from_signal` — small but load-bearing.
# ---------------------------------------------------------------------------

def test_year_from_signal_returns_fs_year_when_present():
    s = _signal(
        module="m01_beneish", severity=Severity.HIGH,
        evidence="M-Score 4.1 exceeds cutoff.",
        triggered_by=[{"label": "FinancialStatement", "cin": CIN, "year": 2017}],
    )
    assert _year_from_signal(s) == 2017


def test_year_from_signal_returns_none_when_no_fs_ref():
    s = _signal(
        module="m09_nclt", severity=Severity.CRITICAL,
        evidence="NCLT admitted CIRP.",
        triggered_by=[{"label": "Company", "cin": CIN}],
    )
    assert _year_from_signal(s) is None


def test_year_from_signal_handles_empty_triggered_by():
    s = _signal(module="m11_anomaly", severity=Severity.MEDIUM, evidence="x")
    assert _year_from_signal(s) is None


# ---------------------------------------------------------------------------
# 2. `persist_fraud_signals` — call accounting.
# ---------------------------------------------------------------------------

def test_persist_returns_zero_for_empty_signal_list():
    driver = _FakeDriver()
    n = asyncio.run(persist_fraud_signals(driver, CIN, []))
    assert n == 0
    assert driver.calls == []


def test_persist_emits_one_upsert_per_signal_plus_triggered_by_link():
    driver = _FakeDriver()
    signals = [
        _signal(
            module="m02_cross_statement", severity=Severity.CRITICAL,
            evidence="Revenue exceeds GST taxable turnover by 51.2%.",
            triggered_by=[{"label": "FinancialStatement", "cin": CIN, "year": 2017}],
        ),
        _signal(
            module="m09_nclt", severity=Severity.CRITICAL,
            evidence="NCLT admitted CIRP under IBC §7.",
            triggered_by=[{"label": "Company", "cin": CIN}],
        ),
    ]
    n = asyncio.run(persist_fraud_signals(driver, CIN, signals))
    assert n == 2

    # Expected calls: 2× upsert + 1× FS link + 1× Company link = 4
    cyphers = [c[0] for c in driver.calls]
    upsert_count = sum(1 for c in cyphers if c.startswith("MERGE (s:FraudSignal"))
    fs_link = sum(1 for c in cyphers if c.startswith("MATCH (s:FraudSignal"))
    assert upsert_count == 2
    assert fs_link == 2  # one per triggered_by ref (FS + Company)


def test_persist_swallows_neo4j_exception_and_returns_partial_count():
    """A 5xx-equivalent mid-write must not crash the request."""
    class _BoomDriver(_FakeDriver):
        def session(self, *, database=None):  # type: ignore[override]
            raise RuntimeError("Neo4j unreachable")

    n = asyncio.run(persist_fraud_signals(
        _BoomDriver(), CIN,
        [_signal(module="m01_beneish", severity=Severity.HIGH, evidence="x")],
    ))
    assert n == 0  # nothing written, no exception escapes


def test_persist_handles_all_five_triggered_by_label_types():
    driver = _FakeDriver()
    signal = _signal(
        module="m04_graph_patterns", severity=Severity.HIGH,
        evidence="Circular trading SCC.",
        triggered_by=[
            {"label": "Company", "cin": CIN},
            {"label": "Director", "din": "00012345"},
            {"label": "FinancialStatement", "cin": CIN, "year": 2017},
            {"label": "LoanDisbursement", "loan_id": "CHG-001"},
            {"label": "GSTEntity", "gstin": "27AAACX1234A1Z5"},
        ],
    )
    n = asyncio.run(persist_fraud_signals(driver, CIN, [signal]))
    assert n == 1
    # 1 upsert + 5 link Cyphers
    cyphers = [c[0] for c in driver.calls]
    assert len([c for c in cyphers if c.startswith("MERGE (s:FraudSignal")]) == 1
    assert len([c for c in cyphers if c.startswith("MATCH (s:FraudSignal")]) == 5


# ---------------------------------------------------------------------------
# 3. `read_provenance_for_cin` — shape contract.
# ---------------------------------------------------------------------------

def test_read_provenance_returns_none_when_no_rows():
    driver = _FakeDriver()  # default empty result
    out = asyncio.run(read_provenance_for_cin(driver, CIN))
    assert out is None


def test_read_provenance_flattens_signals_and_triggered_by():
    driver = _FakeDriver()
    driver.staged["MATCH (s:FraudSignal"] = [
        {
            "signal_id": "sig-1",
            "signal_type": "BENEISH_M_SCORE_BREACH",
            "severity": "HIGH",
            "score_contribution": 70.0,
            "evidence_string": "M-Score 4.1 exceeds cutoff 2.22.",
            "module_name": "m01_beneish",
            "refs": [
                {"label": "FinancialStatement", "props": {"cin": CIN, "year": 2017}},
            ],
        },
        {
            "signal_id": "sig-2",
            "signal_type": "NCLT_CIRP_ADMITTED",
            "severity": "CRITICAL",
            "score_contribution": 95.0,
            "evidence_string": "NCLT admitted CIRP on 2018-10-01.",
            "module_name": "m09_nclt",
            "refs": [{"label": "Company", "props": {"cin": CIN}}],
        },
    ]
    out = asyncio.run(read_provenance_for_cin(driver, CIN))
    assert out is not None
    assert out["cin"] == CIN
    assert out["signal_count"] == 2
    assert {s["signal_id"] for s in out["signals"]} == {"sig-1", "sig-2"}
    by = out["triggered_by"]
    labels = {(b["signal_id"], b["label"]) for b in by}
    assert ("sig-1", "FinancialStatement") in labels
    assert ("sig-2", "Company") in labels
    # `ref` should be the props dict, never include `label`
    for b in by:
        assert "label" not in (b["ref"] or {})


def test_read_provenance_drops_null_refs_from_optional_match():
    """Cypher OPTIONAL MATCH produces NULL elements when a signal has
    no TRIGGERED_BY edges — those must not leak as null triggered_by
    entries."""
    driver = _FakeDriver()
    driver.staged["MATCH (s:FraudSignal"] = [
        {
            "signal_id": "sig-empty",
            "signal_type": "X",
            "severity": "LOW",
            "score_contribution": 5.0,
            "evidence_string": "trivial",
            "module_name": "m11_anomaly",
            "refs": [None],
        },
    ]
    out = asyncio.run(read_provenance_for_cin(driver, CIN))
    assert out is not None
    assert out["signal_count"] == 1
    assert out["triggered_by"] == []


def test_read_provenance_swallows_cypher_exception():
    class _BoomDriver(_FakeDriver):
        def session(self, *, database=None):  # type: ignore[override]
            raise RuntimeError("Neo4j down")
    out = asyncio.run(read_provenance_for_cin(_BoomDriver(), CIN))
    assert out is None

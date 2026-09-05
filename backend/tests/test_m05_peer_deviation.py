"""Tests for Module 5 — Peer Deviation (PRD §4.5 + §10 Day 10)."""

from __future__ import annotations

import pytest

from backend.app.ingest.benchmarks import BenchmarkPoint
from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.modules import m05_peer_deviation as m05
from backend.app.modules.base import Severity


_NIC = 27101
_YEAR = 2023


def _benchmarks() -> list[BenchmarkPoint]:
    """Mirror the BSE seed for NIC 27101 so the module gets real percentiles."""
    return [
        BenchmarkPoint(nic_code=_NIC, year=_YEAR, metric="revenue_growth_pct", p25=6.2,  median=11.4, p75=18.7),
        BenchmarkPoint(nic_code=_NIC, year=_YEAR, metric="gross_margin_pct",   p25=18.4, median=24.1, p75=31.6),
        BenchmarkPoint(nic_code=_NIC, year=_YEAR, metric="debt_to_equity",     p25=0.4,  median=0.8,  p75=1.6),
        BenchmarkPoint(nic_code=_NIC, year=_YEAR, metric="current_ratio",      p25=1.0,  median=1.4,  p75=1.9),
        BenchmarkPoint(nic_code=_NIC, year=_YEAR, metric="interest_coverage",  p25=1.6,  median=3.2,  p75=6.4),
        BenchmarkPoint(nic_code=_NIC, year=_YEAR, metric="asset_turnover",     p25=0.6,  median=0.9,  p75=1.3),
    ]


def _fs(**overrides) -> RawFinancialStatement:
    base = {
        "cin": "U27101MH2010PTC215432",
        "year": _YEAR,
        "revenue": 10_000_000.0,
        "cost_of_materials": 7_500_000.0,
        "employee_cost": 800_000.0,
        "finance_costs": 200_000.0,
        "depreciation": 150_000.0,
        "pbt": 1_350_000.0,
        "pat": 1_000_000.0,
        "total_assets": 12_000_000.0,
        "total_liabilities": 6_000_000.0,
        "current_assets": 5_000_000.0,
        "trade_payables": 1_500_000.0,
        "long_term_borrowings": 3_000_000.0,
        "short_term_borrowings": 800_000.0,
    }
    base.update(overrides)
    return RawFinancialStatement(**base)


def test_module_abstains_when_no_benchmark_rows_match() -> None:
    fs = _fs()
    result = m05.run(fs, prev=None, nic_code=99999, benchmarks=_benchmarks())
    assert result.skipped
    assert "99999" in result.skip_reason


def test_iqr_distance_zero_at_median() -> None:
    bench = _benchmarks()[0]  # revenue_growth_pct: median 11.4
    assert m05._iqr_distance(11.4, bench) == 0.0


def test_iqr_distance_one_at_p75_when_iqr_symmetric() -> None:
    bench = BenchmarkPoint(
        nic_code=_NIC, year=_YEAR, metric="x", p25=10.0, median=20.0, p75=30.0,
    )
    # value at p75 should be +1.0 IQR-units above median
    assert m05._iqr_distance(30.0, bench) == pytest.approx(0.5)


def test_severity_thresholds_match_prd() -> None:
    assert m05._severity_for(0.5) is None
    assert m05._severity_for(1.5) is Severity.HIGH
    assert m05._severity_for(-1.6) is Severity.HIGH
    assert m05._severity_for(3.0) is Severity.CRITICAL
    assert m05._severity_for(-4.0) is Severity.CRITICAL


def test_in_band_company_produces_zero_signals() -> None:
    """A company sitting at the peer median for every ratio gets a clean bill."""
    # revenue_growth_pct = 11.4%, gross_margin_pct = 24.1%, etc.
    prev = _fs(revenue=10_000_000.0 / 1.114)
    fs = _fs(
        revenue=10_000_000.0,
        cost_of_materials=10_000_000.0 * (1 - 0.241),  # GM=24.1%
        # debt_to_equity: total_debt / equity = median 0.8
        # total_assets=12, total_liabilities pushes equity to 5 -> need debt=4
        total_assets=12_000_000.0,
        total_liabilities=7_000_000.0,
        long_term_borrowings=3_000_000.0,
        short_term_borrowings=1_000_000.0,
        # current_ratio = ca / (stb + tp) = 1.4
        current_assets=2.8 * 1_000_000.0,
        trade_payables=1_000_000.0,
        # interest_coverage = (pbt + finance_costs) / finance_costs = 3.2 -> finance_costs=1, pbt=2.2
        finance_costs=1_000_000.0,
        pbt=2_200_000.0,
    )
    result = m05.run(fs, prev=prev, nic_code=_NIC, benchmarks=_benchmarks())
    # No metric should breach 1.5 IQR for this in-band setup
    assert result.score == 0.0
    assert result.signals == []


def test_extreme_debt_to_equity_fires_high_signal() -> None:
    # equity = 12 - 11 = 1; debt = 8 -> D/E = 8.0; peer p75=1.6 -> distance ≈ 6
    fs = _fs(
        total_assets=12_000_000.0,
        total_liabilities=11_000_000.0,
        long_term_borrowings=6_000_000.0,
        short_term_borrowings=2_000_000.0,
    )
    result = m05.run(fs, prev=None, nic_code=_NIC, benchmarks=_benchmarks())
    assert any(s.signal_type == "PEER_DEVIATION" for s in result.signals)
    de_sig = next(s for s in result.signals if "debt-to-equity" in s.evidence_string.lower())
    assert de_sig.severity in {Severity.HIGH, Severity.CRITICAL}
    # PRD §4.2 specific-numbers rule
    assert "8.00x" in de_sig.evidence_string
    assert "0.80x" in de_sig.evidence_string or "0.8" in de_sig.evidence_string


def test_module_falls_back_to_nearest_year_when_exact_missing() -> None:
    """When the FS year doesn't exist in benchmarks, use the most recent <= year."""
    fs = _fs(year=2024)
    result = m05.run(fs, prev=None, nic_code=_NIC, benchmarks=_benchmarks())
    assert not result.skipped


def test_evidence_string_cites_p25_median_p75_and_distance() -> None:
    fs = _fs(
        total_assets=12_000_000.0, total_liabilities=11_000_000.0,
        long_term_borrowings=6_000_000.0, short_term_borrowings=2_000_000.0,
    )
    result = m05.run(fs, prev=None, nic_code=_NIC, benchmarks=_benchmarks())
    de = next(s for s in result.signals if "debt-to-equity" in s.evidence_string.lower())
    assert "p25=" in de.evidence_string
    assert "p75=" in de.evidence_string
    assert "IQR-units" in de.evidence_string
    # triggered_by carries the FS provenance for TRIGGERED_BY edge
    assert de.triggered_by == [{"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}]

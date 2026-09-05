"""Tests for Module 2 — Cross-Statement Consistency (PRD §4.2 + §10 Day 8).

PRD acceptance: "All 7 cross-statement checks fire with exact numbers."
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.ingest.gst import RawGSTEntity
from backend.app.ingest.schemas import RawCharge, RawFinancialStatement
from backend.app.modules import m02_cross_statement
from backend.app.modules.base import Severity
from backend.app.modules.m02_cross_statement import CrossStatementInputs


def _fs(**kwargs) -> RawFinancialStatement:
    base = {
        "cin": "U27101MH2010PTC215432",
        "year": 2023,
        "revenue": 1_000_000_000.0,
        "cost_of_materials": 600_000_000.0,
        "employee_cost": 100_000_000.0,
        "finance_costs": 50_000_000.0,
        "depreciation": 50_000_000.0,
        "other_expenses": 100_000_000.0,
        "pbt": 100_000_000.0,
        "pat": 75_000_000.0,
        "total_assets": 2_000_000_000.0,
        "fixed_assets": 800_000_000.0,
        "current_assets": 800_000_000.0,
        "cwip": 200_000_000.0,
        "cash_and_equivalents": 200_000_000.0,
        "receivables": 200_000_000.0,
        "long_term_borrowings": 600_000_000.0,
        "short_term_borrowings": 200_000_000.0,
        "cf_operating": 80_000_000.0,
    }
    base.update(kwargs)
    return RawFinancialStatement(**base)


def _gst(**kwargs) -> RawGSTEntity:
    base = {
        "gstin": "27AAACA7890B1Z2",
        "pan": "AAACA7890B",
        "cin": "U27101MH2010PTC215432",
        "registration_date": date(2010, 5, 4),
        "aggregate_turnover": 1_000_000_000.0,
    }
    base.update(kwargs)
    return RawGSTEntity(**base)


# --- All 7 checks fire on a deliberately constructed bad fixture ---------
def test_all_seven_checks_fire_on_bad_fixture() -> None:
    """PRD §10 Day 8 acceptance: 'All 7 cross-statement checks fire with exact numbers.'

    Constructs a single FinancialStatement plus side-inputs that intentionally
    fail every PRD §4.2 check, then verifies all 7 distinct signal types appear
    and every evidence string carries specific numbers.
    """
    # Current year — bad cash conversion, bad implied rate, bad CWIP, profitable
    curr = _fs(
        year=2023,
        revenue=2_000_000_000.0,             # 200 cr P&L
        cost_of_materials=1_200_000_000.0,
        finance_costs=4_000_000_000.0,        # implied rate way > 24%
        long_term_borrowings=8_000_000_000.0, # so finance_costs/borrowings = 0.5 -> 50%
        short_term_borrowings=0.0,
        pat=300_000_000.0,
        cf_operating=20_000_000.0,             # OCF/PAT = 0.067 << 0.5
        total_assets=10_000_000_000.0,
        cwip=900_000_000.0,                    # 9% > 5% threshold
        depreciation=100_000_000.0,
    )
    # Prior year — much smaller assets so YoY +>20% with low depreciation growth
    prev = _fs(
        year=2022,
        total_assets=7_000_000_000.0,          # 43% growth YoY (curr 10B vs 7B)
        depreciation=95_000_000.0,             # 5.3% growth — far below 50% of 43% (21.5%)
        revenue=1_800_000_000.0,
        cost_of_materials=1_100_000_000.0,
        finance_costs=120_000_000.0,
        long_term_borrowings=500_000_000.0,
        short_term_borrowings=200_000_000.0,
        cwip=600_000_000.0,
        pat=250_000_000.0,
        cf_operating=180_000_000.0,
    )
    # Two-years-back FS to satisfy CWIP 3-year history
    pprev = _fs(
        year=2021,
        total_assets=6_000_000_000.0,
        cwip=400_000_000.0,                    # 6.7% > 5%
        depreciation=80_000_000.0,
    )

    # GST shows much lower turnover than P&L -> revenue-vs-GST fires
    gst = _gst(aggregate_turnover=900_000_000.0)   # 55% below P&L revenue

    # CERSAI registers much more debt than the BS reports
    charges = [
        RawCharge(
            cin=curr.cin,
            charge_id=f"CHG-{i}",
            lender_name="State Bank of India",
            amount=4_000_000_000.0,
            creation_date=date(2023, 1, 1),
        )
        for i in range(3)
    ]   # total active = 12B > 1.5x the 8B reported borrowings

    bank_credits = 3_200_000_000.0   # 60% above the 2B revenue -> bank-vs-rev fires

    result = m02_cross_statement.run(CrossStatementInputs(
        current=curr,
        previous=prev,
        cwip_history=[curr, prev, pprev],
        gst_entity=gst,
        cersai_charges=charges,
        bank_credits_total=bank_credits,
    ))

    expected = {
        "CROSS_STMT_REVENUE_VS_GST",
        "CROSS_STMT_IMPLIED_INTEREST",
        "CROSS_STMT_CASH_CONVERSION",
        "CROSS_STMT_DEPRECIATION_LAG",
        "CROSS_STMT_CWIP_STALE",
        "CROSS_STMT_CERSAI_VS_DEBT",
        "CROSS_STMT_BANK_VS_REVENUE",
    }
    actual = {s.signal_type for s in result.signals}
    assert actual == expected, (
        f"Expected all 7 cross-statement checks to fire. "
        f"Missing: {expected - actual}. Unexpected: {actual - expected}"
    )

    # Every evidence string must include at least one currency-formatted number
    # OR a percentage with two decimals (the rate/ratio checks).
    for sig in result.signals:
        has_inr = "₹" in sig.evidence_string
        has_pct = "%" in sig.evidence_string
        has_decimal = any(f"{c}." in sig.evidence_string for c in "0123456789")
        assert has_inr or has_pct or has_decimal, (
            f"Evidence string for {sig.signal_type} lacks specific numbers: "
            f"{sig.evidence_string!r}"
        )


# --- Per-check unit tests --------------------------------------------------
def test_revenue_vs_gst_within_tolerance_does_not_fire() -> None:
    fs = _fs(revenue=1_000_000_000.0)
    gst = _gst(aggregate_turnover=970_000_000.0)  # 3% < 5% tolerance
    result = m02_cross_statement.run(CrossStatementInputs(current=fs, gst_entity=gst))
    types = {s.signal_type for s in result.signals}
    assert "CROSS_STMT_REVENUE_VS_GST" not in types


def test_implied_rate_in_band_does_not_fire() -> None:
    fs = _fs(finance_costs=120_000_000.0,
             long_term_borrowings=800_000_000.0,
             short_term_borrowings=200_000_000.0)
    # rate = 120 / 1000 = 12% — inside the [8, 24] band
    result = m02_cross_statement.run(CrossStatementInputs(current=fs))
    types = {s.signal_type for s in result.signals}
    assert "CROSS_STMT_IMPLIED_INTEREST" not in types


def test_cash_conversion_skips_loss_making_years() -> None:
    """Loss-making years bypass the cash-conversion check (PRD §4.2 #3 trigger
    condition requires PAT > 0)."""
    fs = _fs(pat=-100_000_000.0, cf_operating=-50_000_000.0)
    result = m02_cross_statement.run(CrossStatementInputs(current=fs))
    types = {s.signal_type for s in result.signals}
    assert "CROSS_STMT_CASH_CONVERSION" not in types


def test_zero_debt_with_active_charges_fires_critical() -> None:
    """Edge case: CERSAI shows charges but balance sheet reports zero debt.
    PRD-implied: this is structurally worse than the 15% threshold breach."""
    fs = _fs(long_term_borrowings=0.0, short_term_borrowings=0.0)
    charges = [RawCharge(
        cin=fs.cin, charge_id="X-1", lender_name="HDFC",
        amount=500_000_000.0, creation_date=date(2023, 1, 1),
    )]
    result = m02_cross_statement.run(CrossStatementInputs(
        current=fs, cersai_charges=charges,
    ))
    cersai_sigs = [s for s in result.signals if s.signal_type == "CROSS_STMT_CERSAI_VS_DEBT"]
    assert len(cersai_sigs) == 1
    assert cersai_sigs[0].severity == Severity.CRITICAL
    assert "zero borrowings" in cersai_sigs[0].evidence_string


def test_cwip_staleness_requires_three_year_history() -> None:
    """Only 2 years of history isn't enough to flag CWIP staleness."""
    curr = _fs(year=2023, cwip=200_000_000.0, total_assets=2_000_000_000.0)
    prev = _fs(year=2022, cwip=180_000_000.0, total_assets=1_800_000_000.0)
    result = m02_cross_statement.run(CrossStatementInputs(
        current=curr, cwip_history=[curr, prev],
    ))
    types = {s.signal_type for s in result.signals}
    assert "CROSS_STMT_CWIP_STALE" not in types


def test_module_returns_empty_signals_on_clean_inputs() -> None:
    fs = _fs(
        revenue=1_000_000_000.0,
        finance_costs=120_000_000.0,
        long_term_borrowings=600_000_000.0,
        short_term_borrowings=200_000_000.0,
        pat=100_000_000.0,
        cf_operating=80_000_000.0,
    )
    gst = _gst(aggregate_turnover=990_000_000.0)
    result = m02_cross_statement.run(CrossStatementInputs(current=fs, gst_entity=gst))
    assert result.signals == []
    assert result.score == 0.0


def test_score_clamped_to_hundred() -> None:
    """If somehow every check fires CRITICAL, score is still capped at 100."""
    curr = _fs(year=2023, revenue=1e10, finance_costs=1e10, long_term_borrowings=1.0,
               short_term_borrowings=0.0, pat=1e8, cf_operating=1.0,
               total_assets=10e9, depreciation=1e6, cwip=2e9)
    prev = _fs(year=2022, total_assets=1e9, depreciation=1e6, cwip=1e8)
    pprev = _fs(year=2021, total_assets=1e9, depreciation=1e6, cwip=2e8)
    gst = _gst(aggregate_turnover=1.0)
    charges = [RawCharge(
        cin=curr.cin, charge_id="X", lender_name="HDFC",
        amount=1e12, creation_date=date(2023, 1, 1),
    )]
    result = m02_cross_statement.run(CrossStatementInputs(
        current=curr, previous=prev,
        cwip_history=[curr, prev, pprev],
        gst_entity=gst, cersai_charges=charges,
        bank_credits_total=1e12,
    ))
    assert result.score == 100.0


def test_triggered_by_refs_present_for_provenance() -> None:
    """PRD §6: every FraudSignal must carry TRIGGERED_BY refs."""
    fs = _fs(revenue=2e9)
    gst = _gst(aggregate_turnover=8e8)  # huge gap -> revenue-vs-GST fires
    result = m02_cross_statement.run(CrossStatementInputs(current=fs, gst_entity=gst))
    sig = next(s for s in result.signals if s.signal_type == "CROSS_STMT_REVENUE_VS_GST")
    labels = {ref["label"] for ref in sig.triggered_by}
    assert "FinancialStatement" in labels
    assert "GSTEntity" in labels

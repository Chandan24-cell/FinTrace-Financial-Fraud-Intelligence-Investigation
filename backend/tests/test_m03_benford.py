"""Tests for Module 3 — Benford's Law (PRD §4.3 + §10 Day 9)."""

from __future__ import annotations

import math

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.modules import m03_benford
from backend.app.modules.base import Severity


def _benford_distributed(n: int) -> list[float]:
    """Sample n positive numbers whose first digits follow log10(1+1/d)."""
    return [10.0 ** ((i % 50) / 50.0 + 1.0) for i in range(n)]


def _digit9_heavy(n: int) -> list[float]:
    """Pathological: every value starts with 9 — Benford breaks immediately."""
    return [9_000_000.0 + i for i in range(n)]


def test_first_digit_strips_sign_and_decimal() -> None:
    assert m03_benford._first_digit(1234.56) == 1
    assert m03_benford._first_digit(-789.0) == 7
    assert m03_benford._first_digit(0.0042) == 4
    assert m03_benford._first_digit(0.0) is None
    assert m03_benford._first_digit(float("nan")) is None


def test_expected_probs_sum_to_one() -> None:
    assert math.isclose(sum(m03_benford.EXPECTED_FIRST_DIGIT_PROBS), 1.0, abs_tol=1e-9)


def test_benford_stats_returns_none_below_min_sample() -> None:
    assert m03_benford.benford_stats([1.0, 2.0, 3.0]) is None


def test_benford_stats_passes_on_clean_distribution() -> None:
    stats = m03_benford.benford_stats(_benford_distributed(200))
    assert stats is not None
    assert stats.n == 200
    # Synthetic Benford-shaped data should not fail the chi-square test.
    assert not stats.chi_square_p_lt_05
    assert stats.mad < m03_benford.NIGRINI_MAD_THRESHOLD


def test_benford_stats_fires_on_digit_9_heavy_data() -> None:
    stats = m03_benford.benford_stats(_digit9_heavy(200))
    assert stats is not None
    # All-9s should blow chi-square out of the water.
    assert stats.chi_square > m03_benford.CHI_SQUARE_CRIT_8DF_05
    assert stats.mad > m03_benford.NIGRINI_MAD_THRESHOLD


def test_run_skips_disabled_nics() -> None:
    fs = RawFinancialStatement(cin="U46101MH2017PTC289123", year=2023, revenue=100.0)
    result = m03_benford.run([fs] * 5, nic_code="46101")
    assert result.skipped
    assert "46101" in result.skip_reason or "PRD §4.3" in result.skip_reason


def test_run_abstains_below_min_sample() -> None:
    fs = RawFinancialStatement(cin="U27101MH2010PTC215432", year=2023, revenue=100.0)
    result = m03_benford.run([fs], nic_code="27101")
    assert result.skipped
    assert "50" in result.skip_reason


def test_run_fires_when_2_of_3_tests_fail() -> None:
    """Build a synthetic FS with line-items all starting with '9' to force a fire."""
    # 25 fields * 3 years = 75 numbers — above MIN_SAMPLE_SIZE.
    fs_list = []
    for y in (2021, 2022, 2023):
        fs_list.append(RawFinancialStatement(
            cin="U27101MH2010PTC215432", year=y,
            revenue=9_000_000.0, other_income=900_000.0,
            cost_of_materials=9_500_000.0, employee_cost=950_000.0,
            other_expenses=970_000.0, depreciation=980_000.0,
            finance_costs=920_000.0, pbt=910_000.0, pat=930_000.0,
            receivables=940_000.0, trade_payables=960_000.0,
            cash_and_equivalents=905_000.0, inventory=915_000.0,
            cwip=925_000.0, fixed_assets=935_000.0, current_assets=945_000.0,
            total_assets=9_750_000.0,
            long_term_borrowings=9_100_000.0, short_term_borrowings=920_000.0,
            cf_operating=905_500.0, cf_investing=-915_000.0, cf_financing=925_500.0,
        ))
    result = m03_benford.run(fs_list, nic_code="27101")
    assert not result.skipped
    assert len(result.signals) == 1
    sig = result.signals[0]
    assert sig.severity in {Severity.HIGH, Severity.CRITICAL}
    # PRD §4.2 specific-numbers rule
    assert "χ²" in sig.evidence_string
    assert "KS=" in sig.evidence_string
    assert "MAD=" in sig.evidence_string


def test_evidence_string_cites_specific_numbers() -> None:
    fs_list = []
    for y in (2021, 2022, 2023):
        fs_list.append(RawFinancialStatement(
            cin="U27101MH2010PTC215432", year=y,
            revenue=9_876_543.0, cost_of_materials=9_654_321.0,
            employee_cost=987_654.0, other_expenses=976_543.0,
            depreciation=965_432.0, finance_costs=954_321.0,
            pat=943_210.0, receivables=932_109.0,
            cash_and_equivalents=921_098.0, inventory=910_987.0,
            cwip=900_876.0, fixed_assets=890_765.0,
            current_assets=880_654.0, total_assets=8_870_543.0,
            long_term_borrowings=860_432.0, short_term_borrowings=850_321.0,
            cf_operating=840_210.0, cf_investing=-830_109.0, cf_financing=820_098.0,
        ))
    result = m03_benford.run(fs_list, nic_code="27101")
    sig = result.signals[0]
    assert "n=" in sig.evidence_string
    # Numeric values must appear — not generic phrasing
    assert any(ch.isdigit() for ch in sig.evidence_string)


def test_module_constants_match_prd() -> None:
    assert m03_benford.MIN_SAMPLE_SIZE == 50
    assert m03_benford.CHI_SQUARE_P_THRESHOLD == 0.05
    assert m03_benford.NIGRINI_MAD_THRESHOLD == 0.015
    assert m03_benford.BENFORD_DISABLED_NICS == frozenset({"46", "47", "19", "49", "55"})

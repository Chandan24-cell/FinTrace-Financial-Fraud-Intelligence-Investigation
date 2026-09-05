"""Module 1 — Beneish M-Score (PRD §4.1).

8 financial ratios + a single linear combination M-Score. M-Score > -1.78
indicates earnings manipulation (Beneish 1999). Requires two consecutive
years; with single-year data the module abstains.

The 8 ratios (and PRD §4.1 thresholds):

| Ratio | Formula                                                           | Threshold |
|-------|-------------------------------------------------------------------|-----------|
| DSRI  | (Receivables/Sales)_t / (Receivables/Sales)_{t-1}                 | > 1.31    |
| GMI   | GrossMargin_{t-1} / GrossMargin_t                                 | > 1.00    |
| AQI   | (1 − (CA+PPE)/TA)_t / (1 − (CA+PPE)/TA)_{t-1}                     | > 1.25    |
| SGI   | Sales_t / Sales_{t-1}                                             | > 1.60    |
| DEPI  | (Depr/(PPE+Depr))_{t-1} / (Depr/(PPE+Depr))_t                     | < 1.00    |
| SGAI  | (SGA/Sales)_t / (SGA/Sales)_{t-1}                                 | > 1.00    |
| LVGI  | ((LTD+CL)/TA)_t / ((LTD+CL)/TA)_{t-1}                             | > 1.00    |
| TATA  | (Income from operations − CFO) / Total assets                     | high +ve  |

M-Score = -4.84 + 0.92·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
         + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI

PRD §10 Day 8 acceptance: 'IL&FS passes Beneish threshold.'
"""

from __future__ import annotations

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
    fmt_inr,
    safe_div,
)

MODULE_NAME = "m01_beneish"
M_SCORE_THRESHOLD = -1.78  # PRD §4.1 verbatim


# Each per-ratio threshold from PRD §4.1, plus the score contribution if the
# ratio individually fires. The aggregate score also includes a "M-Score breach"
# signal worth +40 when the linear combination clears -1.78.
_RATIO_THRESHOLDS: dict[str, tuple[str, float, str, int]] = {
    "DSRI": (">", 1.31, "Days Sales Receivable Index — receivables growing faster than revenue (fake sales never collected)", 15),
    "GMI":  (">", 1.00, "Gross Margin Index — deteriorating margins increase manipulation incentive", 8),
    "AQI":  (">", 1.25, "Asset Quality Index — soft assets growing (capitalising operating expenses)", 12),
    "SGI":  (">", 1.60, "Sales Growth Index — unsustainable growth creates fabrication pressure", 10),
    "DEPI": ("<", 1.00, "Depreciation Index — reducing depreciation rate to inflate reported profit", 8),
    "SGAI": (">", 1.00, "SGA Expense Index — SGA growing faster than revenue", 5),
    "LVGI": (">", 1.00, "Leverage Index — rising debt raises covenant violation risk", 5),
    "TATA": (">", 0.031, "Total Accruals to Total Assets — earnings not backed by cash", 18),
}


def _gross_margin(fs: RawFinancialStatement) -> float:
    """(Revenue − COGS) / Revenue."""
    return safe_div(fs.revenue - fs.cost_of_materials, fs.revenue)


def _compute_ratios(curr: RawFinancialStatement, prev: RawFinancialStatement) -> dict[str, float]:
    """All 8 Beneish ratios. Missing inputs return 1.0 (neutral) so the M-Score
    formula still produces a finite number rather than NaN."""

    # DSRI — receivables / sales, ratio of ratios
    dsri = safe_div(
        safe_div(curr.receivables, curr.revenue),
        safe_div(prev.receivables, prev.revenue),
        default=1.0,
    )

    # GMI — gross margin t-1 over t (PRD §4.1 says >1 = bad)
    gmi = safe_div(_gross_margin(prev), _gross_margin(curr), default=1.0)

    # AQI — fraction of "soft" (non-current, non-PPE) assets
    curr_soft = safe_div(1 - safe_div(curr.current_assets + curr.fixed_assets, curr.total_assets), 1)
    prev_soft = safe_div(1 - safe_div(prev.current_assets + prev.fixed_assets, prev.total_assets), 1)
    aqi = safe_div(curr_soft, prev_soft, default=1.0)

    # SGI — sales growth
    sgi = safe_div(curr.revenue, prev.revenue, default=1.0)

    # DEPI — depreciation rate, prior over current (PRD: <1 = bad i.e. inflating)
    curr_dep_rate = safe_div(curr.depreciation, curr.fixed_assets + curr.depreciation)
    prev_dep_rate = safe_div(prev.depreciation, prev.fixed_assets + prev.depreciation)
    depi = safe_div(prev_dep_rate, curr_dep_rate, default=1.0)

    # SGAI — SG&A / sales. Our fixture rolls SG&A into other_expenses; treat
    # 'other_expenses' as SG&A proxy. With missing data, returns 1.0.
    curr_sga_ratio = safe_div(curr.other_expenses, curr.revenue)
    prev_sga_ratio = safe_div(prev.other_expenses, prev.revenue)
    sgai = safe_div(curr_sga_ratio, prev_sga_ratio, default=1.0)

    # LVGI — leverage ratio
    curr_lev = safe_div(
        curr.long_term_borrowings + curr.short_term_borrowings + curr.trade_payables,
        curr.total_assets,
    )
    prev_lev = safe_div(
        prev.long_term_borrowings + prev.short_term_borrowings + prev.trade_payables,
        prev.total_assets,
    )
    lvgi = safe_div(curr_lev, prev_lev, default=1.0)

    # TATA — accruals / TA. (Income from operations − CFO) / TA
    income_from_ops = curr.pbt + curr.finance_costs  # back out finance costs to approximate operating income
    tata = safe_div(income_from_ops - curr.cf_operating, curr.total_assets, default=0.0)

    return {
        "DSRI": dsri,
        "GMI": gmi,
        "AQI": aqi,
        "SGI": sgi,
        "DEPI": depi,
        "SGAI": sgai,
        "LVGI": lvgi,
        "TATA": tata,
    }


def _m_score(ratios: dict[str, float]) -> float:
    return (
        -4.84
        + 0.92 * ratios["DSRI"]
        + 0.528 * ratios["GMI"]
        + 0.404 * ratios["AQI"]
        + 0.892 * ratios["SGI"]
        + 0.115 * ratios["DEPI"]
        - 0.172 * ratios["SGAI"]
        + 4.679 * ratios["TATA"]
        - 0.327 * ratios["LVGI"]
    )


def _check_ratio(name: str, value: float) -> bool:
    op, threshold, _desc, _weight = _RATIO_THRESHOLDS[name]
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    raise ValueError(f"Unknown operator for {name}: {op}")


def run(
    curr: RawFinancialStatement,
    prev: RawFinancialStatement | None,
) -> ModuleResult:
    """Run Module 1 on year t (curr) vs year t-1 (prev).

    With no prev, the module abstains per PRD §4.1.
    """
    if prev is None:
        return ModuleResult.skipped_for(
            MODULE_NAME, curr.cin, curr.year,
            reason="Single-year data — Beneish requires two consecutive years",
        )

    if prev.year != curr.year - 1:
        return ModuleResult.skipped_for(
            MODULE_NAME, curr.cin, curr.year,
            reason=f"Non-consecutive years: prev={prev.year} curr={curr.year}",
        )

    ratios = _compute_ratios(curr, prev)
    m_score = _m_score(ratios)

    signals: list[FraudSignal] = []
    total_score = 0.0

    # Each individually-breached ratio fires its own MEDIUM/HIGH signal.
    for name, value in ratios.items():
        if _check_ratio(name, value):
            _op, threshold, desc, weight = _RATIO_THRESHOLDS[name]
            evidence = (
                f"Beneish {name} = {value:.3f} (threshold {_op} {threshold}) — {desc}. "
                f"Computed on FY{curr.year} vs FY{prev.year}."
            )
            signals.append(FraudSignal(
                signal_type=f"BENEISH_{name}_BREACH",
                severity=Severity.HIGH if name in {"DSRI", "TATA"} else Severity.MEDIUM,
                score_contribution=weight,
                evidence_string=evidence,
                module_name=MODULE_NAME,
                triggered_by=[
                    {"label": "FinancialStatement", "cin": curr.cin, "year": curr.year},
                    {"label": "FinancialStatement", "cin": prev.cin, "year": prev.year},
                ],
            ))
            total_score += weight

    # The aggregate M-Score breach itself is the headline finding (CRITICAL).
    if m_score > M_SCORE_THRESHOLD:
        evidence = (
            f"Beneish M-Score = {m_score:.3f} exceeds the −1.78 manipulation threshold "
            f"on FY{curr.year} vs FY{prev.year}. "
            f"Revenue {fmt_inr(curr.revenue)} (prior {fmt_inr(prev.revenue)}), "
            f"receivables {fmt_inr(curr.receivables)} (prior {fmt_inr(prev.receivables)}), "
            f"operating cash flow {fmt_inr(curr.cf_operating)}."
        )
        signals.append(FraudSignal(
            signal_type="BENEISH_M_SCORE_BREACH",
            severity=Severity.CRITICAL,
            score_contribution=40.0,
            evidence_string=evidence,
            module_name=MODULE_NAME,
            triggered_by=[
                {"label": "FinancialStatement", "cin": curr.cin, "year": curr.year},
                {"label": "FinancialStatement", "cin": prev.cin, "year": prev.year},
            ],
        ))
        total_score += 40.0

    return ModuleResult(
        module_name=MODULE_NAME,
        cin=curr.cin,
        year=curr.year,
        score=clamp_score(total_score),
        signals=signals,
    )


def m_score(curr: RawFinancialStatement, prev: RawFinancialStatement) -> float:
    """Public helper for callers who only want the bare M-Score number (e.g. tests)."""
    return _m_score(_compute_ratios(curr, prev))


def ratios(curr: RawFinancialStatement, prev: RawFinancialStatement) -> dict[str, float]:
    """Public helper exposing the 8 raw ratios — useful for the UI / report."""
    return _compute_ratios(curr, prev)

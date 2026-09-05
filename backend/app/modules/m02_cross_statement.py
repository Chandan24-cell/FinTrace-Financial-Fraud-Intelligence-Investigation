"""Module 2 — Cross-Statement Consistency (PRD §4.2).

Seven checks. Each evidence string MUST cite specific numbers — PRD §4.2
verbatim: never 'revenue appears inflated', always 'Revenue per P&L (₹12.4 cr)
exceeds GST taxable turnover (₹8.2 cr) by 51.2% — above 5% tolerance.'

| # | Check                              | Logic                                              | Severity / +score |
|---|------------------------------------|----------------------------------------------------|-------------------|
| 1 | Revenue vs GST Turnover            | |P&L Rev − GST Turnover| / Rev > 5%                | CRITICAL / +40    |
| 2 | Implied Interest Rate              | Finance Costs / Borrowings < 8% or > 24%           | HIGH / +25        |
| 3 | Cash Conversion Ratio              | OCF / PAT < 0.5                                    | HIGH / +25        |
| 4 | Depreciation vs Asset Growth       | Assets +>20% YoY, depreciation +<50% proportional  | MEDIUM / +15      |
| 5 | CWIP Staleness                     | CWIP > 5% of total assets for 3+ consecutive years | HIGH / +25        |
| 6 | CERSAI Charges vs Debt             | Registered charges exceed reported debt by >15%    | CRITICAL / +40    |
| 7 | Bank Credits vs Revenue            | Bank credits differ from revenue by >20%           | HIGH / +25        |
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.ingest.gst import RawGSTEntity
from backend.app.ingest.schemas import RawCharge, RawFinancialStatement
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
    fmt_inr,
    safe_div,
)

MODULE_NAME = "m02_cross_statement"


@dataclass
class CrossStatementInputs:
    """All optional side-inputs Module 2 may consume. Missing inputs simply
    cause the corresponding check to abstain (no FraudSignal)."""

    current: RawFinancialStatement
    previous: RawFinancialStatement | None = None
    cwip_history: list[RawFinancialStatement] | None = None  # current + prior years for stale-CWIP check
    gst_entity: RawGSTEntity | None = None
    cersai_charges: list[RawCharge] | None = None
    bank_credits_total: float | None = None  # from /upload/bank reconstruction


def _check_revenue_vs_gst(
    fs: RawFinancialStatement, gst: RawGSTEntity | None,
) -> FraudSignal | None:
    """Check 1 — PRD §4.2 #1."""
    if gst is None or gst.aggregate_turnover <= 0 or fs.revenue <= 0:
        return None
    diff_pct = abs(fs.revenue - gst.aggregate_turnover) / fs.revenue * 100
    if diff_pct <= 5.0:
        return None
    return FraudSignal(
        signal_type="CROSS_STMT_REVENUE_VS_GST",
        severity=Severity.CRITICAL,
        score_contribution=40.0,
        evidence_string=(
            f"Revenue per P&L ({fmt_inr(fs.revenue)}) "
            f"{'exceeds' if fs.revenue > gst.aggregate_turnover else 'falls below'} "
            f"GST taxable turnover ({fmt_inr(gst.aggregate_turnover)}) "
            f"by {diff_pct:.1f}% — above the 5% tolerance threshold."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year},
            {"label": "GSTEntity", "gstin": gst.gstin},
        ],
    )


def _check_implied_interest_rate(fs: RawFinancialStatement) -> FraudSignal | None:
    """Check 2 — PRD §4.2 #2."""
    total_borrowings = fs.long_term_borrowings + fs.short_term_borrowings
    if total_borrowings <= 0 or fs.finance_costs <= 0:
        return None
    rate = fs.finance_costs / total_borrowings * 100
    if 8.0 <= rate <= 24.0:
        return None
    return FraudSignal(
        signal_type="CROSS_STMT_IMPLIED_INTEREST",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Implied interest rate {rate:.2f}% "
            f"(finance costs {fmt_inr(fs.finance_costs)} / total borrowings "
            f"{fmt_inr(total_borrowings)}) is outside the 8.00%–24.00% market band — "
            f"possible off-book debt or related-party finance."
        ),
        module_name=MODULE_NAME,
        triggered_by=[{"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}],
    )


def _check_cash_conversion(fs: RawFinancialStatement) -> FraudSignal | None:
    """Check 3 — PRD §4.2 #3."""
    if fs.pat <= 0:
        return None  # Loss-making years skip this check (PAT > 0 is the trigger condition)
    ratio = safe_div(fs.cf_operating, fs.pat, default=0.0)
    if ratio >= 0.5:
        return None
    return FraudSignal(
        signal_type="CROSS_STMT_CASH_CONVERSION",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Cash conversion ratio {ratio:.2f} = operating cash flow "
            f"{fmt_inr(fs.cf_operating)} / PAT {fmt_inr(fs.pat)} "
            f"is below the 0.5 sustainability floor — earnings not backed by cash."
        ),
        module_name=MODULE_NAME,
        triggered_by=[{"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}],
    )


def _check_depreciation_vs_asset_growth(
    curr: RawFinancialStatement, prev: RawFinancialStatement | None,
) -> FraudSignal | None:
    """Check 4 — PRD §4.2 #4. Assets grew >20% YoY but depreciation grew <50% proportionally."""
    if prev is None or prev.total_assets <= 0 or prev.depreciation <= 0:
        return None
    asset_growth = (curr.total_assets - prev.total_assets) / prev.total_assets * 100
    if asset_growth <= 20.0:
        return None
    dep_growth = (curr.depreciation - prev.depreciation) / prev.depreciation * 100
    proportional_floor = 0.5 * asset_growth
    if dep_growth >= proportional_floor:
        return None
    return FraudSignal(
        signal_type="CROSS_STMT_DEPRECIATION_LAG",
        severity=Severity.MEDIUM,
        score_contribution=15.0,
        evidence_string=(
            f"Total assets grew {asset_growth:.1f}% YoY (from {fmt_inr(prev.total_assets)} "
            f"to {fmt_inr(curr.total_assets)}) but depreciation grew only {dep_growth:.1f}% — "
            f"below the {proportional_floor:.1f}% proportional floor. Possible capitalisation "
            f"of operating expenses as long-lived assets."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "FinancialStatement", "cin": curr.cin, "year": curr.year},
            {"label": "FinancialStatement", "cin": prev.cin, "year": prev.year},
        ],
    )


def _check_cwip_staleness(history: list[RawFinancialStatement] | None) -> FraudSignal | None:
    """Check 5 — PRD §4.2 #5. CWIP > 5% of total assets for 3+ consecutive years."""
    if history is None or len(history) < 3:
        return None
    sorted_hist = sorted(history, key=lambda f: f.year, reverse=True)[:3]
    breaches = [
        (fs, safe_div(fs.cwip, fs.total_assets) * 100)
        for fs in sorted_hist
        if fs.total_assets > 0
    ]
    if len(breaches) < 3:
        return None
    if not all(pct > 5.0 for _, pct in breaches):
        return None
    latest = breaches[0][0]
    years_csv = ", ".join(f"FY{fs.year}={pct:.2f}%" for fs, pct in breaches)
    return FraudSignal(
        signal_type="CROSS_STMT_CWIP_STALE",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"CWIP has exceeded 5% of total assets for 3 consecutive years "
            f"({years_csv}). Stale capital-work-in-progress is a classic vehicle "
            f"for parked expenses or fictitious assets."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}
            for fs, _ in breaches
        ],
    )


def _check_cersai_vs_debt(
    fs: RawFinancialStatement, charges: list[RawCharge] | None,
) -> FraudSignal | None:
    """Check 6 — PRD §4.2 #6. CERSAI registered charges > reported debt by >15%."""
    if charges is None:
        return None
    # Only count active (un-satisfied) charges as outstanding exposure
    active = [c for c in charges if c.satisfaction_date is None]
    if not active:
        return None
    cersai_total = sum(c.amount for c in active)
    reported_debt = fs.long_term_borrowings + fs.short_term_borrowings
    if reported_debt <= 0:
        # Charges exist but balance sheet shows no debt — that's its own red flag
        return FraudSignal(
            signal_type="CROSS_STMT_CERSAI_VS_DEBT",
            severity=Severity.CRITICAL,
            score_contribution=40.0,
            evidence_string=(
                f"CERSAI shows {len(active)} active charges totalling "
                f"{fmt_inr(cersai_total)} but the balance sheet reports zero borrowings."
            ),
            module_name=MODULE_NAME,
            triggered_by=[
                {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year},
                *[{"label": "LoanDisbursement", "loan_id": c.charge_id} for c in active],
            ],
        )
    excess_pct = (cersai_total - reported_debt) / reported_debt * 100
    if excess_pct <= 15.0:
        return None
    return FraudSignal(
        signal_type="CROSS_STMT_CERSAI_VS_DEBT",
        severity=Severity.CRITICAL,
        score_contribution=40.0,
        evidence_string=(
            f"CERSAI registered charges {fmt_inr(cersai_total)} exceed reported "
            f"borrowings {fmt_inr(reported_debt)} by {excess_pct:.1f}% — above the "
            f"15% tolerance. Possible undisclosed leverage."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year},
            *[{"label": "LoanDisbursement", "loan_id": c.charge_id} for c in active],
        ],
    )


def _check_bank_credits_vs_revenue(
    fs: RawFinancialStatement, bank_credits: float | None,
) -> FraudSignal | None:
    """Check 7 — PRD §4.2 #7. Bank credits diverge from revenue by >20%."""
    if bank_credits is None or fs.revenue <= 0:
        return None
    diff_pct = abs(bank_credits - fs.revenue) / fs.revenue * 100
    if diff_pct <= 20.0:
        return None
    direction = "exceed" if bank_credits > fs.revenue else "fall below"
    return FraudSignal(
        signal_type="CROSS_STMT_BANK_VS_REVENUE",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Bank statement credits {fmt_inr(bank_credits)} {direction} reported "
            f"revenue {fmt_inr(fs.revenue)} by {diff_pct:.1f}% — above the 20% "
            f"tolerance. Possible cash-flow misstatement or off-book sales."
        ),
        module_name=MODULE_NAME,
        triggered_by=[{"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}],
    )


def run(inputs: CrossStatementInputs) -> ModuleResult:
    """Run all 7 checks. Any check that can't run on the given inputs simply abstains."""
    fs = inputs.current
    signals: list[FraudSignal] = []

    for sig in (
        _check_revenue_vs_gst(fs, inputs.gst_entity),
        _check_implied_interest_rate(fs),
        _check_cash_conversion(fs),
        _check_depreciation_vs_asset_growth(fs, inputs.previous),
        _check_cwip_staleness(inputs.cwip_history),
        _check_cersai_vs_debt(fs, inputs.cersai_charges),
        _check_bank_credits_vs_revenue(fs, inputs.bank_credits_total),
    ):
        if sig is not None:
            signals.append(sig)

    score = clamp_score(sum(s.score_contribution for s in signals))
    return ModuleResult(
        module_name=MODULE_NAME,
        cin=fs.cin,
        year=fs.year,
        score=score,
        signals=signals,
    )

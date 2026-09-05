"""Module 6 — Temporal Behavioural Signals (PRD §4.6).

Eight signals, each watching a different time-axis red flag. Signals whose
inputs aren't on file abstain rather than crash. PRD §4.2 specific-numbers
rule applies module-wide.

| #  | Signal                                       | Trigger                                                        | Severity / +score |
|----|----------------------------------------------|----------------------------------------------------------------|-------------------|
| T1 | Director change pre-filing                   | ≥1 director appointment/cessation within 90 days of FS year-end | HIGH / +25       |
| T2 | Auditor change year-over-year                | auditor_name in FS_t != FS_{t-1}                                | HIGH / +25       |
| T3 | Sole-proprietor auditor on large company     | auditor_type='sole_proprietor' AND revenue > ₹50 cr             | MEDIUM / +15     |
| T4 | Sudden PAT improvement after stress           | PAT_t / |PAT_{t-1}| > 3.0 when PAT_{t-1} < 0                    | HIGH / +25       |
| T5 | Quarterly smoothing CoV<0.03                  | (requires quarterly data — abstains in fixture mode)            | HIGH / +25       |
| T6 | Round-trip borrowing                          | borrowings up >25% then down >25% across 3 years                | HIGH / +25       |
| T7 | Filing-delay trend                            | (requires filing-date metadata — abstains in fixture mode)      | MEDIUM / +15     |
| T8 | Accounting policy change post-bad-year        | going_concern/adverse flag flipped on/off between years          | CRITICAL / +40   |

Day-10 acceptance (PRD §10): 'Module 6 fires temporal signals on IL&FS.'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.app.ingest.schemas import RawDirector, RawFinancialStatement
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
    fmt_inr,
)

MODULE_NAME = "m06_temporal"

# PRD §4.6 thresholds
DIRECTOR_CHANGE_WINDOW_DAYS = 90
LARGE_COMPANY_REVENUE = 500_000_000.0   # ₹50 cr
SUDDEN_RECOVERY_MULTIPLE = 3.0
ROUND_TRIP_BORROWING_PCT = 25.0


@dataclass
class TemporalInputs:
    """Sorted (ascending year) FS list plus all-time director snapshot."""

    financials: list[RawFinancialStatement]
    directors: list[RawDirector]


def _sorted_fs(fs_list: Iterable[RawFinancialStatement]) -> list[RawFinancialStatement]:
    return sorted(fs_list, key=lambda f: f.year)


def _t1_director_change_pre_filing(
    fs_list: list[RawFinancialStatement], directors: list[RawDirector],
) -> FraudSignal | None:
    """Director appointed or removed within 90 days of any FS year-end (Mar 31).

    The fixture data doesn't carry an MCA filing date, so we proxy with the FS
    year-end (31 Mar). PRD §4.6 #1 — RBI red flag.
    """
    if not fs_list or not directors:
        return None
    matches: list[str] = []
    for fs in fs_list:
        from datetime import date
        ye = date(fs.year, 3, 31)
        for d in directors:
            for event_date, kind in (
                (d.appointment_date, "appointed"),
                (d.cessation_date, "removed"),
            ):
                if event_date is None:
                    continue
                gap = abs((ye - event_date).days)
                if gap <= DIRECTOR_CHANGE_WINDOW_DAYS:
                    matches.append(
                        f"{d.name} (DIN {d.din}) {kind} {gap}d "
                        f"before FY{fs.year} year-end"
                    )
    if not matches:
        return None
    return FraudSignal(
        signal_type="TEMPORAL_DIRECTOR_CHANGE_PRE_FILING",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Director events within {DIRECTOR_CHANGE_WINDOW_DAYS}d of FS year-end: "
            + "; ".join(matches[:5])
            + ("…" if len(matches) > 5 else "")
            + ". Late-cycle board changes often precede restated accounts."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "Director", "din": d.din}
            for d in directors[:5]
        ],
    )


def _t2_auditor_change(fs_list: list[RawFinancialStatement]) -> FraudSignal | None:
    """auditor_name flips across consecutive years."""
    changes: list[str] = []
    for prev, curr in zip(fs_list, fs_list[1:]):
        a_prev = (prev.auditor_name or "").strip()
        a_curr = (curr.auditor_name or "").strip()
        if a_prev and a_curr and a_prev != a_curr:
            changes.append(f"FY{prev.year}→FY{curr.year}: '{a_prev}' → '{a_curr}'")
    if not changes:
        return None
    return FraudSignal(
        signal_type="TEMPORAL_AUDITOR_CHANGE",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"{len(changes)} auditor change(s) observed: " + "; ".join(changes) +
            ". Frequent auditor rotation is a documented fraud precursor (Beasley et al. 2018)."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}
            for fs in fs_list
        ],
    )


def _t3_sole_proprietor_large_company(
    fs_list: list[RawFinancialStatement],
) -> FraudSignal | None:
    """sole-proprietor audit firm on a company with > ₹50 cr revenue."""
    hits: list[RawFinancialStatement] = []
    for fs in fs_list:
        if fs.auditor_type == "sole_proprietor" and fs.revenue > LARGE_COMPANY_REVENUE:
            hits.append(fs)
    if not hits:
        return None
    fs = hits[-1]  # cite latest year
    return FraudSignal(
        signal_type="TEMPORAL_SOLE_PROP_AUDITOR_LARGE_CO",
        severity=Severity.MEDIUM,
        score_contribution=15.0,
        evidence_string=(
            f"FY{fs.year} revenue {fmt_inr(fs.revenue)} exceeds the "
            f"{fmt_inr(LARGE_COMPANY_REVENUE)} threshold yet auditor is a sole "
            f"proprietorship ('{fs.auditor_name}'). NFRA-flagged structural risk."
        ),
        module_name=MODULE_NAME,
        triggered_by=[{"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}],
    )


def _t4_sudden_recovery(fs_list: list[RawFinancialStatement]) -> FraudSignal | None:
    """PAT swings from large loss to multi-x gain in one year."""
    for prev, curr in zip(fs_list, fs_list[1:]):
        if prev.pat >= 0 or curr.pat <= 0:
            continue
        multiple = curr.pat / abs(prev.pat)
        if multiple < SUDDEN_RECOVERY_MULTIPLE:
            continue
        return FraudSignal(
            signal_type="TEMPORAL_SUDDEN_RECOVERY",
            severity=Severity.HIGH,
            score_contribution=25.0,
            evidence_string=(
                f"PAT swung from a loss of {fmt_inr(prev.pat)} in FY{prev.year} to "
                f"a profit of {fmt_inr(curr.pat)} in FY{curr.year} — "
                f"{multiple:.1f}x recovery. Sudden post-stress turnarounds are a "
                f"PRD §4.6 #4 red flag."
            ),
            module_name=MODULE_NAME,
            triggered_by=[
                {"label": "FinancialStatement", "cin": prev.cin, "year": prev.year},
                {"label": "FinancialStatement", "cin": curr.cin, "year": curr.year},
            ],
        )
    return None


def _t5_quarterly_smoothing(fs_list: list[RawFinancialStatement]) -> FraudSignal | None:
    """Requires quarterly revenue data — not available in fixture mode. Abstains."""
    return None


def _t6_round_trip_borrowing(fs_list: list[RawFinancialStatement]) -> FraudSignal | None:
    """borrowings up > 25% then down > 25% across three consecutive years."""
    if len(fs_list) < 3:
        return None
    for a, b, c in zip(fs_list, fs_list[1:], fs_list[2:]):
        bor_a = a.long_term_borrowings + a.short_term_borrowings
        bor_b = b.long_term_borrowings + b.short_term_borrowings
        bor_c = c.long_term_borrowings + c.short_term_borrowings
        if bor_a <= 0 or bor_b <= 0:
            continue
        up_pct = (bor_b - bor_a) / bor_a * 100.0
        down_pct = (bor_c - bor_b) / bor_b * 100.0
        if up_pct >= ROUND_TRIP_BORROWING_PCT and down_pct <= -ROUND_TRIP_BORROWING_PCT:
            return FraudSignal(
                signal_type="TEMPORAL_ROUND_TRIP_BORROWING",
                severity=Severity.HIGH,
                score_contribution=25.0,
                evidence_string=(
                    f"Round-trip borrowing pattern: total debt grew "
                    f"{up_pct:+.1f}% from FY{a.year} ({fmt_inr(bor_a)}) to FY{b.year} "
                    f"({fmt_inr(bor_b)}), then shrank {down_pct:+.1f}% to FY{c.year} "
                    f"({fmt_inr(bor_c)}). Suggests window-dressing at FY{b.year} year-end."
                ),
                module_name=MODULE_NAME,
                triggered_by=[
                    {"label": "FinancialStatement", "cin": x.cin, "year": x.year}
                    for x in (a, b, c)
                ],
            )
    return None


def _t7_filing_delay_trend(_: list[RawFinancialStatement]) -> FraudSignal | None:
    """Requires MCA filing-date metadata — not in fixture mode. Abstains."""
    return None


def _t8_policy_change_post_bad_year(
    fs_list: list[RawFinancialStatement],
) -> FraudSignal | None:
    """going_concern_flag or adverse_flag flipping on→off or off→on between
    consecutive years signals an opinion change that may not be justified by
    the underlying numbers. PRD §4.6 #8 — CRITICAL."""
    for prev, curr in zip(fs_list, fs_list[1:]):
        flags_changed = (
            prev.going_concern_flag != curr.going_concern_flag
            or prev.adverse_flag != curr.adverse_flag
        )
        if not flags_changed:
            continue
        return FraudSignal(
            signal_type="TEMPORAL_POLICY_CHANGE_POST_BAD_YEAR",
            severity=Severity.CRITICAL,
            score_contribution=40.0,
            evidence_string=(
                f"Audit-opinion flags changed between FY{prev.year} and FY{curr.year} — "
                f"going_concern: {prev.going_concern_flag}→{curr.going_concern_flag}, "
                f"adverse: {prev.adverse_flag}→{curr.adverse_flag}. "
                f"Sudden flag flips often track restated accounts or new auditor."
            ),
            module_name=MODULE_NAME,
            triggered_by=[
                {"label": "FinancialStatement", "cin": prev.cin, "year": prev.year},
                {"label": "FinancialStatement", "cin": curr.cin, "year": curr.year},
            ],
        )
    return None


def run(inputs: TemporalInputs) -> ModuleResult:
    fs_list = _sorted_fs(inputs.financials)
    cin = fs_list[0].cin if fs_list else ""
    year = fs_list[-1].year if fs_list else None

    signals: list[FraudSignal] = []
    for sig in (
        _t1_director_change_pre_filing(fs_list, inputs.directors),
        _t2_auditor_change(fs_list),
        _t3_sole_proprietor_large_company(fs_list),
        _t4_sudden_recovery(fs_list),
        _t5_quarterly_smoothing(fs_list),
        _t6_round_trip_borrowing(fs_list),
        _t7_filing_delay_trend(fs_list),
        _t8_policy_change_post_bad_year(fs_list),
    ):
        if sig is not None:
            signals.append(sig)

    return ModuleResult(
        module_name=MODULE_NAME,
        cin=cin,
        year=year,
        score=clamp_score(sum(s.score_contribution for s in signals)),
        signals=signals,
    )

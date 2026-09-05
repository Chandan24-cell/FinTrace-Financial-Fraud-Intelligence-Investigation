"""Module 3 — Benford's Law (PRD §4.3).

Chi-square (p < 0.05) + Kolmogorov-Smirnov + Nigrini Mean Absolute Deviation.
Requires ≥ 50 numbers to be statistically meaningful; below that the module abstains.

Disabled for fixed-price / posted-price industries where Benford does not naturally
hold even on clean books — PRD §4.3 verbatim list: NIC 46, 47, 19, 49, 55.

Evidence strings cite chi-square / KS / MAD with the exact computed values, never
'distribution looks odd' — same rule as Modules 1 & 2 (PRD §4.2 specific-numbers rule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
)

MODULE_NAME = "m03_benford"

# Industries where posted/retail pricing breaks Benford even on honest books.
# PRD §4.3 verbatim: skip NIC 46 (wholesale), 47 (retail), 19 (petroleum),
# 49 (transport), 55 (accommodation).
BENFORD_DISABLED_NICS: frozenset[str] = frozenset({"46", "47", "19", "49", "55"})

# Theoretical first-digit probabilities P(d) = log10(1 + 1/d) for d=1..9.
EXPECTED_FIRST_DIGIT_PROBS: tuple[float, ...] = tuple(
    math.log10(1 + 1 / d) for d in range(1, 10)
)

# PRD §4.3 thresholds
MIN_SAMPLE_SIZE = 50
CHI_SQUARE_P_THRESHOLD = 0.05
# Chi-square critical value at df=8, alpha=0.05 — Benford has 9 bins, 1 dof loss.
CHI_SQUARE_CRIT_8DF_05 = 15.507
# Nigrini (2012) "Forensic Analytics" MAD bands for first-digit Benford:
#   0.000 - 0.006  close conformity
#   0.006 - 0.012  acceptable conformity
#   0.012 - 0.015  marginal
#   > 0.015        nonconformity (the threshold we fire on)
NIGRINI_MAD_THRESHOLD = 0.015
# KS critical value at n=50, alpha=0.05 — 1.36/sqrt(n) Kolmogorov approximation
# applied to the cumulative Benford CDF.
KS_CRIT_FACTOR = 1.36


def _is_nic_disabled(nic_code: str | int | None) -> bool:
    if nic_code is None:
        return False
    # CompanyBundle.company.nic_code is an int (e.g. 45201) per
    # backend/app/ingest/schemas.py; older callsites passed pre-stringified
    # forms. Coerce here so both shapes work — first two digits are the
    # NIC section we filter on.
    s = str(nic_code)
    if not s:
        return False
    return s[:2] in BENFORD_DISABLED_NICS


def _first_digit(value: float) -> int | None:
    """Strip sign and decimal, return the leading non-zero digit. None on 0 / NaN / inf."""
    if value is None or not math.isfinite(value):
        return None
    v = abs(value)
    if v < 1e-12:
        return None
    while v < 1.0:
        v *= 10.0
    while v >= 10.0:
        v /= 10.0
    d = int(v)
    if d < 1 or d > 9:
        return None
    return d


def _gather_numbers(fs_list: Iterable[RawFinancialStatement]) -> list[float]:
    """Pull every numeric field that could carry a fabricated invoice / journal
    amount. PRD §4.3 implies a wide net: revenue, expenses, receivables, debt,
    PPE, depreciation, finance costs — anything posted as a line item."""
    fields = (
        "revenue",
        "other_income",
        "cost_of_materials",
        "employee_cost",
        "other_expenses",
        "depreciation",
        "finance_costs",
        "pbt",
        "pat",
        "receivables",
        "trade_payables",
        "cash_and_equivalents",
        "inventory",
        "cwip",
        "fixed_assets",
        "current_assets",
        "total_assets",
        "long_term_borrowings",
        "short_term_borrowings",
        "cf_operating",
        "cf_investing",
        "cf_financing",
    )
    out: list[float] = []
    for fs in fs_list:
        for name in fields:
            val = getattr(fs, name, None)
            if val is None:
                continue
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v) and abs(v) > 0:
                out.append(v)
    return out


@dataclass(frozen=True)
class BenfordStats:
    """Diagnostic snapshot — handy for unit tests and for the demo dashboard."""

    n: int
    observed_counts: tuple[int, ...]
    expected_counts: tuple[float, ...]
    chi_square: float
    chi_square_p_lt_05: bool
    ks_statistic: float
    ks_critical: float
    mad: float


def benford_stats(numbers: Iterable[float]) -> BenfordStats | None:
    """Compute chi-square, KS, MAD vs the Benford first-digit distribution.

    Returns None if fewer than `MIN_SAMPLE_SIZE` valid numbers (Nigrini's
    rule of thumb — chi-square is unstable on small samples)."""
    digits = [d for d in (_first_digit(x) for x in numbers) if d is not None]
    n = len(digits)
    if n < MIN_SAMPLE_SIZE:
        return None

    observed = [0] * 9
    for d in digits:
        observed[d - 1] += 1
    expected = [p * n for p in EXPECTED_FIRST_DIGIT_PROBS]

    chi_sq = sum(
        (obs - exp) ** 2 / exp for obs, exp in zip(observed, expected) if exp > 0
    )

    # KS: max |F_observed(d) - F_expected(d)| over the 9 cumulative bins.
    cum_obs, cum_exp, ks = 0.0, 0.0, 0.0
    for i in range(9):
        cum_obs += observed[i] / n
        cum_exp += EXPECTED_FIRST_DIGIT_PROBS[i]
        ks = max(ks, abs(cum_obs - cum_exp))
    ks_critical = KS_CRIT_FACTOR / math.sqrt(n)

    # Mean Absolute Deviation across the 9 bins — Nigrini's preferred metric
    # because it's sample-size insensitive.
    mad = sum(
        abs(observed[i] / n - EXPECTED_FIRST_DIGIT_PROBS[i]) for i in range(9)
    ) / 9.0

    return BenfordStats(
        n=n,
        observed_counts=tuple(observed),
        expected_counts=tuple(expected),
        chi_square=chi_sq,
        chi_square_p_lt_05=chi_sq > CHI_SQUARE_CRIT_8DF_05,
        ks_statistic=ks,
        ks_critical=ks_critical,
        mad=mad,
    )


def _evidence_string(stats: BenfordStats) -> str:
    """Specific-numbers evidence (PRD §4.2 rule applies to every module)."""
    return (
        f"Benford first-digit test failed on n={stats.n} amounts. "
        f"χ²={stats.chi_square:.2f} (critical={CHI_SQUARE_CRIT_8DF_05:.2f} at df=8, p<{CHI_SQUARE_P_THRESHOLD}); "
        f"KS={stats.ks_statistic:.4f} (critical={stats.ks_critical:.4f}); "
        f"MAD={stats.mad:.4f} (Nigrini nonconformity > {NIGRINI_MAD_THRESHOLD:.3f}). "
        f"Possible journal-entry fabrication or capped invoice clustering."
    )


def run(
    fs_list: list[RawFinancialStatement],
    *,
    nic_code: str | None = None,
) -> ModuleResult:
    """Run Benford across all available years for a single company.

    PRD §4.3 says 'minimum 50 numbers' — we pool every numeric line item across
    every year on file. Below 50, abstain. NIC 46/47/19/49/55 also abstain.
    """
    cin = fs_list[0].cin if fs_list else ""
    year = fs_list[-1].year if fs_list else None

    if _is_nic_disabled(nic_code):
        return ModuleResult.skipped_for(
            MODULE_NAME, cin, year,
            reason=f"NIC {nic_code} in Benford-disabled list "
                   f"(posted-price industry — PRD §4.3)",
        )

    numbers = _gather_numbers(fs_list)
    stats = benford_stats(numbers)
    if stats is None:
        return ModuleResult.skipped_for(
            MODULE_NAME, cin, year,
            reason=f"Only {len(numbers)} non-zero numbers across {len(fs_list)} year(s) — "
                   f"need ≥{MIN_SAMPLE_SIZE} for chi-square to be meaningful.",
        )

    signals: list[FraudSignal] = []
    # Any 2 of the 3 tests firing means we surface a HIGH signal.
    failures = sum([
        stats.chi_square_p_lt_05,
        stats.ks_statistic > stats.ks_critical,
        stats.mad > NIGRINI_MAD_THRESHOLD,
    ])
    if failures >= 2:
        severity = Severity.HIGH if failures == 2 else Severity.CRITICAL
        score = 25.0 if severity is Severity.HIGH else 35.0
        signals.append(FraudSignal(
            signal_type="BENFORD_DEVIATION",
            severity=severity,
            score_contribution=score,
            evidence_string=_evidence_string(stats),
            module_name=MODULE_NAME,
            triggered_by=[
                {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year}
                for fs in fs_list
            ],
        ))

    return ModuleResult(
        module_name=MODULE_NAME,
        cin=cin,
        year=year,
        score=clamp_score(sum(s.score_contribution for s in signals)),
        signals=signals,
    )

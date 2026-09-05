"""Module 5 — Peer Deviation (PRD §4.5).

Compares a company's key ratios against IndustryBenchmark (NIC × FY) p25/median/p75
quartiles seeded from BSE SME data. We don't compute a z-score (BSE only publishes
quartiles, not mean/sd), so we use an IQR-distance:

  iqr = max(p75 - p25, ε)
  distance = (value - median) / iqr

|distance| ≥ 1.5 puts the company in the outlier region for that metric
(roughly equivalent to a 2σ excursion when the distribution is approximately
log-normal, which matches the BSE SME observation). |distance| ≥ 3.0 is a
severe outlier and bumps severity to CRITICAL.

Each firing metric becomes one FraudSignal with the metric name, the company
value, the (p25, median, p75) triple, and the IQR-distance — never generic.
PRD §4.2 specific-numbers rule applies module-wide.

Metrics computed per fiscal year:
  revenue_growth_pct, gross_margin_pct, debt_to_equity, current_ratio,
  interest_coverage, asset_turnover.

Day-10 acceptance (PRD §10): 'Module 5 fires on outliers vs BSE benchmarks.'
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.app.ingest.benchmarks import BenchmarkPoint
from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
    safe_div,
)

MODULE_NAME = "m05_peer_deviation"

# IQR-distance thresholds per PRD §4.5
HIGH_DISTANCE = 1.5
CRITICAL_DISTANCE = 3.0


@dataclass(frozen=True)
class _MetricRow:
    """One computed ratio for the current FS year."""

    name: str
    value: float
    label: str             # human-readable label for the evidence string
    unit_suffix: str       # '%', 'x', '' — appended after the number


def _compute_metrics(
    curr: RawFinancialStatement,
    prev: RawFinancialStatement | None,
) -> list[_MetricRow]:
    """Compute the six PRD §4.5 ratios. Returns only metrics whose inputs are
    well-defined (no zero denominators, no NaN/inf)."""

    rows: list[_MetricRow] = []

    if prev is not None and prev.revenue > 0:
        rg = (curr.revenue - prev.revenue) / prev.revenue * 100.0
        if math.isfinite(rg):
            rows.append(_MetricRow("revenue_growth_pct", rg, "revenue growth", "%"))

    if curr.revenue > 0:
        gm = (curr.revenue - curr.cost_of_materials) / curr.revenue * 100.0
        if math.isfinite(gm):
            rows.append(_MetricRow("gross_margin_pct", gm, "gross margin", "%"))

    equity = curr.total_assets - curr.total_liabilities
    total_debt = curr.long_term_borrowings + curr.short_term_borrowings
    if equity > 0 and total_debt >= 0:
        de = total_debt / equity
        if math.isfinite(de):
            rows.append(_MetricRow("debt_to_equity", de, "debt-to-equity", "x"))

    # current_ratio = current_assets / (short_term_borrowings + trade_payables)
    cl = curr.short_term_borrowings + curr.trade_payables
    if cl > 0:
        cr = safe_div(curr.current_assets, cl)
        if math.isfinite(cr) and cr > 0:
            rows.append(_MetricRow("current_ratio", cr, "current ratio", "x"))

    if curr.finance_costs > 0:
        ic = safe_div(curr.pbt + curr.finance_costs, curr.finance_costs)
        if math.isfinite(ic):
            rows.append(_MetricRow("interest_coverage", ic, "interest coverage", "x"))

    if curr.total_assets > 0:
        at = safe_div(curr.revenue, curr.total_assets)
        if math.isfinite(at) and at >= 0:
            rows.append(_MetricRow("asset_turnover", at, "asset turnover", "x"))

    return rows


def _iqr_distance(value: float, bench: BenchmarkPoint) -> float:
    """IQR-normalised distance from the median. Positive = above peer median."""
    iqr = max(bench.p75 - bench.p25, 1e-9)
    return (value - bench.median) / iqr


def _index_benchmarks(
    benchmarks: list[BenchmarkPoint], nic_code: int, year: int,
) -> dict[str, BenchmarkPoint]:
    """Pick the closest available year for this NIC code, fall back to any year."""
    by_year: dict[int, dict[str, BenchmarkPoint]] = {}
    for b in benchmarks:
        if b.nic_code != nic_code:
            continue
        by_year.setdefault(b.year, {})[b.metric] = b
    if not by_year:
        return {}
    # Prefer the exact year, then the most recent year <= target, then the most recent overall.
    if year in by_year:
        return by_year[year]
    older = sorted([y for y in by_year if y <= year], reverse=True)
    if older:
        return by_year[older[0]]
    newest = max(by_year)
    return by_year[newest]


def _severity_for(distance: float) -> Severity | None:
    abs_d = abs(distance)
    if abs_d >= CRITICAL_DISTANCE:
        return Severity.CRITICAL
    if abs_d >= HIGH_DISTANCE:
        return Severity.HIGH
    return None


def _score_for(severity: Severity) -> float:
    return {Severity.CRITICAL: 35.0, Severity.HIGH: 20.0, Severity.MEDIUM: 10.0}[severity]


def _format_number(value: float, suffix: str) -> str:
    if suffix == "%":
        return f"{value:.1f}%"
    if suffix == "x":
        return f"{value:.2f}x"
    return f"{value:.2f}"


def _build_signal(
    *,
    fs: RawFinancialStatement,
    metric: _MetricRow,
    bench: BenchmarkPoint,
    distance: float,
    severity: Severity,
) -> FraudSignal:
    direction = "above" if distance > 0 else "below"
    return FraudSignal(
        signal_type="PEER_DEVIATION",
        severity=severity,
        score_contribution=_score_for(severity),
        evidence_string=(
            f"{metric.label.capitalize()} {_format_number(metric.value, metric.unit_suffix)} "
            f"is {abs(distance):.2f} IQR-units {direction} the NIC {bench.nic_code} FY{bench.year} "
            f"peer median {_format_number(bench.median, metric.unit_suffix)} "
            f"(p25={_format_number(bench.p25, metric.unit_suffix)}, "
            f"p75={_format_number(bench.p75, metric.unit_suffix)}). "
            f"Outside the {HIGH_DISTANCE:.1f}-IQR peer band — possible misstatement or stress."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "FinancialStatement", "cin": fs.cin, "year": fs.year},
        ],
    )


def run(
    curr: RawFinancialStatement,
    prev: RawFinancialStatement | None,
    *,
    nic_code: int,
    benchmarks: list[BenchmarkPoint],
) -> ModuleResult:
    """Compare current-year ratios against the matching IndustryBenchmark row."""

    bench_by_metric = _index_benchmarks(benchmarks, nic_code, curr.year)
    if not bench_by_metric:
        return ModuleResult.skipped_for(
            MODULE_NAME, curr.cin, curr.year,
            reason=f"No IndustryBenchmark rows for NIC {nic_code} / FY{curr.year}",
        )

    metrics = _compute_metrics(curr, prev)
    if not metrics:
        return ModuleResult.skipped_for(
            MODULE_NAME, curr.cin, curr.year,
            reason="No usable ratios derivable from current FS (zero denominators)",
        )

    signals: list[FraudSignal] = []
    for metric in metrics:
        bench = bench_by_metric.get(metric.name)
        if bench is None:
            continue
        distance = _iqr_distance(metric.value, bench)
        severity = _severity_for(distance)
        if severity is None:
            continue
        signals.append(_build_signal(
            fs=curr, metric=metric, bench=bench, distance=distance, severity=severity,
        ))

    return ModuleResult(
        module_name=MODULE_NAME,
        cin=curr.cin,
        year=curr.year,
        score=clamp_score(sum(s.score_contribution for s in signals)),
        signals=signals,
    )

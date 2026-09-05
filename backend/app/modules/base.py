"""Common types for Tier-1 deterministic detection modules (PRD §4 + §6).

Every module returns a ModuleResult containing zero or more FraudSignal records.
Each FraudSignal mirrors the PRD §3 Neo4j node schema verbatim — `signal_id`,
`signal_type`, `severity`, `score_contribution`, `evidence_string`, `module_name`,
`created_at` — so the graph writer can persist it without translation.

Severity contributions sum into the module's 0-100 score. Overall scoring per
PRD §7.2 is the orchestrator's (`scorer.py`) job on Day 15.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class Severity(str, Enum):
    """PRD §4 severity ladder. Order matters: CRITICAL > HIGH > MEDIUM > LOW."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def numeric(self) -> int:
        return {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}[self.value]


@dataclass
class FraudSignal:
    """One flag. PRD §3 node schema verbatim.

    Evidence strings MUST cite specific numbers — no generic "revenue appears
    inflated". Acceptable: "Revenue per P&L (₹12.4 cr) exceeds GST taxable
    turnover (₹8.2 cr) by 51.2% — above 5% tolerance." (PRD §4.2)
    """

    signal_type: str           # e.g. "BENEISH_M_SCORE_BREACH"
    severity: Severity
    score_contribution: float  # 0..100 — what this signal adds to the module score
    evidence_string: str
    module_name: str           # e.g. "m01_beneish"
    triggered_by: list[dict] = field(default_factory=list)  # foreign refs for TRIGGERED_BY edges
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_node_props(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "severity": self.severity.value,
            "score_contribution": float(self.score_contribution),
            "evidence_string": self.evidence_string,
            "module_name": self.module_name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ModuleResult:
    """The shape every Tier-1 module returns."""

    module_name: str
    cin: str
    year: int | None
    score: float                       # 0-100, weighted aggregate of signal contributions
    signals: list[FraudSignal] = field(default_factory=list)
    skipped: bool = False              # True when the module abstained (e.g. single-year Beneish)
    skip_reason: str = ""

    @classmethod
    def skipped_for(cls, module_name: str, cin: str, year: int | None, reason: str) -> "ModuleResult":
        return cls(
            module_name=module_name,
            cin=cin,
            year=year,
            score=0.0,
            signals=[],
            skipped=True,
            skip_reason=reason,
        )

    @property
    def max_severity(self) -> Severity | None:
        if not self.signals:
            return None
        return max((s.severity for s in self.signals), key=lambda s: s.numeric)

    def evidence_lines(self) -> Iterable[str]:
        for sig in self.signals:
            yield f"[{sig.severity.value}] {sig.evidence_string}"


def clamp_score(score: float) -> float:
    """Clamp module scores into the [0, 100] band PRD §7 expects."""
    return max(0.0, min(100.0, score))


def fmt_inr(value: float) -> str:
    """Format an INR value with thousands separators. Evidence strings must
    use this so the number is human-readable in the report."""
    if abs(value) >= 1e7:
        return f"₹{value / 1e7:,.2f} cr"
    if abs(value) >= 1e5:
        return f"₹{value / 1e5:,.2f} lakh"
    return f"₹{value:,.0f}"


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division with explicit handling of zero/None denominators.

    Tier-1 modules run on partial data — single-year filings, missing GST,
    zero borrowings. Silent zero-division would propagate NaN into the
    FraudSignal evidence strings, which is worse than returning a default.
    """
    if denominator is None or abs(denominator) < 1e-9:
        return default
    return numerator / denominator

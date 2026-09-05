"""Module 9 — NCLT / DRT / Wilful Defaulter integration (PRD §4.9 + §7.3).

Three signal sources, all of which are *override-grade* — any single match
forces the company's fraud_risk_score to ≥ 75 per PRD §7.3:

  1. NCLT proceeding with status='admitted' or petition_type='CIRP'
  2. DRT (Debt Recovery Tribunal) proceeding — modelled as petition_type='DRT'
  3. RBI / CIBIL wilful-defaulter declaration

Each match yields a FraudSignal with severity=CRITICAL and the special
`force_min_score` field consumed by the scorer (PRD §7.3 override rule).

Day-12 acceptance (PRD §10): 'Override rules fire on IL&FS + DHFL.'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple

from backend.app.ingest.nclt import RawNCLTProceeding
from backend.app.ingest.wilful_defaulter import RawWilfulDefaulter
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
    fmt_inr,
)

MODULE_NAME = "m09_nclt_defaulter"

# PRD §7.3 — any of these match forces fraud_risk_score >= NCLT_WD_FLOOR_SCORE
NCLT_WD_FLOOR_SCORE = 75.0


@dataclass
class NCLTDefaulterInputs:
    """All inputs Module 9 needs in one struct. Each list is filtered
    per-CIN by the caller."""

    cin: str
    nclt_proceedings: list[RawNCLTProceeding]
    wilful_declarations: list[RawWilfulDefaulter]


def _filter_for_cin(cin: str, proceedings: Iterable[RawNCLTProceeding]) -> list[RawNCLTProceeding]:
    return [p for p in proceedings if p.cin == cin]


def _filter_wd_for_cin(cin: str, wds: Iterable[RawWilfulDefaulter]) -> list[RawWilfulDefaulter]:
    return [w for w in wds if w.cin == cin]


def _nclt_signal(p: RawNCLTProceeding) -> FraudSignal:
    is_admitted_cirp = p.petition_type == "CIRP" and p.status == "admitted"
    label = "Admitted CIRP" if is_admitted_cirp else f"{p.petition_type} ({p.status})"
    return FraudSignal(
        signal_type="NCLT_PROCEEDING_MATCH",
        severity=Severity.CRITICAL,
        score_contribution=40.0,
        evidence_string=(
            f"{label} on file at {p.bench or 'NCLT'} — "
            f"case {p.case_number}, filed {p.filing_date.isoformat()}, "
            f"claimed amount {fmt_inr(p.amount_claimed)}. "
            f"PRD §7.3 override: forces fraud_risk_score ≥ {NCLT_WD_FLOOR_SCORE:.0f}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "NCLTProceeding", "case_number": p.case_number, "cin": p.cin},
        ],
    )


def _drt_signal(p: RawNCLTProceeding) -> FraudSignal:
    return FraudSignal(
        signal_type="DRT_PROCEEDING_MATCH",
        severity=Severity.CRITICAL,
        score_contribution=40.0,
        evidence_string=(
            f"DRT proceeding {p.case_number} (status: {p.status}) filed "
            f"{p.filing_date.isoformat()} at {p.bench or 'DRT'}, claim "
            f"{fmt_inr(p.amount_claimed)}. PRD §7.3 override: forces "
            f"fraud_risk_score ≥ {NCLT_WD_FLOOR_SCORE:.0f}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "NCLTProceeding", "case_number": p.case_number, "cin": p.cin},
        ],
    )


def _wilful_signal(w: RawWilfulDefaulter) -> FraudSignal:
    return FraudSignal(
        signal_type="WILFUL_DEFAULTER_MATCH",
        severity=Severity.CRITICAL,
        score_contribution=40.0,
        evidence_string=(
            f"Declared wilful defaulter by {w.bank_name} on "
            f"{w.declared_date.isoformat()} for {fmt_inr(w.amount)} ({w.source}). "
            f"PRD §7.3 override: forces fraud_risk_score ≥ {NCLT_WD_FLOOR_SCORE:.0f}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "WilfulDefaulter", "cin": w.cin, "din": w.din},
        ],
    )


class OverrideResult(NamedTuple):
    """Structured return from `apply_override` — Stream 5.1 of the
    production-grade closure plan.

    Old contract returned `(final_score, applied)`. New contract adds
    `matched_signal_ids` so an investigator can trace *which* M9 signal
    triggered the floor instead of seeing only a boolean. The override
    audit trail was missing from RiskReport previously, which made the
    "score jumped from 50 to 75 but the rule modules look unanimous"
    case look like a bug to anyone debugging downstream.
    """

    final_score: float
    applied: bool
    matched_signal_ids: tuple[str, ...]


_OVERRIDE_TRIGGERING_TYPES = {
    "NCLT_PROCEEDING_MATCH",
    "DRT_PROCEEDING_MATCH",
    "WILFUL_DEFAULTER_MATCH",
}


def apply_override(score: float, signals: list[FraudSignal]) -> OverrideResult:
    """PRD §7.3 override rule for Module 9.

    Returns an `OverrideResult` namedtuple `(final_score, applied,
    matched_signal_ids)`. If any signal in `signals` matches one of
    `_OVERRIDE_TRIGGERING_TYPES` the score is promoted to
    >= NCLT_WD_FLOOR_SCORE and every matching signal's id is recorded.

    NamedTuple supports positional unpacking (`final_score, applied, ids
    = apply_override(...)`) and attribute access — callers may pick
    whichever reads better at the call site.
    """
    matched = tuple(
        s.signal_id for s in signals
        if s.signal_type in _OVERRIDE_TRIGGERING_TYPES
    )
    if not matched:
        return OverrideResult(clamp_score(score), False, ())
    return OverrideResult(
        max(clamp_score(score), NCLT_WD_FLOOR_SCORE),
        True,
        matched,
    )


def run(inputs: NCLTDefaulterInputs) -> ModuleResult:
    """Build FraudSignal rows for every NCLT/DRT/WD match against this CIN."""
    cin = inputs.cin
    proceedings = _filter_for_cin(cin, inputs.nclt_proceedings)
    wds = _filter_wd_for_cin(cin, inputs.wilful_declarations)

    signals: list[FraudSignal] = []
    for p in proceedings:
        if p.petition_type == "DRT":
            signals.append(_drt_signal(p))
        elif p.petition_type == "CIRP" and p.status == "admitted":
            signals.append(_nclt_signal(p))
        elif p.petition_type in {"CIRP", "winding_up", "other"}:
            # Filed-but-not-yet-admitted still warrants a CRITICAL signal —
            # the override is reserved for admitted CIRP / DRT / wilful declared.
            sig = _nclt_signal(p)
            signals.append(sig)
    for w in wds:
        signals.append(_wilful_signal(w))

    raw_score = clamp_score(sum(s.score_contribution for s in signals))
    final_score = apply_override(raw_score, signals).final_score

    return ModuleResult(
        module_name=MODULE_NAME,
        cin=cin,
        year=None,
        score=final_score,
        signals=signals,
    )


def run_for_cins(
    cins: Iterable[str],
    proceedings: list[RawNCLTProceeding],
    wds: list[RawWilfulDefaulter],
) -> dict[str, ModuleResult]:
    """Batch convenience: one ModuleResult per CIN. CIns with no matches
    produce an empty ModuleResult (score=0, signals=[])."""
    out: dict[str, ModuleResult] = {}
    for cin in cins:
        out[cin] = run(NCLTDefaulterInputs(
            cin=cin,
            nclt_proceedings=proceedings,
            wilful_declarations=wds,
        ))
    return out

"""Stream 5.1 — apply_override now returns matched_signal_ids.

The previous `tuple[float, bool]` shape forced investigators to walk the
full evidence chain to figure out *which* M9 signal triggered the
PRD §7.3 floor. The new `OverrideResult` NamedTuple records every
triggering signal_id, and RiskReport surfaces them as
`override_matched_signal_ids`. Tests pin the new contract.
"""

from __future__ import annotations

from backend.app.modules.base import FraudSignal, Severity
from backend.app.modules.m09_nclt_defaulter import (
    NCLT_WD_FLOOR_SCORE,
    OverrideResult,
    apply_override,
)


def _signal(*, signal_type: str, severity: Severity = Severity.CRITICAL) -> FraudSignal:
    return FraudSignal(
        signal_type=signal_type,
        severity=severity,
        score_contribution=85.0,
        evidence_string="<test>",
        module_name="m09_nclt_defaulter",
    )


def test_no_override_returns_empty_matched_ids():
    res = apply_override(45.0, [_signal(signal_type="BENEISH_M_SCORE_BREACH")])
    assert isinstance(res, OverrideResult)
    assert res.applied is False
    assert res.matched_signal_ids == ()
    assert res.final_score == 45.0


def test_nclt_signal_triggers_floor_and_records_id():
    sig = _signal(signal_type="NCLT_PROCEEDING_MATCH")
    res = apply_override(40.0, [sig])
    assert res.applied is True
    assert res.final_score == NCLT_WD_FLOOR_SCORE
    assert res.matched_signal_ids == (sig.signal_id,)


def test_wilful_defaulter_signal_also_triggers():
    sig = _signal(signal_type="WILFUL_DEFAULTER_MATCH")
    res = apply_override(10.0, [sig])
    assert res.applied is True
    assert res.matched_signal_ids == (sig.signal_id,)


def test_multiple_triggering_signals_all_recorded():
    a = _signal(signal_type="NCLT_PROCEEDING_MATCH")
    b = _signal(signal_type="DRT_PROCEEDING_MATCH")
    c = _signal(signal_type="WILFUL_DEFAULTER_MATCH")
    other = _signal(signal_type="BENEISH_M_SCORE_BREACH")
    res = apply_override(20.0, [a, other, b, c])
    assert res.applied is True
    assert res.matched_signal_ids == (a.signal_id, b.signal_id, c.signal_id)
    # Non-triggering signal must NOT appear in the matched list.
    assert other.signal_id not in res.matched_signal_ids


def test_override_only_lifts_when_base_below_floor():
    """If the base score is already at/above the floor the override is
    *still applied semantically* (matched_signal_ids populated) but the
    score doesn't move — pins the contract that `applied` reflects
    presence-of-trigger, not change-in-score."""
    sig = _signal(signal_type="NCLT_PROCEEDING_MATCH")
    res = apply_override(NCLT_WD_FLOOR_SCORE + 5.0, [sig])
    assert res.applied is True
    assert res.matched_signal_ids == (sig.signal_id,)
    assert res.final_score == NCLT_WD_FLOOR_SCORE + 5.0


def test_namedtuple_supports_positional_and_attribute_access():
    """The result is a NamedTuple — both calling styles must work
    so existing positional unpacking remains an option for callers."""
    sig = _signal(signal_type="NCLT_PROCEEDING_MATCH")
    res = apply_override(50.0, [sig])
    # Positional
    score, applied, ids = res
    assert score == NCLT_WD_FLOOR_SCORE
    assert applied is True
    assert ids == (sig.signal_id,)
    # Attribute
    assert res.final_score == score
    assert res.applied == applied
    assert res.matched_signal_ids == ids

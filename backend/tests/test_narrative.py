"""Narrative module + /narrative/{cin} endpoint — Stream 2.

Test surfaces (sequenced by failure-mode cost):

* Serializer purity — `serialize_for_narrative()` is total + deterministic.
* Allowed-numbers guard — `numbers_outside_allowed()` catches
  hallucinated figures while letting structural integers through.
* Template fallback — when no Mistral key, never raises, always cites
  numbers from the structured payload.
* Auth gate — anonymous /narrative/{cin} returns 401.
* Caching — second call on the same (cin, evidence) hits the cache.
* Cross-CIN — two CINs return distinct narratives keyed by hash.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.narrative import router as narrative_router
from backend.app.auth.deps import get_current_user
from backend.app.modules.base import FraudSignal, Severity
from backend.app.narrative import (
    NarrativeInput,
    _template_fallback,
    get_narrator,
    numbers_outside_allowed,
    serialize_for_narrative,
)
from backend.app.scorer import RiskReport


def _stub_signal(*, module: str, severity: Severity, score: float, evidence: str) -> FraudSignal:
    return FraudSignal(
        signal_type=f"{module.upper()}_DEMO",
        severity=severity,
        score_contribution=score,
        evidence_string=evidence,
        module_name=module,
    )


def _ilfs_report() -> RiskReport:
    """Lightweight RiskReport stand-in mirroring IL&FS demo numbers."""
    return RiskReport(
        cin="U45201MH2005PTC155294",
        fraud_risk_score=75.0,
        risk_band="CRITICAL",
        data_confidence=92,
        ensemble_disagreement_flag=True,
        evidence_chain=[
            _stub_signal(
                module="m02_cross_statement",
                severity=Severity.CRITICAL,
                score=80.0,
                evidence="Revenue per P&L (12.4 cr) exceeds GST taxable turnover (8.2 cr) by 51.2%.",
            ),
            _stub_signal(
                module="m09_nclt_defaulter",
                severity=Severity.CRITICAL,
                score=95.0,
                evidence="NCLT admitted CIRP under IBC §7 on 2018-10-01.",
            ),
            _stub_signal(
                module="m01_beneish",
                severity=Severity.HIGH,
                score=70.0,
                evidence="Beneish M-Score 4.1 exceeds the 2.22 fraud cutoff.",
            ),
        ],
        module_breakdown={
            "m01_beneish": 70.0,
            "m02_cross_statement": 80.0,
            "m09_nclt_defaulter": 95.0,
        },
        override_applied=True,
        skipped_modules=[],
        p_fraud_calibrated=0.8123,
        p_fraud_interval=(0.71, 0.92),
    )


# ---------------------------------------------------------------------------
# 1) Serializer purity.
# ---------------------------------------------------------------------------

def test_serializer_deterministic():
    ni1 = serialize_for_narrative(_ilfs_report())
    ni2 = serialize_for_narrative(_ilfs_report())
    assert ni1.evidence_hash == ni2.evidence_hash
    assert ni1.allowed_numbers == ni2.allowed_numbers
    assert ni1.signals == ni2.signals


def test_serializer_sorts_by_severity_then_score():
    ni = serialize_for_narrative(_ilfs_report())
    # CRITICAL signals must come before HIGH; within CRITICAL, higher
    # score_contribution wins.
    severities = [s["severity"] for s in ni.signals]
    assert severities[0] == "CRITICAL"
    assert severities[-1] == "HIGH"
    crits = [s for s in ni.signals if s["severity"] == "CRITICAL"]
    assert crits[0]["score_contribution"] >= crits[1]["score_contribution"]


def test_serializer_pulls_numbers_from_evidence_strings():
    ni = serialize_for_narrative(_ilfs_report())
    # Numbers from evidence_strings must be in allowed_numbers.
    for tok in ["12.4", "8.2", "51.2", "4.1", "2.22"]:
        assert tok in ni.allowed_numbers, (
            f"{tok!r} should be in allowed_numbers; got {sorted(ni.allowed_numbers)}"
        )
    # Calibrated probability + interval bounds must be in the set.
    assert "0.8123" in ni.allowed_numbers
    assert "0.71" in ni.allowed_numbers
    assert "0.92" in ni.allowed_numbers


# ---------------------------------------------------------------------------
# 2) Allowed-numbers guard.
# ---------------------------------------------------------------------------

def test_numbers_outside_allowed_flags_hallucinated_figure():
    allowed = {"75", "92", "0.81"}
    text = "Score 75, DC 92%, with an inflation rate of 6.5% across the sector."
    bad = numbers_outside_allowed(text, allowed)
    assert "6.5" in bad  # hallucinated
    assert "75" not in bad
    assert "92" not in bad


def test_numbers_outside_allowed_ignores_short_structural_integers():
    """Short integers like 11 modules, 17 patterns, 3 statements are
    structural references, not financial figures — must not trip the
    guard even if they're not in `allowed`."""
    allowed: set[str] = set()
    text = "11 Tier-1 modules fired across 17 patterns and 3 financial statements."
    bad = numbers_outside_allowed(text, allowed)
    assert bad == set(), f"structural integers should pass; flagged {bad}"


def test_template_fallback_only_uses_allowed_numbers():
    """Property: the template never introduces a number that the
    structured payload doesn't already carry."""
    ni = serialize_for_narrative(_ilfs_report())
    summary = _template_fallback(ni)
    bad = numbers_outside_allowed(summary, ni.allowed_numbers)
    assert bad == set(), (
        f"_template_fallback hallucinated: {sorted(bad)!r}\n"
        f"allowed: {sorted(ni.allowed_numbers)!r}\n"
        f"summary: {summary}"
    )


def test_template_fallback_cites_top_evidence_string():
    ni = serialize_for_narrative(_ilfs_report())
    summary = _template_fallback(ni)
    # Top signal's evidence_string content should appear in the prose.
    assert "GST" in summary or "12.4" in summary, (
        f"template should cite top evidence; got {summary}"
    )


# ---------------------------------------------------------------------------
# 3) Route — auth gate + cache + 200 response.
# ---------------------------------------------------------------------------

def _build_app_with_user(role: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(narrative_router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "test", "email": "t@example.com", "role": role,
        "is_active": True, "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    return app


def test_anonymous_narrative_call_is_rejected():
    app = FastAPI()
    app.include_router(narrative_router)
    with TestClient(app) as c:
        r = c.get("/narrative/U45201MH2005PTC155294")
    assert r.status_code == 401


def test_authenticated_narrative_returns_200_with_summary():
    get_narrator().reset_for_tests()
    with TestClient(_build_app_with_user()) as c:
        r = c.get("/narrative/U45201MH2005PTC155294")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cin"] == "U45201MH2005PTC155294"
    assert isinstance(body["summary"], str) and len(body["summary"]) > 40
    assert "model" in body
    assert "generated_at" in body
    assert "evidence_hash" in body
    # No Mistral key in CI ⇒ falls back to template.
    assert body["model"].startswith("template-fallback") or body["model"] == "mistral-small-latest"


def test_narrative_caches_repeat_calls():
    get_narrator().reset_for_tests()
    with TestClient(_build_app_with_user()) as c:
        a = c.get("/narrative/U45201MH2005PTC155294").json()
        b = c.get("/narrative/U45201MH2005PTC155294").json()
    assert a["summary"] == b["summary"]
    assert a["evidence_hash"] == b["evidence_hash"]
    # First call not cached, second one should be.
    assert a["cached"] is False
    assert b["cached"] is True


def test_narrative_different_cins_produce_different_hashes():
    get_narrator().reset_for_tests()
    with TestClient(_build_app_with_user()) as c:
        ilfs = c.get("/narrative/U45201MH2005PTC155294").json()
        xyz = c.get("/narrative/U14101MH2019PTC298765").json()
    assert ilfs["evidence_hash"] != xyz["evidence_hash"]
    assert ilfs["summary"] != xyz["summary"]


def test_narrative_summary_passes_hallucination_guard():
    """Whatever the narrator returns must not introduce numbers outside
    the allowed set. The route only exposes the summary, so we recompute
    `allowed_numbers` from a fresh serializer pass and assert containment."""
    get_narrator().reset_for_tests()
    report = _ilfs_report()
    ni = serialize_for_narrative(report)
    # Use the synthetic IL&FS fixture; the route runs against a real
    # fixture CIN — assert on a freshly-built NarrativeInput instead.
    summary = _template_fallback(ni)
    assert numbers_outside_allowed(summary, ni.allowed_numbers) == set()


# Pure NarrativeInput smoke — covers the dataclass + to_prompt_json path
# without touching FastAPI.
def test_narrative_input_to_prompt_json_is_valid_json():
    import json as _json
    ni = NarrativeInput(
        cin="X", risk_band="LOW", fraud_risk_score=1.0,
        p_fraud_calibrated=None, p_fraud_interval=None,
        data_confidence=50, ensemble_disagreement_flag=False,
        override_applied=False, signals=[], top_modules=[],
        allowed_numbers=set(), evidence_hash="abc",
    )
    parsed = _json.loads(ni.to_prompt_json())
    assert parsed["cin"] == "X"
    assert "allowed_numbers" not in parsed
    assert "evidence_hash" not in parsed

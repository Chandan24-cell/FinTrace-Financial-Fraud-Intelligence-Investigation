"""Day-26 IL&FS manual-calculation parity tests — PRD §10.

PRD §10 Day-26 Done When: 'IL&FS scores match manual calculation.'

These tests hard-code the hand-computed Beneish ratios + M-Score for
IL&FS FY2017 vs FY2016 (using the fixture numbers in
infra/seeds/companies/U45201MH2005PTC155294.json) and pin them against
backend/app/modules/m01_beneish.py. They also assert the expected M2
cross-statement signals fire on IL&FS without any /upload overlay.

The reference numbers were computed by running each PRD §4.1 ratio
formula on the raw fixture rows — see scripts/day26_rehearsal.py for the
sequence the demo will hit live.

If any of these tests start failing, *first* check whether the IL&FS
fixture was edited (legitimate) before assuming the module logic
regressed.
"""

from __future__ import annotations

import pytest

from backend.app.ingest.sources import FixtureSource
from backend.app.modules import m01_beneish, m02_cross_statement
from backend.app.modules.base import Severity
from backend.app.modules.m02_cross_statement import CrossStatementInputs

ILFS_CIN = "U45201MH2005PTC155294"

# Hand-computed on IL&FS FY2017 vs FY2016 fixture numbers. See PRD §4.1
# for the formulas. Tolerance is generous to absorb floating-point drift;
# the assertion shape pins module behaviour, not exact-FP equality.
EXPECTED_RATIOS = {
    "DSRI": 2.000340,
    "GMI":  1.006025,
    "AQI":  0.969420,
    "SGI":  1.056000,
    "DEPI": 1.072007,
    "SGAI": 1.065341,
    "LVGI": 1.051563,
    "TATA": 0.058218,
}
EXPECTED_M_SCORE = -1.266326

# Ratios that breach their PRD §4.1 threshold on IL&FS FY17.
EXPECTED_BREACHED_RATIOS = {"DSRI", "GMI", "SGAI", "LVGI", "TATA"}


@pytest.mark.asyncio
async def test_ilfs_beneish_ratios_match_manual_calculation() -> None:
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    assert bundle is not None
    fs = {f.year: f for f in bundle.financials}
    prev, curr = fs[2016], fs[2017]

    ratios = m01_beneish.ratios(curr, prev)
    for name, expected in EXPECTED_RATIOS.items():
        actual = ratios[name]
        assert abs(actual - expected) < 1e-3, (
            f"Beneish {name}: module={actual:.6f} but manual={expected:.6f}"
        )


@pytest.mark.asyncio
async def test_ilfs_m_score_matches_manual_calculation() -> None:
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    fs = {f.year: f for f in bundle.financials}
    m = m01_beneish.m_score(fs[2017], fs[2016])
    assert abs(m - EXPECTED_M_SCORE) < 1e-3, (
        f"M-Score: module={m:.6f} but manual={EXPECTED_M_SCORE:.6f}"
    )
    # Above the -1.78 manipulation threshold → CRITICAL band on IL&FS.
    assert m > m01_beneish.M_SCORE_THRESHOLD


@pytest.mark.asyncio
async def test_ilfs_beneish_emits_critical_signal_and_expected_breaches() -> None:
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    fs = {f.year: f for f in bundle.financials}
    result = m01_beneish.run(fs[2017], fs[2016])

    # CRITICAL M-Score breach signal must fire.
    headline = [s for s in result.signals if s.signal_type == "BENEISH_M_SCORE_BREACH"]
    assert len(headline) == 1
    assert headline[0].severity is Severity.CRITICAL

    # The five individually-breached ratios from the manual calc must each
    # appear as their own FraudSignal.
    breach_types = {s.signal_type.replace("BENEISH_", "").replace("_BREACH", "")
                    for s in result.signals
                    if s.signal_type.startswith("BENEISH_") and s.signal_type != "BENEISH_M_SCORE_BREACH"}
    assert breach_types == EXPECTED_BREACHED_RATIOS, (
        f"Breached ratios mismatch.\n  expected={EXPECTED_BREACHED_RATIOS}\n  actual  ={breach_types}"
    )


@pytest.mark.asyncio
async def test_ilfs_cross_statement_fires_expected_checks_without_uploads() -> None:
    """Module 2 should produce the textbook IL&FS evidence even without
    a GST/bank overlay — the FS rows alone contain enough red flags."""
    bundle = await FixtureSource().fetch_bundle(ILFS_CIN)
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    inputs = CrossStatementInputs(
        current=fs_sorted[-1],
        previous=fs_sorted[-2],
        cwip_history=fs_sorted[-3:],
        cersai_charges=list(bundle.charges),
        gst_entity=None,
        bank_credits_total=None,
    )
    result = m02_cross_statement.run(inputs)
    fired_types = {s.signal_type for s in result.signals}

    # These don't require uploaded GST or bank overlay; they should fire
    # on IL&FS's intrinsic FS data.
    #
    # Note: CASH_CONVERSION abstains on loss years (PAT <= 0 — IL&FS FY18
    # PAT = -113 cr), so the textbook IL&FS M2 evidence on FY18 is
    # IMPLIED_INTEREST + CWIP_STALE.
    expected_without_overlay = {
        "CROSS_STMT_IMPLIED_INTEREST",  # finance_costs / borrowings impossibly low
        "CROSS_STMT_CWIP_STALE",         # CWIP > 5% of total assets for 3+ years
    }
    missing = expected_without_overlay - fired_types
    assert not missing, (
        f"Expected M2 checks to fire on IL&FS without overlay; "
        f"missing: {missing}; got: {sorted(fired_types)}"
    )

    # Specific-numbers rule (PRD §4.2): every fired signal cites the
    # exact rupees/percentages from the underlying FS rows.
    for s in result.signals:
        assert any(token in s.evidence_string for token in ("FY", "₹", "%")), (
            f"M2 signal {s.signal_type} lacks specific-numbers evidence: "
            f"{s.evidence_string!r}"
        )

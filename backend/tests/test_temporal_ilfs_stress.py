"""IL&FS temporal stress test — PRD §10 Day 22 acceptance.

The Day-22 'Done When' line is verbatim: 'Temporal signals fire correctly
on IL&FS.' This test locks the behaviour in CI so a future refactor of
M6 can't silently drop the multi-year audit-flag signal that the demo's
CRITICAL-band determination depends on.

IL&FS fixture (FY2014–FY2018) carries:
  - PAT trajectory 168 cr / 182 cr / 185 cr / -130 cr / -1,130 cr
  - going_concern_flag flipping False → True between FY2016 and FY2017
  - same auditor (Deloitte H&S) throughout, so T2 should *not* fire
"""

from __future__ import annotations

import pytest

from backend.app.ingest.sources import FixtureSource
from backend.app.modules import m06_temporal
from backend.app.modules.base import Severity
from backend.app.modules.m06_temporal import TemporalInputs

ILFS_CIN = "U45201MH2005PTC155294"


@pytest.mark.asyncio
async def test_m6_fires_critical_signal_on_ilfs_multiyear_data() -> None:
    src = FixtureSource()
    bundle = await src.fetch_bundle(ILFS_CIN)
    assert bundle is not None, "IL&FS fixture missing from infra/seeds/companies"
    assert len(bundle.financials) >= 4, "IL&FS needs ≥4 FS years for T6/T8 to be meaningful"

    result = m06_temporal.run(TemporalInputs(
        financials=list(bundle.financials),
        directors=list(bundle.directors),
    ))

    # PRD §4.6 #8 — going-concern flag flip should fire a CRITICAL signal.
    critical_types = {s.signal_type for s in result.signals
                      if s.severity is Severity.CRITICAL}
    assert "TEMPORAL_POLICY_CHANGE_POST_BAD_YEAR" in critical_types, (
        f"Expected CRITICAL T8 on IL&FS; got signals: "
        f"{[(s.signal_type, s.severity.value) for s in result.signals]}"
    )

    # Score floor: any single CRITICAL temporal signal = ≥ 40 pts.
    assert result.score >= 40.0, f"M6 score {result.score} below CRITICAL floor"


@pytest.mark.asyncio
async def test_m6_does_not_fire_t2_auditor_change_on_ilfs() -> None:
    """Negative-control: Deloitte stays auditor across all 5 years."""
    src = FixtureSource()
    bundle = await src.fetch_bundle(ILFS_CIN)
    assert bundle is not None
    result = m06_temporal.run(TemporalInputs(
        financials=list(bundle.financials),
        directors=list(bundle.directors),
    ))
    types = {s.signal_type for s in result.signals}
    assert "TEMPORAL_AUDITOR_CHANGE" not in types, (
        "T2 should not fire on IL&FS — Deloitte is auditor throughout. "
        "If this is firing, the fixture or M6 logic has drifted."
    )

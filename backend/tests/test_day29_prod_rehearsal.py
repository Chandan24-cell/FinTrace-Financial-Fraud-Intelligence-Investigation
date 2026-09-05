"""Day-29 production rehearsal — PRD §10.

PRD §10 Day-29 verbatim: 'Rehearsal 3 on production only. All three
scenarios. Verify offline fallback video accessible. Done When:
Production rehearsal passes. Fallback video confirmed playable.'

This module is dual-mode:

  - **Local run** (`PROD_API_URL` unset): tests are skipped with a clear
    message so the day-to-day pytest stays green. The local rehearsal
    is already locked in CI by `test_day27_three_scenarios.py`.

  - **Production run** (`PROD_API_URL=https://sentinel-g.duckdns.org`):
    every scenario fires HTTP requests at the live Oracle Cloud host
    and asserts the same three-scenario contract.

The operator runs:

    PROD_API_URL=https://sentinel-g.duckdns.org \
        ./.venv/Scripts/python.exe -m pytest backend/tests/test_day29_prod_rehearsal.py -v

Setting `PROD_JWT` additionally covers the /report endpoint (which
requires a real user-table JWT). Without it the rehearsal still proves
/analyse + /provenance end-to-end and flags /report as 401-expected.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.day27_rehearsal_multi import (
    AMTEK_CIN,
    ILFS_CIN,
    ITC_CAROUSEL_CINS,
    _active_target,
    _build_client,
    _load_itc_ring,
    _scenario_full_flow,
)

ROOT = Path(__file__).resolve().parents[2]

# Per-test skip on the four scenarios that need a live URL. The two
# repo-artifact tests at the bottom of the module always run.
prod_only = pytest.mark.skipif(
    not os.environ.get("PROD_API_URL"),
    reason=(
        "PROD_API_URL not set — Day 29 production rehearsal is opt-in. "
        "Run with PROD_API_URL=https://sentinel-g.duckdns.org pytest ..."
    ),
)


@prod_only
@pytest.mark.asyncio
async def test_target_label_reports_prod_url() -> None:
    """Defensive: the harness must know it's running against prod."""
    label = _active_target()
    assert label.startswith("PROD "), label
    assert "duckdns.org" in label or "://" in label, label


@prod_only
@pytest.mark.asyncio
async def test_prod_scenario_1_ilfs_critical_band() -> None:
    async with await _build_client() as client:
        result = await _scenario_full_flow(client, ILFS_CIN, expected_band="CRITICAL")
    assert result["ok"], result
    h = result["headline"]
    assert h["risk_band"] == "CRITICAL"
    assert h["fraud_risk_score"] >= 60.0
    assert h["evidence_signal_count"] >= 5


@prod_only
@pytest.mark.asyncio
async def test_prod_scenario_2_amtek_critical_band() -> None:
    async with await _build_client() as client:
        result = await _scenario_full_flow(client, AMTEK_CIN, expected_band="CRITICAL")
    assert result["ok"], result
    h = result["headline"]
    # M9 wilful-defaulter override forces score >= 75 on Amtek.
    assert h["fraud_risk_score"] >= 75.0
    assert h["risk_band"] == "CRITICAL"


@prod_only
@pytest.mark.asyncio
async def test_prod_scenario_3_itc_carousel_all_critical() -> None:
    async with await _build_client() as client:
        for cin in ITC_CAROUSEL_CINS:
            result = await _scenario_full_flow(client, cin, expected_band="CRITICAL")
            assert result["ok"], (cin, result)
            assert result["headline"]["risk_band"] == "CRITICAL"


def test_prod_itc_ring_fixture_loadable_from_repo() -> None:
    """The 7-node synthetic ring lives in the repo (not on the server) —
    confirm the demo's graph view will render against the committed JSON.
    Independent of PROD_API_URL but kept in this module so the operator
    sees the full Day-29 picture in one run."""
    ring = _load_itc_ring()
    assert ring["ok"], ring


def test_fallback_video_checklist_present() -> None:
    """PRD §10 Day-29 Done When: 'Fallback video confirmed playable.'
    We can't open a video file in CI, but we can lock that the operator
    has a checklist on file with the exact verification steps."""
    path = ROOT / "docs" / "FALLBACK_VIDEO_CHECKLIST.md"
    assert path.exists(), "docs/FALLBACK_VIDEO_CHECKLIST.md missing"
    text = path.read_text(encoding="utf-8")
    for marker in (
        "recording target",      # 1080p / mp4 callout
        "hosting",               # where the mp4 lives
        "URL in submission",     # Devfolio link reference
        "playback verification", # actually plays end-to-end
    ):
        assert marker.lower() in text.lower(), (
            f"FALLBACK_VIDEO_CHECKLIST missing section: {marker!r}"
        )

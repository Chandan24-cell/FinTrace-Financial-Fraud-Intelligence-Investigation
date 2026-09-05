"""GET /trending — public landing-surface feed.

Backs the Dashboard's "Today's findings" panel. No auth — the feed is
the equivalent of a homepage marquee: judges and new users see live
counts the moment they land, before any /analyse call.

Three sections:

  top_critical_today   — the 4 verified demo cases the engine catches
                         as CRITICAL/HIGH. Hardcoded order so judges
                         see the strongest cases first.
  newest_shell_clusters — top-by-size clusters from the M0 atlas.
                         Sized for a 3-row preview.
  stats                — counts the public /sources endpoint already
                         carries, surfaced here for the dashboard
                         banner so the Dashboard doesn't need a second
                         API call.

The "today" framing is aspirational: in this deployment the demo
cases and atlas don't change across the day, so the feed is stable.
The shape is forward-compatible: when the weekly DGGI refresh job
appends new dggi_*.json files, this endpoint will surface them too.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.modules.m0_master_shell_atlas import get_atlas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trending", tags=["trending"])


class TrendingCase(BaseModel):
    cin: str
    name: str
    band: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    blurb: str
    source: str
    evidence: int
    route: str


class TrendingCluster(BaseModel):
    cluster_id: str
    signal_type: str
    size: int
    state: str | None
    anchor_value: str
    evidence_string: str


class TrendingStats(BaseModel):
    total_critical_cases: int
    total_shell_clusters: int
    total_flagged_cins: int
    rows_in_tn_corpus: int


class TrendingFeed(BaseModel):
    generated_at: str
    top_critical_today: list[TrendingCase]
    newest_shell_clusters: list[TrendingCluster]
    stats: TrendingStats


# The 4 verified cases — kept in sync with frontend/src/lib/demoCases.ts.
# Hardcoded server-side so the feed is independent of frontend deploy
# timing (Vercel can be a few minutes behind GHCR).
_VERIFIED_CASES: list[TrendingCase] = [
    TrendingCase(
        cin="U45201MH2005PTC155294",
        name="IL&FS",
        band="CRITICAL",
        blurb="₹91,000 cr default · canonical SME loan fraud",
        source="SFIO 2019 report + NCLT CP(IB) 3334/MB/2018",
        evidence=19,
        route="/dashboard?cin=U45201MH2005PTC155294",
    ),
    TrendingCase(
        cin="L65910MH1984PLC032662",
        name="DHFL",
        band="CRITICAL",
        blurb="₹31,200 cr round-tripping · evergreening canonical",
        source="SFIO + RBI + NCLT CP(IB) 4258/MB/2019",
        evidence=18,
        route="/evergreening",
    ),
    TrendingCase(
        cin="U27101MH2010PTC215432",
        name="Amtek Auto",
        band="CRITICAL",
        blurb="₹1,240 cr CIRP · wilful defaulter (ICICI)",
        source="SFIO + NCLT Mumbai CIRP",
        evidence=11,
        route="/dashboard?cin=U27101MH2010PTC215432",
    ),
    TrendingCase(
        cin="DGGI-MZU-2024-Q1",
        name="DGGI ITC carousel",
        band="HIGH",
        blurb="7-node Mumbai zone bust · ₹512 cr fake ITC",
        source="DGGI Mumbai Zonal Unit · cbic.gov.in",
        evidence=12,
        route="/itc",
    ),
]


def _newest_clusters(limit: int = 5) -> list[TrendingCluster]:
    """Take the largest clusters across all three signal types — these
    are 'newest' in the sense of 'most likely to surface next', since
    address-clustering at scale is rare and gets ranked first."""
    atlas = get_atlas()
    picked: list[TrendingCluster] = []
    # Interleave one of each type so the feed isn't a wall of identical
    # ADDRESS_CLUSTER rows.
    pools = {
        "ADDRESS_CLUSTER": list(atlas.by_signal.get("ADDRESS_CLUSTER", [])),
        "MASS_INCORPORATION": list(atlas.by_signal.get("MASS_INCORPORATION", [])),
        "PAPER_SHELL": list(atlas.by_signal.get("PAPER_SHELL", [])),
    }
    while len(picked) < limit and any(pools.values()):
        for sig_type, ids in pools.items():
            if not ids or len(picked) >= limit:
                continue
            cid = ids.pop(0)
            cluster = atlas.clusters.get(cid)
            if cluster is None:
                continue
            picked.append(
                TrendingCluster(
                    cluster_id=cluster.cluster_id,
                    signal_type=cluster.signal_type,
                    size=cluster.size,
                    state=cluster.state,
                    anchor_value=cluster.anchor_value,
                    evidence_string=cluster.evidence_string,
                )
            )
    return picked


@router.get("", response_model=TrendingFeed)
async def trending_feed() -> TrendingFeed:
    """Live snapshot of CRITICAL cases + newest shell clusters + stats."""
    atlas = await asyncio.to_thread(get_atlas)
    newest = _newest_clusters(limit=5)
    stats = TrendingStats(
        total_critical_cases=sum(1 for c in _VERIFIED_CASES if c.band == "CRITICAL"),
        total_shell_clusters=(
            len(atlas.by_signal.get("ADDRESS_CLUSTER", []))
            + len(atlas.by_signal.get("MASS_INCORPORATION", []))
            + len(atlas.by_signal.get("PAPER_SHELL", []))
        ),
        total_flagged_cins=len(atlas.cin_to_clusters),
        rows_in_tn_corpus=atlas.rows_scanned,
    )
    return TrendingFeed(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        top_critical_today=_VERIFIED_CASES,
        newest_shell_clusters=newest,
        stats=stats,
    )

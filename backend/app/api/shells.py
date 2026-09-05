"""GET /shells — Master-data Shell Atlas (M0).

Public endpoint backing the /shells page. Surfaces ranked shell-like
company clusters computed from the data.gov.in MCA bulk CSV (no
financials needed). Three signals:

  ADDRESS_CLUSTER     — companies sharing one registered office address
  MASS_INCORPORATION  — companies registered same day in same state
  PAPER_SHELL         — paid-up capital < 10% of authorised, > 2 years old

The atlas is computed once lazily on first call (the CSV scan takes
~5-10s on the 250k-row TN snapshot). Subsequent requests hit dict
lookups. No auth — judges and auditors browse the inventory directly
to verify the engine's master-data signal sources.

When `/analyse/{cin}` runs on a CIN in the atlas, the scorer attaches
a MASTER_DATA_SHELL_CLUSTER FraudSignal (see backend/app/scorer.py),
so a TN search that previously returned an empty dossier now shows
the cluster context that flagged it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.app.modules.m0_master_shell_atlas import (
    ShellCluster,
    ShellSignalType,
    get_atlas,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shells", tags=["shells"])

SignalParam = Literal["address", "mass_incorp", "paper", "all"]
_SIGNAL_MAP: dict[SignalParam, list[ShellSignalType]] = {
    "address": ["ADDRESS_CLUSTER"],
    "mass_incorp": ["MASS_INCORPORATION"],
    "paper": ["PAPER_SHELL"],
    "all": ["ADDRESS_CLUSTER", "MASS_INCORPORATION", "PAPER_SHELL"],
}


class ShellClusterOut(BaseModel):
    cluster_id: str
    signal_type: str
    size: int
    state: str | None = None
    anchor_value: str
    sample_cins: list[str]
    sample_names: list[str]
    evidence_string: str
    source: str


class ShellsPage(BaseModel):
    signal: str
    total: int = Field(..., description="Total clusters matching the signal filter")
    limit: int
    offset: int
    clusters: list[ShellClusterOut]
    atlas: dict


def _to_out(c: ShellCluster) -> ShellClusterOut:
    return ShellClusterOut(
        cluster_id=c.cluster_id,
        signal_type=c.signal_type,
        size=c.size,
        state=c.state,
        anchor_value=c.anchor_value,
        sample_cins=c.sample_cins,
        sample_names=c.sample_names,
        evidence_string=c.evidence_string,
        source=c.source,
    )


@router.get("", response_model=ShellsPage)
async def list_shells(
    signal: Annotated[SignalParam, Query(description="Cluster signal type to filter")] = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ShellsPage:
    """Paginated list of ranked shell clusters by signal type."""
    # Atlas build is CPU + I/O bound — push to a thread to keep the
    # event loop responsive on first call.
    atlas = await asyncio.to_thread(get_atlas)
    wanted_types = _SIGNAL_MAP[signal]
    cluster_ids: list[str] = []
    for st in wanted_types:
        cluster_ids.extend(atlas.by_signal.get(st, []))
    total = len(cluster_ids)
    window = cluster_ids[offset : offset + limit]
    clusters = [_to_out(atlas.clusters[cid]) for cid in window if cid in atlas.clusters]
    return ShellsPage(
        signal=signal,
        total=total,
        limit=limit,
        offset=offset,
        clusters=clusters,
        atlas={
            "rows_scanned": atlas.rows_scanned,
            "csv_path": atlas.csv_path,
            "built_at": atlas.built_at,
            "address_clusters": len(atlas.by_signal["ADDRESS_CLUSTER"]),
            "mass_incorporation_clusters": len(atlas.by_signal["MASS_INCORPORATION"]),
            "paper_shells": len(atlas.by_signal["PAPER_SHELL"]),
            "flagged_cins": len(atlas.cin_to_clusters),
        },
    )


@router.get("/{cluster_id}", response_model=ShellClusterOut)
async def get_cluster(cluster_id: str) -> ShellClusterOut:
    """Single cluster lookup — used by the dossier's evidence drill-through."""
    atlas = await asyncio.to_thread(get_atlas)
    c = atlas.clusters.get(cluster_id)
    if c is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=cluster_id)
    return _to_out(c)

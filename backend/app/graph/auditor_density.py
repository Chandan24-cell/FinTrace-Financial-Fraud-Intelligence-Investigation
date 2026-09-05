"""Auditor-network density Cypher (PRD §4.7 #2 — auditor density analysis).

PRD: "Same DIN signs >50 SME filings in same district -> flag clients." We
ship the query as a constant so callers can run it via the AsyncDriver
without writing Cypher inline.

The density check is materialised as a one-shot Cypher: group Company nodes by
`auditor_din` and `state` (state proxies for district in fixture mode), and
return any cluster whose size exceeds the threshold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from neo4j import AsyncDriver

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


AUDITOR_DENSITY_THRESHOLD = 50  # PRD §4.7 #2 verbatim


# Returns one row per (auditor_din, state) cluster above the threshold, with
# the cluster size and an array of client CINs (capped at 200 to keep the
# response payload bounded). Used by Module 7 + Module 10 in lock-step.
AUDITOR_DENSITY_CYPHER = """
MATCH (c:Company)
WHERE c.auditor_din IS NOT NULL AND c.auditor_din <> ''
WITH c.auditor_din AS auditor_din,
     c.state       AS state,
     collect(c.cin) AS client_cins
WHERE size(client_cins) > $threshold
RETURN auditor_din,
       state,
       size(client_cins) AS cluster_size,
       client_cins[0..200] AS sample_client_cins
ORDER BY cluster_size DESC
"""


@dataclass(frozen=True)
class AuditorDensityRow:
    auditor_din: str
    state: str | None
    cluster_size: int
    sample_client_cins: list[str]


async def fetch_dense_auditors(
    driver: AsyncDriver, *, threshold: int = AUDITOR_DENSITY_THRESHOLD,
) -> list[AuditorDensityRow]:
    """Run the density query and return the list of breaching clusters."""
    settings = get_settings()
    out: list[AuditorDensityRow] = []
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(AUDITOR_DENSITY_CYPHER, threshold=threshold)
        async for record in result:
            out.append(AuditorDensityRow(
                auditor_din=record["auditor_din"],
                state=record["state"],
                cluster_size=int(record["cluster_size"]),
                sample_client_cins=list(record["sample_client_cins"]),
            ))
    logger.info("fetch_dense_auditors: %d clusters above threshold=%d", len(out), threshold)
    return out

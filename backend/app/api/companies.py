"""/companies — directory of `:Company` nodes already in Neo4j.

Backs the frontend Search page's "recently loaded" panel. Returns CIN +
name + state + registration year, sorted newest-first. Optionally filters
by state (e.g. `?state=TN`) and pages via `?limit=` / `?offset=`.

This endpoint is intentionally **read-only over Neo4j** — it does NOT
trigger live MCA Public Portal scrapes. The Search page uses it to show
the analyst what's already loaded; clicking a row routes to /analyse
which does the live work.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from neo4j.exceptions import Neo4jError
from neo4j import AsyncDriver
from pydantic import BaseModel, Field
from typing import Literal

from backend.app.config import get_settings
from backend.app.deps import get_driver
from backend.app.ingest.sources import FixtureSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])


# Cardinality of the data we have on file for a company. Drives the
# Search-page colour badge so analysts know up-front whether clicking
# through will yield a full forensic analysis or just an empty plate.
#   complete    — ≥2 financial statements AND ≥1 director on file
#   partial     — some financials or directors, but not both
#   master_only — only the MCA master record (the 191k TN bulk default)
DataQuality = Literal["complete", "partial", "master_only"]


class CompanySummary(BaseModel):
    cin: str
    name: str | None = None
    state: str | None = None
    incorporation_year: int | None = None
    data_quality: DataQuality = "master_only"
    n_financials: int = 0
    n_directors: int = 0


class CompaniesPage(BaseModel):
    total: int = Field(..., description="Total `:Company` rows matching the filter")
    items: list[CompanySummary]


# Cheap OPTIONAL MATCH counts — runs per page, not per company, so 25-200
# rows fit comfortably in the query budget. Indexes on (Company.cin),
# IS_DIRECTOR_OF, HAS_FINANCIALS are assumed (Day-1 schema).
_LIST_CYPHER = """
MATCH (c:Company)
WHERE ($state IS NULL OR c.state = $state)
  AND c.incorporation_date IS NOT NULL
WITH c
ORDER BY c.incorporation_date DESC, c.cin ASC
SKIP $offset
LIMIT $limit
OPTIONAL MATCH (c)-[:HAS_FINANCIALS]->(f:FinancialStatement)
WITH c, count(DISTINCT f) AS n_financials
OPTIONAL MATCH (c)<-[:IS_DIRECTOR_OF]-(d:Director)
WITH c, n_financials, count(DISTINCT d) AS n_directors
RETURN c.cin AS cin,
       c.name AS name,
       c.state AS state,
       c.incorporation_date AS incorporation_date,
       n_financials,
       n_directors
"""


def _classify(n_financials: int, n_directors: int) -> DataQuality:
    if n_financials >= 2 and n_directors >= 1:
        return "complete"
    if n_financials >= 1 or n_directors >= 1:
        return "partial"
    return "master_only"

_COUNT_CYPHER = """
MATCH (c:Company)
WHERE $state IS NULL OR c.state = $state
RETURN count(c) AS total
"""

_fixture_source = FixtureSource()


async def _list_fixture_companies(
    state: str | None,
    limit: int,
    offset: int,
) -> CompaniesPage:
    """Serve the supported seed corpus when production Neo4j is unavailable."""
    summaries: list[CompanySummary] = []
    for cin in await _fixture_source.list_available_cins():
        bundle = await _fixture_source.fetch_bundle(cin)
        if bundle is None or (state is not None and bundle.company.state != state):
            continue
        summaries.append(CompanySummary(
            cin=bundle.company.cin,
            name=bundle.company.name,
            state=bundle.company.state,
            incorporation_year=bundle.company.incorporation_date.year,
            data_quality=_classify(len(bundle.financials), len(bundle.directors)),
            n_financials=len(bundle.financials),
            n_directors=len(bundle.directors),
        ))
    summaries.sort(key=lambda item: (item.incorporation_year or 0, item.cin), reverse=True)
    return CompaniesPage(
        total=len(summaries),
        items=summaries[offset:offset + limit],
    )


@router.get("", response_model=CompaniesPage)
async def list_companies(
    state: Annotated[str | None, Query(description="2-letter state code (e.g. TN)")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompaniesPage:
    """Paginated directory of companies known to Neo4j."""
    try:
        driver: AsyncDriver = get_driver()
    except RuntimeError:
        logger.warning("Neo4j unavailable; serving the configured fixture company directory")
        return await _list_fixture_companies(state, limit, offset)

    settings = get_settings()
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            count_res = await session.run(_COUNT_CYPHER, state=state)
            count_row = await count_res.single()
            total = int(count_row["total"]) if count_row else 0

            list_res = await session.run(
                _LIST_CYPHER, state=state, offset=offset, limit=limit,
            )
            items: list[CompanySummary] = []
            async for row in list_res:
                inc = row["incorporation_date"]
                year: int | None
                if inc is None:
                    year = None
                elif hasattr(inc, "year"):  # neo4j.time.Date
                    year = int(inc.year)
                else:
                    year = None
                n_fs = int(row.get("n_financials") or 0)
                n_dir = int(row.get("n_directors") or 0)
                items.append(CompanySummary(
                    cin=row["cin"],
                    name=row.get("name"),
                    state=row.get("state"),
                    incorporation_year=year,
                    data_quality=_classify(n_fs, n_dir),
                    n_financials=n_fs,
                    n_directors=n_dir,
                ))
    except Neo4jError as exc:
        logger.warning("Neo4j company directory query failed (%s); serving fixtures", type(exc).__name__)
        return await _list_fixture_companies(state, limit, offset)
    return CompaniesPage(total=total, items=items)

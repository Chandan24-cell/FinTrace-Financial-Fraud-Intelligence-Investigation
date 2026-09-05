"""Industry benchmark sources (PRD §4.5 + §10 Day 4).

BSE SME publishes quarterly financials for listed SME companies. We aggregate
those into IndustryBenchmark nodes (NIC code × year × metric → p25/median/p75)
that Module 5 (Peer Deviation) reads from.

Two implementations:
  BSEFixtureBenchmark — reads infra/seeds/benchmarks/*.json. Default working path.
  BSEAPIBenchmark     — real BSE SME API client (stub until aspx scraping is wired).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


SEED_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds" / "benchmarks"


class BenchmarkPoint(BaseModel):
    """One (nic_code, year, metric) → (p25, median, p75) row."""

    model_config = ConfigDict(extra="forbid")

    nic_code: int = Field(ge=1, le=99999)
    year: int = Field(ge=2000, le=2099)
    metric: str
    p25: float
    median: float
    p75: float
    benford_applicable: bool = True

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.nic_code, self.year, self.metric)


class BenchmarkSource(Protocol):
    name: str

    async def fetch_all(self) -> list[BenchmarkPoint]: ...


class BSEFixtureBenchmark:
    """Reads BSE SME benchmark snapshots from infra/seeds/benchmarks/.

    File layout: one JSON per fiscal year (e.g. bse_sme_fy2023.json) containing
    an array of BenchmarkPoint dicts.
    """

    name = "bse_fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SEED_ROOT

    async def fetch_all(self) -> list[BenchmarkPoint]:
        if not self.root.exists():
            return []
        points: list[BenchmarkPoint] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for row in raw:
                points.append(BenchmarkPoint.model_validate(row))
        return points


class BSEAPIBenchmark:
    """Real BSE SME quarterly results scraper. Day 7+ task.

    BSE SME exposes quarterly financials via JSON endpoints behind the SME quotes
    portal. Full implementation needs httpx + retry + NIC-code mapping; left as a
    stub so the interface is stable.
    """

    name = "bse_api"

    async def fetch_all(self) -> list[BenchmarkPoint]:
        raise NotImplementedError("BSEAPIBenchmark — Day 7+ task")

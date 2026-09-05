"""CERSAI charges source (PRD §3 Extension B + §10 Day 5).

CERSAI registers all charges on movable assets. We scrape its public search
endpoint and write LoanDisbursement nodes. Module 4 patterns 13-17 (PRD §4.4
Evergreening) rely on this dataset.

The Day-3 fixture bundles include inline charges via RawCharge; this Day-5
loader handles standalone CERSAI dumps (separate from MCA bundles).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from backend.app.ingest.schemas import RawCharge


SEED_ROOT = Path(__file__).resolve().parents[3] / "infra" / "seeds" / "cersai"


class CERSAISource(Protocol):
    name: str

    async def fetch_all(self) -> list[RawCharge]: ...

    async def fetch_for_cin(self, cin: str) -> list[RawCharge]: ...


class CERSAIFixtureSource:
    """Reads CERSAI charges from infra/seeds/cersai/*.json (one array per file)."""

    name = "cersai_fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SEED_ROOT

    async def fetch_all(self) -> list[RawCharge]:
        if not self.root.exists():
            return []
        charges: list[RawCharge] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for row in raw:
                charges.append(RawCharge.model_validate(row))
        return charges

    async def fetch_for_cin(self, cin: str) -> list[RawCharge]:
        return [c for c in await self.fetch_all() if c.cin == cin]

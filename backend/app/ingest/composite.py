"""CompositeSource — chains MCA21 V3 in front of the FixtureSource fallback
(PRD §10 Day 17).

Why a composite:
  - Demo path always works: when MCA21_API_KEY is the .env placeholder the
    composite resolves every CIN via the fixture seeds.
  - Production path "just works": once a real key lands in .env.local, the
    same composite tries MCA21 first and only falls through on 404 / network
    errors / validation failures.
  - Upload overlay (Day 16) lands on the *merged* bundle, not on whichever
    source supplied it — the overlay layer doesn't need to care.

Composition rules:
  1. If MCA21 raises MCA21KeyMissingError (no real key), skip straight to the
     fixture.
  2. If MCA21 returns a CompanyBundle, use it as-is. (No fixture merge — the
     production data is canonical.)
  3. If MCA21 returns None (404 / network error), fall back to the fixture.
  4. If both return None, return None — caller turns it into a 404.

The composite also fold CERSAI charges (when configured) onto the MCA21
bundle, since MCA21's /charges endpoint is incomplete vs CERSAI.
"""

from __future__ import annotations

import logging
from typing import Iterable

from backend.app.ingest.cersai import CERSAIFixtureSource
from backend.app.ingest.data_gov_in import DataGovInBulkSource
from backend.app.ingest.mca_public import (
    MCAPublicFetcherNotConfiguredError,
    MCAPublicScraper,
)
from backend.app.ingest.schemas import CompanyBundle, RawCharge
from backend.app.ingest.sources import (
    CERSAIScraper,
    CompanySource,
    FixtureSource,
    MCA21KeyMissingError,
    MCA21V3Source,
)

logger = logging.getLogger(__name__)


class CompositeCompanySource:
    """Primary-then-fallback CompanySource."""

    name = "composite"

    def __init__(
        self,
        primary: MCA21V3Source | None = None,
        secondary: MCAPublicScraper | None = None,
        fallback: CompanySource | None = None,
        cersai: CERSAIScraper | CERSAIFixtureSource | None = None,
        bulk_universe: DataGovInBulkSource | None = None,
    ) -> None:
        self.primary = primary or MCA21V3Source()
        # PRD §10 free-source plan, Phase A — MCA Public Portal scraper.
        # `MCAPublicScraper()` with no fetcher raises
        # MCAPublicFetcherNotConfiguredError on first call; composite
        # treats that as 'skip this tier' (same as MCA21KeyMissingError).
        # Production wiring injects MCAPublicPlaywrightFetcher.
        self.secondary = secondary or MCAPublicScraper()
        self.fallback = fallback or FixtureSource()
        self.cersai = cersai or CERSAIFixtureSource()
        # PRD §10 free-source plan, Phase C — data.gov.in bulk MCA dump.
        # Provides `list_available_cins()` with the full annual universe
        # when the operator has run `--refresh`. With an empty cache_dir
        # this source returns [] and the composite falls back to fixture.
        self.bulk_universe = bulk_universe or DataGovInBulkSource()

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None:
        # Tier 1 — paid MCA21 V3 (full bundle including financials).
        primary_bundle: CompanyBundle | None = None
        try:
            primary_bundle = await self.primary.fetch_bundle(cin)
        except MCA21KeyMissingError:
            logger.debug("MCA21 has no real key; trying %s", self.secondary.name)
        except Exception as exc:  # network errors, parse failures, etc.
            logger.warning("MCA21 fetch failed for %s: %s", cin, exc)

        if primary_bundle is not None:
            return await self._fold_extra_charges(primary_bundle, cin)

        # Tier 2 — free MCA Public Portal Playwright scrape (master +
        # signatories + charges). No financials.
        secondary_bundle: CompanyBundle | None = None
        try:
            secondary_bundle = await self.secondary.fetch_bundle(cin)
        except MCAPublicFetcherNotConfiguredError:
            logger.debug(
                "MCA public scraper has no fetcher; falling through to %s",
                self.fallback.name,
            )
        except Exception as exc:
            logger.warning("MCA public scrape failed for %s: %s", cin, exc)

        if secondary_bundle is not None:
            return await self._fold_extra_charges(secondary_bundle, cin)

        # Tier 3 — data.gov.in bulk CSV master-only bundle. Returns None
        # when the cache_dir is empty (operator hasn't downloaded a
        # snapshot yet) or the CIN isn't in the universe.
        bulk_bundle: CompanyBundle | None = None
        try:
            bulk_bundle = await self.bulk_universe.fetch_bundle(cin)
        except Exception as exc:
            logger.warning("data.gov.in bulk fetch failed for %s: %s", cin, exc)

        if bulk_bundle is not None:
            return await self._fold_extra_charges(bulk_bundle, cin)

        # Tier 4 — FixtureSource (offline demo backbone).
        return await self.fallback.fetch_bundle(cin)

    async def _fold_extra_charges(
        self, bundle: CompanyBundle, cin: str,
    ) -> CompanyBundle:
        """Merge any extra CERSAI charges onto a freshly-fetched bundle."""
        extras = await self._extra_charges(cin)
        if not extras:
            return bundle
        merged = list(bundle.charges)
        seen = {c.charge_id for c in merged}
        for c in extras:
            if c.charge_id not in seen:
                merged.append(c)
                seen.add(c.charge_id)
        return bundle.model_copy(update={"charges": merged})

    async def list_available_cins(self) -> list[str]:
        # Bulk universe first (data.gov.in CSV cache), fixture as backbone.
        # MCA21 has no enumeration endpoint; the public scraper has no
        # listing capability either. We union both lists so demo CINs
        # remain reachable even after a real CSV refresh is loaded.
        try:
            bulk = await self.bulk_universe.list_available_cins()
        except Exception as exc:
            logger.warning("data.gov.in bulk list failed: %s", exc)
            bulk = []
        fallback = await self.fallback.list_available_cins()
        if not bulk:
            return fallback
        return sorted(set(bulk) | set(fallback))

    async def _extra_charges(self, cin: str) -> Iterable[RawCharge]:
        if self.cersai is None:
            return []
        try:
            if isinstance(self.cersai, CERSAIFixtureSource):
                return await self.cersai.fetch_for_cin(cin)
            return await self.cersai.fetch_charges(cin)
        except NotImplementedError:
            # Scraper not configured for this run — silently skip.
            return []
        except Exception as exc:
            logger.warning("CERSAI fetch failed for %s: %s", cin, exc)
            return []

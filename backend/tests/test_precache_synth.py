"""Tests for the 200-company synthetic precache (PRD §10 Day 7).

Acceptance: '200 companies in Neo4j with DataConfidence %'.
We verify the in-memory generation step here so CI passes without Neo4j.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from backend.app.ingest.data_confidence import compute_data_confidence
from backend.app.ingest.pipeline import ingest_many
from scripts.precache_companies import SyntheticSource, generate_bundles


_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")


def test_generator_produces_requested_count() -> None:
    bundles = generate_bundles(200, seed=42)
    assert len(bundles) == 200


def test_generator_is_deterministic() -> None:
    a = generate_bundles(50, seed=123)
    b = generate_bundles(50, seed=123)
    assert [x.company.cin for x in a] == [y.company.cin for y in b]


def test_generator_produces_unique_cins() -> None:
    bundles = generate_bundles(200, seed=42)
    cins = [b.company.cin for b in bundles]
    assert len(set(cins)) == len(cins), "CIN collisions in the synthetic pool"


def test_all_generated_cins_are_valid_format() -> None:
    for b in generate_bundles(200, seed=42):
        assert _CIN_RE.match(b.company.cin), f"Bad CIN: {b.company.cin!r}"


def test_data_confidence_spans_all_five_tiers() -> None:
    """PRD §7.1: DataConfidence is one of 25/45/65/80/92. The synthetic mix
    must populate each tier so downstream tests (Day 12+) have coverage."""
    bundles = generate_bundles(200, seed=42)
    tiers_seen = {compute_data_confidence(b) for b in bundles}
    assert tiers_seen >= {25, 45, 65, 80, 92}, (
        f"Missing DataConfidence tiers: {{25, 45, 65, 80, 92}} - {tiers_seen}"
    )


def test_directors_are_two_or_three_per_company() -> None:
    for b in generate_bundles(50, seed=42):
        assert 2 <= len(b.directors) <= 3


def test_pipeline_ingests_200_bundles_via_synthetic_source() -> None:
    """End-to-end shape check: 200 bundles flow through ingest_many without errors."""
    bundles = generate_bundles(200, seed=42)
    source = SyntheticSource(bundles)
    cins = asyncio.run(source.list_available_cins())

    async def _noop_bundle(_b):
        return None

    async def _noop_dqe(_d):
        return None

    async def _noop_set(_c, _s):
        return None

    results = asyncio.run(
        ingest_many(cins, source, _noop_bundle, _noop_dqe, _noop_set)
    )
    assert len(results) == 200
    ok = sum(1 for r in results if r.ok)
    assert ok == 200, f"Expected all 200 to ingest cleanly; got {ok}"

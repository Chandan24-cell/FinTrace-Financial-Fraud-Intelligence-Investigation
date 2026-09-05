"""Tests for ml/features.py — module-score feature builder (PRD §5.2 + §10 Day 13)."""

from __future__ import annotations

import pytest

from backend.app.ingest.benchmarks import BSEFixtureBenchmark
from backend.app.ingest.nclt import NCLTFixtureSource
from backend.app.ingest.sources import FixtureSource
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource
from ml.features import (
    FEATURE_NAMES,
    MODULE_KEYS,
    FeatureContext,
    build_feature_matrix,
    build_feature_vector,
)


@pytest.fixture
async def feature_ctx() -> FeatureContext:
    return FeatureContext(
        benchmarks=await BSEFixtureBenchmark().fetch_all(),
        nclt=await NCLTFixtureSource().fetch_all(),
        wilful=await WilfulDefaulterFixtureSource().fetch_all(),
    )


def test_feature_names_match_module_keys_plus_summary_block() -> None:
    # 3 features per module + 11 summary bundle stats + 2 detector slots (D4 + D6).
    # D3 (TGN) + D5 (Mamba) deferred — they need event-stream / sequence pipelines
    # that don't exist yet (PRD §9 Phase 3). See ml/features.py DETECTOR_KEYS.
    from ml.features import DETECTOR_KEYS
    assert len(FEATURE_NAMES) == len(MODULE_KEYS) * 3 + 11 + len(DETECTOR_KEYS)


def test_feature_names_are_unique() -> None:
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


@pytest.mark.asyncio
async def test_ilfs_feature_vector_has_correct_width(feature_ctx) -> None:
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    vec = build_feature_vector(bundle, feature_ctx)
    assert vec.shape == (len(FEATURE_NAMES),)


@pytest.mark.asyncio
async def test_ilfs_fires_m9_and_m6(feature_ctx) -> None:
    """IL&FS must light up M9 (NCLT/WD) and M6 (temporal flag flip) at minimum."""
    bundle = await FixtureSource().fetch_bundle("U45201MH2005PTC155294")
    assert bundle is not None
    vec = build_feature_vector(bundle, feature_ctx)
    # Locate the m9 + m6 score positions
    m9_score_idx = FEATURE_NAMES.index("m9_score")
    m6_score_idx = FEATURE_NAMES.index("m6_score")
    assert vec[m9_score_idx] > 0.0
    assert vec[m6_score_idx] > 0.0


@pytest.mark.asyncio
async def test_build_feature_matrix_stacks_rows(feature_ctx) -> None:
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    bundles = [b for cin in cins if (b := await fixture_source.fetch_bundle(cin)) is not None]
    X, cins_in_order = build_feature_matrix(bundles, feature_ctx)
    assert X.shape == (len(bundles), len(FEATURE_NAMES))
    assert cins_in_order == [b.company.cin for b in bundles]


def test_empty_matrix_returns_correct_width() -> None:
    ctx = FeatureContext(benchmarks=[], nclt=[], wilful=[])
    X, cins = build_feature_matrix([], ctx)
    assert X.shape == (0, len(FEATURE_NAMES))
    assert cins == []

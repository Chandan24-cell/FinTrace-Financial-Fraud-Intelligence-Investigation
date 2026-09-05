"""Tier-1 module-score feature builder (PRD §5.2 + §10 Day 13).

Converts a CompanyBundle into a fixed-width numeric feature vector by running
every Tier-1 module that can score the bundle deterministically and emitting
each module's score, its max-severity ordinal, and its signal count.

This is the L2 stack input to F1a (LightGBM OOF). Keeping the builder in one
place means the OOF retrain in Day 13 and the production scorer in Day 15 see
the same feature schema — drift = label leakage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from typing import TYPE_CHECKING

from backend.app.ingest.benchmarks import BenchmarkPoint
from backend.app.ingest.nclt import RawNCLTProceeding
from backend.app.ingest.schemas import CompanyBundle
from backend.app.ingest.wilful_defaulter import RawWilfulDefaulter
from backend.app.modules import (
    m01_beneish,
    m02_cross_statement,
    m03_benford,
    m05_peer_deviation,
    m06_temporal,
    m07_auditor_nlp,
    m08_document_forensics,
    m09_nclt_defaulter,
    m11_anomaly,
)
from backend.app.modules.base import ModuleResult, Severity
from backend.app.modules.m02_cross_statement import CrossStatementInputs
from backend.app.modules.m06_temporal import TemporalInputs
from backend.app.modules.m09_nclt_defaulter import NCLTDefaulterInputs
from backend.app.modules.m11_anomaly import (
    AnomalyInputs,
    financial_feature_row,
    graph_feature_row,
)

if TYPE_CHECKING:  # pragma: no cover
    from backend.app.analytics_cache import AnalyticsCache


# Three features per module: score, max severity ordinal, signal count.
_PER_MODULE_FEATURES = ("score", "max_severity", "signal_count")

# 2026-05-21: extended from 7 to 10 modules. M4 (graph patterns) is
# deliberately excluded — its run() is async + requires a live Neo4j
# driver, which day13_oof_retrain.py doesn't have. Including M4 with a
# constant 0 at training time would create train/infer asymmetry that
# would mislead F1a. The scorer still fires M4 at /analyse time; its
# signal lives in fraud_risk_score, just not in this meta-feature slot.
MODULE_KEYS: tuple[str, ...] = (
    "m1", "m2", "m3", "m5", "m6", "m7", "m8", "m9", "m10", "m11",
)

# Detector outputs appended to the feature vector when an analytics_cache
# is supplied. All four real detectors are wired: D3 trains fresh on the
# bundle pool's event stream inside analytics_cache.build_cache; D4 fits
# LOF on graph features; D5 loads a persisted TCN; D6 trains a small
# autoencoder during cache build. D1 + D2 were deleted upstream as
# unimplemented stubs.
DETECTOR_KEYS: tuple[str, ...] = ("d3_score", "d4_score", "d5_score", "d6_score")

FEATURE_NAMES: tuple[str, ...] = tuple(
    f"{mod}_{feat}" for mod in MODULE_KEYS for feat in _PER_MODULE_FEATURES
) + (
    "fs_count",
    "has_gst_upload",
    "has_bank_upload",
    "director_count",
    "active_director_count",
    "charge_count",
    "total_borrowings",
    "revenue_latest",
    "pat_latest",
    "going_concern_any",
    "adverse_any",
) + DETECTOR_KEYS


@dataclass
class FeatureContext:
    """Shared lookups that every bundle needs."""

    benchmarks: list[BenchmarkPoint]
    nclt: list[RawNCLTProceeding]
    wilful: list[RawWilfulDefaulter]
    # Optional — provides the M10/M11/D4/D6 backing population view. When
    # None those slots are filled with zeros (still informative for the
    # majority case in tests / lightweight feature extraction).
    analytics_cache: "AnalyticsCache | None" = None


def _module_triplet(result: ModuleResult | None) -> tuple[float, float, float]:
    if result is None or result.skipped:
        return (0.0, 0.0, 0.0)
    max_sev = result.max_severity
    sev_ord = float(max_sev.numeric) if max_sev is not None else 0.0
    return (float(result.score), sev_ord, float(len(result.signals)))


def _bundle_triplets(
    bundle: CompanyBundle, ctx: FeatureContext,
) -> dict[str, tuple[float, float, float]]:
    """Run each module on the bundle. Modules that can't score abstain."""
    triplets: dict[str, tuple[float, float, float]] = {}
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    cin = bundle.company.cin

    if len(fs_sorted) >= 2:
        triplets["m1"] = _module_triplet(m01_beneish.run(fs_sorted[-1], fs_sorted[-2]))
    else:
        triplets["m1"] = (0.0, 0.0, 0.0)

    if fs_sorted:
        curr = fs_sorted[-1]
        prev = fs_sorted[-2] if len(fs_sorted) >= 2 else None
        cwip_hist = fs_sorted[-3:] if len(fs_sorted) >= 3 else None
        m2 = m02_cross_statement.run(CrossStatementInputs(
            current=curr, previous=prev, cwip_history=cwip_hist,
        ))
        triplets["m2"] = _module_triplet(m2)

        triplets["m3"] = _module_triplet(m03_benford.run(
            fs_sorted, nic_code=bundle.company.nic_code,
        ))

        m5 = m05_peer_deviation.run(
            curr, prev,
            nic_code=bundle.company.nic_code,
            benchmarks=ctx.benchmarks,
        )
        triplets["m5"] = _module_triplet(m5)

        triplets["m6"] = _module_triplet(m06_temporal.run(TemporalInputs(
            financials=fs_sorted,
            directors=list(bundle.directors),
        )))

        triplets["m7"] = _module_triplet(m07_auditor_nlp.run(fs_sorted))
        triplets["m8"] = _module_triplet(m08_document_forensics.run_for_fs_list(fs_sorted))
    else:
        for key in ("m2", "m3", "m5", "m6", "m7", "m8"):
            triplets[key] = (0.0, 0.0, 0.0)

    triplets["m9"] = _module_triplet(m09_nclt_defaulter.run(NCLTDefaulterInputs(
        cin=bundle.company.cin,
        nclt_proceedings=ctx.nclt,
        wilful_declarations=ctx.wilful,
    )))

    # M10 + M11 read from the analytics_cache (population-view artefacts).
    # Without the cache they abstain — same fallback the scorer uses.
    triplets["m10"] = (0.0, 0.0, 0.0)
    triplets["m11"] = (0.0, 0.0, 0.0)
    if ctx.analytics_cache is not None:
        cache = ctx.analytics_cache
        pre_m10 = cache.m10_results.get(cin)
        if pre_m10 is not None:
            triplets["m10"] = _module_triplet(pre_m10)
        graph_row = None
        financial_row = None
        gf = cache.graph_feature_by_cin.get(cin)
        if gf is not None:
            graph_row = graph_feature_row(gf)
        if cin in cache.financial_row_by_cin:
            financial_row = cache.financial_row_by_cin[cin]
        elif fs_sorted:
            financial_row = financial_feature_row(fs_sorted[-1])
        if graph_row is not None or financial_row is not None:
            m11_result = m11_anomaly.run(AnomalyInputs(
                cin=cin,
                year=fs_sorted[-1].year if fs_sorted else None,
                financial_row=financial_row,
                graph_row=graph_row,
                background_financials=cache.background_financials,
                background_graph=cache.background_graph,
            ))
            triplets["m11"] = _module_triplet(m11_result)

    return triplets


def build_feature_vector(bundle: CompanyBundle, ctx: FeatureContext) -> np.ndarray:
    """Return one row of the feature matrix for this bundle."""
    triplets = _bundle_triplets(bundle, ctx)
    flat: list[float] = []
    for mod in MODULE_KEYS:
        flat.extend(triplets[mod])

    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    curr = fs_sorted[-1] if fs_sorted else None
    flat.extend([
        float(len(fs_sorted)),
        1.0 if bundle.has_gst_upload else 0.0,
        1.0 if bundle.has_bank_upload else 0.0,
        float(len(bundle.directors)),
        float(sum(1 for d in bundle.directors if d.cessation_date is None)),
        float(len(bundle.charges)),
        float((curr.long_term_borrowings + curr.short_term_borrowings) if curr else 0.0),
        float(curr.revenue if curr else 0.0),
        float(curr.pat if curr else 0.0),
        1.0 if any(f.going_concern_flag for f in fs_sorted) else 0.0,
        1.0 if any(f.adverse_flag for f in fs_sorted) else 0.0,
    ])
    # Detector slots — populated when the analytics_cache is present; zero
    # otherwise. Order must match DETECTOR_KEYS so FEATURE_NAMES stays
    # consistent at train/infer.
    d3_score = 0.0
    d4_score = 0.0
    d5_score = 0.0
    d6_score = 0.0
    if ctx.analytics_cache is not None:
        # Avoid circular import at module top — the analytics_cache imports
        # ml/features via ml/l05_graph_features indirectly through D6.
        from backend.app.analytics_cache import compute_target_detector_scores
        d3_score, d4_score, d5_score, d6_score = compute_target_detector_scores(
            bundle.company.cin, bundle, ctx.analytics_cache,
        )
    flat.extend([d3_score, d4_score, d5_score, d6_score])
    return np.asarray(flat, dtype=np.float32)


def build_feature_matrix(
    bundles: list[CompanyBundle], ctx: FeatureContext,
) -> tuple[np.ndarray, list[str]]:
    """Stack feature rows for every bundle. Returns (X, cins_in_row_order)."""
    rows = [build_feature_vector(b, ctx) for b in bundles]
    cins = [b.company.cin for b in bundles]
    X = np.vstack(rows) if rows else np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
    return X, cins


# Severity legend exported so downstream consumers don't have to import the enum.
SEVERITY_NUMERIC = {s.value: s.numeric for s in Severity}

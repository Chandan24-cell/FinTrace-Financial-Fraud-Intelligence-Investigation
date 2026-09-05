"""/analyse — PRD §10 Day 15 + Day 16 extensions.

The dual-output endpoint. Looks up a CIN, applies any /upload/* overlay,
fans the bundle out across Tier-1 modules via RiskScorer, then lifts the
score using belief propagation across same-cluster CINs (PRD §5.3).

Day-16 additions:
  - Upload overlay merged onto the FixtureSource bundle (GST + bank +
    extra financials).
  - propagation_band / propagation_score fields surfaced in the payload.
  - GET /analyse/{cin}/provenance returns the FraudSignal -> TRIGGERED_BY
    chain so the React UI can render the evidence graph.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from backend.app.analytics_cache import get_or_build as get_analytics_cache
from backend.app.api.upload_store import get_upload_store
from backend.app.api.validators import CIN_PATH
from backend.app.deps import get_driver
from backend.app.ingest.benchmarks import BSEFixtureBenchmark
from backend.app.ingest.composite import CompositeCompanySource
from backend.app.ingest.mca_public import MCAPublicScraper
from backend.app.ingest.mca_public_playwright import MCAPublicPlaywrightFetcher
from backend.app.ingest.nclt import NCLTFixtureSource
from backend.app.ingest.sources import FixtureSource
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource
from backend.app.scorer import ScoringContext, score
from ml.belief_propagation import SharedAttributeEdge, propagate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analyse", tags=["analyse"])

# Day-17 switches /analyse to the CompositeCompanySource which tries MCA21
# V3 first and falls back to the FixtureSource seeds on key absence / 404.
# Belief-propagation edges still come from the FixtureSource list (it's the
# only enumerable surface — MCA21 has no "list all CINs" endpoint).
#
# Phase-A free-source wiring: the secondary tier injects
# MCAPublicPlaywrightFetcher so that on a cookie-bootstrapped machine
# (`python -m backend.app.ingest.mca_public_playwright --bootstrap`) the
# composite scrapes mca.gov.in live for any CIN. With no cookies present
# the fetcher returns None and the composite cleanly falls through to
# data.gov.in bulk → FixtureSource. See docs/INGEST_MCA_PUBLIC.md.
_company_source = CompositeCompanySource(
    secondary=MCAPublicScraper(fetcher=MCAPublicPlaywrightFetcher()),
)
_fixture_source = FixtureSource()
_benchmark_source = BSEFixtureBenchmark()
_nclt_source = NCLTFixtureSource()
_wilful_source = WilfulDefaulterFixtureSource()


async def _all_fixture_bundles() -> list:
    """Thunk passed to analytics_cache.get_or_build — only invoked on cold start.

    Iterates `list_available_cins` + `fetch_bundle` for every fixture CIN.
    Cost amortises over every subsequent /analyse request because the cache
    is module-level."""
    cins = await _fixture_source.list_available_cins()
    bundles = []
    for c in cins:
        b = await _fixture_source.fetch_bundle(c)
        if b is not None:
            bundles.append(b)
    return bundles


async def prewarm_analytics_cache() -> None:
    """Eagerly build the analytics cache at FastAPI startup.

    Without this, the first /analyse caller after a cold boot pays a
    5–10 s wait while D3 trains, D4 fits, D5 loads, D6 trains, and M10
    runs its hypergraph batch. The same population-view artefacts are
    needed regardless of which CIN gets queried first — there is no
    benefit to lazy-building them. Stream 1.3 of the production-grade
    closure plan.

    Failures here are logged but never crash the lifespan — the lazy
    build inside _build_scoring_context will still run on first request
    if startup pre-warm raised.
    """
    try:
        await get_analytics_cache(_all_fixture_bundles)
        logger.info("analytics_cache: pre-warmed at startup")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("analytics_cache prewarm failed (%s) — falling back to lazy build", exc)


async def _build_scoring_context(overlay) -> ScoringContext:
    # M4 (graph patterns) wants the live Neo4j driver. When the graph isn't
    # connected (CI without Neo4j, etc.), pass None — M4 then skips cleanly.
    try:
        driver = get_driver()
    except Exception:
        driver = None
    # M10 + M11 need the population-view cache. Lazy-built on first request
    # via the analytics_cache module; subsequent calls hit the warm copy.
    try:
        analytics = await get_analytics_cache(_all_fixture_bundles)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("analytics cache build failed (%s) — M10/M11 will skip", exc)
        analytics = None
    return ScoringContext(
        benchmarks=await _benchmark_source.fetch_all(),
        nclt=await _nclt_source.fetch_all(),
        wilful=await _wilful_source.fetch_all(),
        gst_entity=overlay.gst_entity,
        bank_credits_total=overlay.bank_credits_total,
        driver=driver,
        analytics_cache=analytics,
    )


async def _propagation_lift(cin: str, seed_score: float) -> tuple[str, float]:
    """Build SharedAttribute edges across all fixture CINs + this seed, then
    run loopy belief propagation. Returns (lifted_band, lifted_score)."""
    all_cins = await _fixture_source.list_available_cins()
    edges: list[SharedAttributeEdge] = []
    for other_cin in all_cins:
        b = await _fixture_source.fetch_bundle(other_cin)
        if b is None:
            continue
        c = b.company
        if c.registered_address:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="registered_address",
                attribute_value=c.registered_address.strip().lower(),
            ))
        if c.auditor_din:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="auditor_din",
                attribute_value=c.auditor_din,
            ))
        if c.contact_phone:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="contact_phone",
                attribute_value=c.contact_phone.strip(),
            ))
    seeds = {cin: seed_score} if cin in all_cins else {}
    if not seeds:
        return ("LOW", 0.0)
    result = propagate(seed_scores=seeds, edges=edges)
    return (result.bands.get(cin, "LOW"), float(result.lifted_scores.get(cin, 0.0)))


async def score_cin_full(cin: str):
    """Shared helper — fetch + overlay-merge + score + propagation-lift.

    Returns the RiskReport, or raises HTTPException(404) when the CIN
    isn't in any source. Reused by /analyse and /narrative so both paths
    produce identical numbers from identical evidence.
    """
    bundle = await _company_source.fetch_bundle(cin)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CIN {cin} not found in fixture source",
        )
    overlay = get_upload_store().get(cin)
    bundle = overlay.merge_into(bundle)
    ctx = await _build_scoring_context(overlay)
    report = await score(bundle, ctx)
    band, lifted = await _propagation_lift(cin, report.fraud_risk_score)
    report.propagation_band = band
    report.propagation_score = lifted
    return report


@router.get("/{cin}")
async def analyse(
    cin: CIN_PATH,
) -> dict:
    """PRD §7.1 dual-output payload for one CIN, with upload overlay folded in.

    Auth-gated (Day-19 JWT) — mirrors /report/{cin}. Bypass on this endpoint
    was filed as F4 in docs/LOCAL_TEST_REPORT.md.
    """
    report = await score_cin_full(cin)
    return report.to_dict()



@router.get("/{cin}/provenance/export")
async def provenance_export(
    cin: CIN_PATH,
) -> JSONResponse:
    """Download the evidence graph as JSON for audit handoff."""
    payload = await provenance(cin)
    return JSONResponse(
        content=jsonable_encoder(payload),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=sentinel-g-{cin}-provenance.json"},
    )
@router.get("/{cin}/provenance")
async def provenance(
    cin: CIN_PATH,
) -> dict:
    """Return the evidence chain in graph form: nodes + TRIGGERED_BY edges.

    Output shape:
      {
        "cin": "U…",
        "signal_count": int,
        "signals":   [{signal_id, signal_type, severity, module_name,
                       score_contribution, evidence_string}, …],
        "triggered_by": [{signal_id, label, ref}, …]
      }

    Stream 3.4 — live-graph traversal first (`read_provenance_for_cin`);
    falls through to a full scorer-driven rebuild when:
      * no Neo4j driver is configured (tests, offline scoring),
      * Cypher fails for any reason,
      * the graph has no FraudSignal nodes for this CIN yet (cold start
        before /analyse has been called once).
    Both paths emit an identical shape so the React GraphExplorer can
    consume either without branching.
    """
    # Fast path: pull from the graph.
    try:
        driver = get_driver()
    except Exception:
        driver = None

    if driver is not None:
        from backend.app.graph.writes import read_provenance_for_cin
        graph_payload = await read_provenance_for_cin(driver, cin)
        if graph_payload is not None:
            return graph_payload

    # Fallback: rebuild from the scorer. Same shape as `read_provenance_for_cin`.
    bundle = await _company_source.fetch_bundle(cin)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CIN {cin} not found in fixture source",
        )
    overlay = get_upload_store().get(cin)
    bundle = overlay.merge_into(bundle)
    ctx = await _build_scoring_context(overlay)
    report = await score(bundle, ctx)

    signals = []
    triggered_by = []
    for s in report.evidence_chain:
        signals.append({
            "signal_id": s.signal_id,
            "signal_type": s.signal_type,
            "severity": s.severity.value,
            "module_name": s.module_name,
            "score_contribution": s.score_contribution,
            "evidence_string": s.evidence_string,
        })
        for ref in s.triggered_by:
            triggered_by.append({
                "signal_id": s.signal_id,
                "label": ref.get("label"),
                "ref": {k: v for k, v in ref.items() if k != "label"},
            })

    return {
        "cin": cin,
        "signal_count": len(signals),
        "signals": signals,
        "triggered_by": triggered_by,
    }

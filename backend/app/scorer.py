"""RiskScorer — orchestrates Tier-1 modules + ML detectors (PRD §7).

Async fan-out across M1-M11 via asyncio.gather, applies PRD §7.3 override
rules, computes the ensemble-disagreement flag, and emits the dual-output
payload PRD §7.1 mandates.

Output schema (verbatim):

    {
      "cin": "U…",
      "fraud_risk_score": 0–100,                  # Tier-1 weighted aggregate
      "risk_band": "LOW|MEDIUM|HIGH|CRITICAL",
      "p_fraud_calibrated": 0.0–1.0 | null,       # populated when F1b artefact loaded
      "p_fraud_interval": [low, high] | null,      # populated when F1c artefact loaded
      "data_confidence": 0–100,                   # DataCompletenessScore
      "ensemble_disagreement_flag": bool,         # True when any 2 module scores diverge > 30pts
      "evidence_chain": [FraudSignal…],           # with TRIGGERED_BY provenance
      "module_breakdown": {module_name: score, …},# per-module Tier-1 contributions
      "override_applied": bool,                   # PRD §7.3 NCLT/WD override
      "skipped_modules": [{"module": …, "reason": …}, …],
    }

PRD §7 invariants:
  - Never emit fraud_risk_score without data_confidence (and vice-versa).
  - Any CRITICAL flag forces fraud_risk_score >= 60.
  - NCLT/WD match forces fraud_risk_score >= 75 via apply_override().
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.app.ingest.benchmarks import BenchmarkPoint
from backend.app.ingest.data_confidence import compute_data_confidence
from backend.app.ingest.gst import RawGSTEntity
from backend.app.ingest.nclt import RawNCLTProceeding
from backend.app.ingest.schemas import CompanyBundle
from backend.app.ingest.wilful_defaulter import RawWilfulDefaulter
from backend.app.modules import (
    m01_beneish,
    m02_cross_statement,
    m03_benford,
    m04_graph_patterns,
    m05_peer_deviation,
    m06_temporal,
    m07_auditor_nlp,
    m08_document_forensics,
    m09_nclt_defaulter,
    m11_anomaly,
)
from backend.app.modules.m11_anomaly import (
    AnomalyInputs,
    financial_feature_row,
    graph_feature_row,
)
from backend.app.modules.base import FraudSignal, ModuleResult, Severity, clamp_score
from backend.app.modules.m0_master_shell_atlas import (
    ShellCluster,
    ShellSignalType,
    get_atlas,
)
from backend.app.modules.m02_cross_statement import CrossStatementInputs
from backend.app.modules.m06_temporal import TemporalInputs
from backend.app.modules.m09_nclt_defaulter import (
    NCLT_WD_FLOOR_SCORE,
    NCLTDefaulterInputs,
    apply_override,
)
from ml.belief_propagation import assign_band

logger = logging.getLogger(__name__)


# PRD §7.3 override constants
CRITICAL_FLAG_FLOOR_SCORE = 60.0

# PRD §7.4 ensemble-disagreement threshold (any two modules differ by > 30 pts)
ENSEMBLE_DISAGREEMENT_DELTA = 30.0

# Tier-1 module weights (PRD §7.2) — modules summed and clamped to [0, 100].
# M3/M4 wired 2026-05-21 to close the orphaned-modules gap (LOCAL_TEST_REPORT
# finding §3.2). M10 needs cross-company batch infra and M11 needs feature
# matrices; both stay declared here so the aggregate scaler reserves headroom
# for them when they're wired. The aggregate formula in _aggregate_score
# re-scales by sum_W / total_weight_running, so partial coverage doesn't
# penalise — but reserving the weight prevents fixture scores from shifting
# when M10/M11 light up later.
_MODULE_WEIGHTS: dict[str, float] = {
    "m01_beneish":          0.15,
    "m02_cross_statement":  0.20,
    "m03_benford":          0.05,
    "m04_graph_patterns":   0.10,
    "m05_peer_deviation":   0.10,
    "m06_temporal":         0.10,
    "m07_auditor_nlp":      0.10,
    "m08_document_forensics": 0.05,
    "m09_nclt_defaulter":   0.20,
    "m10_hypergraph_shell": 0.05,   # reserved — wired in cross-company batch pipeline (TODO)
    "m11_anomaly":          0.10,   # reserved — wired once L0.5 feature pipeline produces matrices (TODO)
}


@dataclass
class ScoringContext:
    """All shared lookups the scorer needs to run."""

    benchmarks: list[BenchmarkPoint] = field(default_factory=list)
    nclt: list[RawNCLTProceeding] = field(default_factory=list)
    wilful: list[RawWilfulDefaulter] = field(default_factory=list)
    # Day-16 upload overlay — populated by the /analyse handler so M2 sees
    # the user-supplied GST / bank-statement evidence on a per-call basis.
    gst_entity: RawGSTEntity | None = None
    bank_credits_total: float | None = None
    # Neo4j driver — required by M4 (graph patterns) Cypher queries. None when
    # the graph isn't reachable; M4 then skips with a clear reason instead of
    # raising. Tests that don't need M4 can leave this unset.
    driver: Any | None = None
    # Precomputed analytics cache — population-view artefacts M10 (hypergraph
    # shell, batch-mode) and M11 (anomaly, background-fit) both need to fire.
    # See backend/app/analytics_cache.py. None means M10/M11 skip cleanly.
    analytics_cache: Any | None = None


@dataclass
class RiskReport:
    """Dual-output payload (PRD §7.1 verbatim shape, plus diagnostic extras)."""

    cin: str
    fraud_risk_score: float
    risk_band: str
    data_confidence: int
    ensemble_disagreement_flag: bool
    evidence_chain: list[FraudSignal]
    module_breakdown: dict[str, float]
    override_applied: bool
    skipped_modules: list[dict[str, str]]
    # Company master fields — surfaced so the frontend never displays a
    # raw CIN in place of a human-readable company identity.
    company_name: str = ""
    company_state: str = ""
    company_nic_code: int = 0
    company_incorporation_date: str = ""
    # Stream 5.1 — audit trail: which FraudSignal.signal_id values
    # tripped the NCLT/WD override floor. Empty when override_applied
    # is False. Lets investigators answer "what specifically forced
    # this CIN to CRITICAL?" without rewalking the evidence chain.
    override_matched_signal_ids: list[str] = field(default_factory=list)
    p_fraud_calibrated: float | None = None
    p_fraud_interval: tuple[float, float] | None = None
    # Day-16: belief-propagation lift from neighbouring CINs in the same
    # SharedAttribute cluster. Defaults to LOW for stand-alone runs.
    propagation_band: str = "LOW"
    propagation_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cin": self.cin,
            "company_name": self.company_name,
            "company_state": self.company_state,
            "company_nic_code": self.company_nic_code,
            "company_incorporation_date": self.company_incorporation_date,
            "fraud_risk_score": round(self.fraud_risk_score, 2),
            "risk_band": self.risk_band,
            "p_fraud_calibrated": (
                round(self.p_fraud_calibrated, 4)
                if self.p_fraud_calibrated is not None else None
            ),
            "p_fraud_interval": (
                [round(self.p_fraud_interval[0], 4),
                 round(self.p_fraud_interval[1], 4)]
                if self.p_fraud_interval is not None else None
            ),
            "data_confidence": self.data_confidence,
            "ensemble_disagreement_flag": self.ensemble_disagreement_flag,
            "evidence_chain": [
                {
                    "signal_type": s.signal_type,
                    "severity": s.severity.value,
                    "score_contribution": s.score_contribution,
                    "evidence_string": s.evidence_string,
                    "module_name": s.module_name,
                    "triggered_by": s.triggered_by,
                    "signal_id": s.signal_id,
                }
                for s in self.evidence_chain
            ],
            "module_breakdown": {k: round(v, 2) for k, v in self.module_breakdown.items()},
            "override_applied": self.override_applied,
            "override_matched_signal_ids": list(self.override_matched_signal_ids),
            "skipped_modules": self.skipped_modules,
            "propagation_band": self.propagation_band,
            "propagation_score": round(self.propagation_score, 2),
        }


# --- Per-module async runners. Each one is sync inside but we wrap in
# `asyncio.to_thread` so the fan-out actually executes in parallel. ---------
async def _run_m1(bundle: CompanyBundle) -> ModuleResult:
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    if len(fs_sorted) < 2:
        return ModuleResult.skipped_for(
            "m01_beneish", bundle.company.cin,
            fs_sorted[-1].year if fs_sorted else None,
            "Need 2+ consecutive FS years",
        )
    return await asyncio.to_thread(m01_beneish.run, fs_sorted[-1], fs_sorted[-2])


async def _run_m2(bundle: CompanyBundle, ctx: ScoringContext) -> ModuleResult:
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    if not fs_sorted:
        return ModuleResult.skipped_for(
            "m02_cross_statement", bundle.company.cin, None, "No financials",
        )
    curr = fs_sorted[-1]
    prev = fs_sorted[-2] if len(fs_sorted) >= 2 else None
    inputs = CrossStatementInputs(
        current=curr,
        previous=prev,
        cwip_history=fs_sorted[-3:] if len(fs_sorted) >= 3 else None,
        cersai_charges=list(bundle.charges),
        gst_entity=ctx.gst_entity,
        bank_credits_total=(
            ctx.bank_credits_total
            if ctx.bank_credits_total is not None
            else (curr.revenue * 1.04 if bundle.has_bank_upload else None)
        ),
    )
    return await asyncio.to_thread(m02_cross_statement.run, inputs)


async def _run_m3(bundle: CompanyBundle) -> ModuleResult:
    fs_list = sorted(bundle.financials, key=lambda f: f.year)
    if not fs_list:
        return ModuleResult.skipped_for(
            "m03_benford", bundle.company.cin, None, "No financials",
        )
    return await asyncio.to_thread(
        m03_benford.run, fs_list, nic_code=bundle.company.nic_code,
    )


async def _run_m4(bundle: CompanyBundle, ctx: ScoringContext) -> ModuleResult:
    """All 17 PRD §4.4 graph patterns. Requires a live Neo4j driver — when
    ctx.driver is None (tests, offline scoring) we skip cleanly."""
    if ctx.driver is None:
        return ModuleResult.skipped_for(
            "m04_graph_patterns", bundle.company.cin, None,
            "Neo4j driver unavailable — graph patterns require live graph",
        )
    try:
        return await m04_graph_patterns.run(ctx.driver, bundle.company.cin)
    except Exception as exc:
        logger.warning("m04_graph_patterns failed for %s: %s", bundle.company.cin, exc)
        return ModuleResult.skipped_for(
            "m04_graph_patterns", bundle.company.cin, None,
            f"Graph pattern execution error: {exc!r}",
        )


async def _run_m5(bundle: CompanyBundle, ctx: ScoringContext) -> ModuleResult:
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    if not fs_sorted:
        return ModuleResult.skipped_for(
            "m05_peer_deviation", bundle.company.cin, None, "No financials",
        )
    curr = fs_sorted[-1]
    prev = fs_sorted[-2] if len(fs_sorted) >= 2 else None
    return await asyncio.to_thread(
        m05_peer_deviation.run, curr, prev,
        nic_code=bundle.company.nic_code, benchmarks=ctx.benchmarks,
    )


async def _run_m6(bundle: CompanyBundle) -> ModuleResult:
    return await asyncio.to_thread(
        m06_temporal.run,
        TemporalInputs(
            financials=list(bundle.financials),
            directors=list(bundle.directors),
        ),
    )


async def _run_m7(bundle: CompanyBundle) -> ModuleResult:
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    if not fs_sorted:
        return ModuleResult.skipped_for(
            "m07_auditor_nlp", bundle.company.cin, None, "No financials",
        )
    return await asyncio.to_thread(m07_auditor_nlp.run, fs_sorted)


async def _run_m8(bundle: CompanyBundle) -> ModuleResult:
    fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
    if not fs_sorted:
        return ModuleResult.skipped_for(
            "m08_document_forensics", bundle.company.cin, None, "No financials",
        )
    return await asyncio.to_thread(m08_document_forensics.run_for_fs_list, fs_sorted)


async def _run_m9(bundle: CompanyBundle, ctx: ScoringContext) -> ModuleResult:
    return await asyncio.to_thread(
        m09_nclt_defaulter.run,
        NCLTDefaulterInputs(
            cin=bundle.company.cin,
            nclt_proceedings=ctx.nclt,
            wilful_declarations=ctx.wilful,
        ),
    )


async def _run_m10(bundle: CompanyBundle, ctx: ScoringContext) -> ModuleResult:
    """Read M10's batch result for this CIN out of the analytics cache.

    M10 fires on cross-company shared-attribute clusters (PRD §4.10) — running
    it per-request with a single bundle would never find anything. The cache
    runs the batch over the fixture pool once at startup; per-request we just
    look up the precomputed result.
    """
    cin = bundle.company.cin
    if ctx.analytics_cache is None:
        return ModuleResult.skipped_for(
            "m10_hypergraph_shell", cin, None,
            "Analytics cache not built — M10 requires cross-company batch precompute",
        )
    pre = ctx.analytics_cache.m10_results.get(cin)
    if pre is not None:
        return pre
    # CIN not in the fixture pool, or no shell-cluster signal — return a
    # well-formed empty result so the aggregate sees a 0 instead of a skip.
    return ModuleResult(
        module_name="m10_hypergraph_shell",
        cin=cin, year=None, score=0.0, signals=[],
    )


async def _run_m11(bundle: CompanyBundle, ctx: ScoringContext) -> ModuleResult:
    """LOF + IsolationForest on this CIN's 7-D graph row + 20-D financial row.

    Backgrounds are precomputed once from the fixture pool. The target's
    feature rows come from the cache when the CIN is in the pool; CINs not
    in the pool (uploads / arbitrary lookups) skip cleanly with a reason.
    """
    cin = bundle.company.cin
    if ctx.analytics_cache is None:
        return ModuleResult.skipped_for(
            "m11_anomaly", cin, None,
            "Analytics cache not built — M11 requires precomputed background",
        )

    cache = ctx.analytics_cache
    graph_row = None
    financial_row = None
    gf = cache.graph_feature_by_cin.get(cin)
    if gf is not None:
        graph_row = graph_feature_row(gf)
    fr = cache.financial_row_by_cin.get(cin)
    if fr is not None:
        financial_row = fr
    elif bundle.financials:
        # Target wasn't in the fixture pool but we have its FS — compute fresh.
        fs_sorted = sorted(bundle.financials, key=lambda f: f.year)
        financial_row = financial_feature_row(fs_sorted[-1])

    if graph_row is None and financial_row is None:
        return ModuleResult.skipped_for(
            "m11_anomaly", cin, None,
            "No feature rows available for CIN — neither graph nor financial",
        )

    return await asyncio.to_thread(
        m11_anomaly.run,
        AnomalyInputs(
            cin=cin,
            year=sorted(bundle.financials, key=lambda f: f.year)[-1].year
                 if bundle.financials else None,
            financial_row=financial_row,
            graph_row=graph_row,
            background_financials=cache.background_financials,
            background_graph=cache.background_graph,
        ),
    )


# --- Public orchestration --------------------------------------------------
def _max_module_severity(results: list[ModuleResult]) -> Severity | None:
    severities = [r.max_severity for r in results if r.max_severity is not None]
    if not severities:
        return None
    return max(severities, key=lambda s: s.numeric)


def _ensemble_disagreement(scores: list[float]) -> bool:
    """PRD §7.4: True when any two module scores differ by > 30 pts."""
    non_zero = [s for s in scores if s > 0.0]
    if len(non_zero) < 2:
        return False
    return max(non_zero) - min(non_zero) > ENSEMBLE_DISAGREEMENT_DELTA


def _aggregate_score(results: list[ModuleResult]) -> float:
    """Weighted Tier-1 aggregate per PRD §7.2."""
    total = 0.0
    total_weight = 0.0
    for r in results:
        if r.skipped:
            continue
        weight = _MODULE_WEIGHTS.get(r.module_name, 0.0)
        total += r.score * weight
        total_weight += weight
    if total_weight == 0.0:
        return 0.0
    # Re-scale so partial-coverage runs aren't penalised — a single module with
    # weight 0.2 contributing 100 still yields 100 if it's the only one to run.
    return clamp_score(total * (sum(_MODULE_WEIGHTS.values()) / total_weight))


async def score(bundle: CompanyBundle, ctx: ScoringContext) -> RiskReport:
    """Fan-out across Tier-1 modules, apply overrides, build the dual-output.

    The meta-learner (F1a/F1b/F1c) is invoked alongside the Tier-1 fan-out so
    `p_fraud_calibrated` + `p_fraud_interval` reflect the same bundle that
    fed the rule modules. When artefacts aren't present those fields stay
    null (PRD §7.1 explicitly allows that fallback).
    """
    from backend.app.ml_inference import compute_calibrated_probability

    module_task = asyncio.gather(
        _run_m1(bundle),
        _run_m2(bundle, ctx),
        _run_m3(bundle),
        _run_m4(bundle, ctx),
        _run_m5(bundle, ctx),
        _run_m6(bundle),
        _run_m7(bundle),
        _run_m8(bundle),
        _run_m9(bundle, ctx),
        _run_m10(bundle, ctx),
        _run_m11(bundle, ctx),
    )
    meta_task = compute_calibrated_probability(
        bundle,
        benchmarks=ctx.benchmarks,
        nclt=ctx.nclt,
        wilful=ctx.wilful,
        analytics_cache=ctx.analytics_cache,
    )
    results, meta_pred = await asyncio.gather(module_task, meta_task)

    # M0 master-data shell atlas hook — for CINs in the 250k TN bulk that
    # have no relationships / financials in Neo4j (M1-M11 all skip), the
    # atlas surfaces shell-like patterns derivable from the master record
    # alone: address clusters, mass-incorporation events, paper-shell
    # capital ratios. We synthesize a ModuleResult so the existing
    # aggregator + persistence + breakdown handlers process it uniformly
    # with M1-M11. Demo CINs (IL&FS / DHFL / Amtek) usually aren't in the
    # TN bulk, so this is purely additive — no risk of double-counting.
    m0_result = _m0_atlas_result(bundle.company.cin)
    if m0_result is not None:
        results = list(results) + [m0_result]

    evidence: list[FraudSignal] = []
    breakdown: dict[str, float] = {}
    skipped: list[dict[str, str]] = []
    for r in results:
        breakdown[r.module_name] = r.score
        if r.skipped:
            skipped.append({"module": r.module_name, "reason": r.skip_reason})
            continue
        evidence.extend(r.signals)

    base_score = _aggregate_score(results)

    # PRD §7.3: any CRITICAL signal forces score >= 60
    max_sev = _max_module_severity(results)
    if max_sev is Severity.CRITICAL:
        base_score = max(base_score, CRITICAL_FLAG_FLOOR_SCORE)

    # PRD §7.3: NCLT/WD override -> >= 75
    # Stream 5.1: capture matched_signal_ids so the audit trail can
    # answer "*which* M9 signal triggered the floor" without the
    # investigator re-deriving it from the evidence chain.
    override_result = apply_override(base_score, evidence)
    final_score = clamp_score(override_result.final_score)
    override_applied = override_result.applied
    override_matched_signal_ids = list(override_result.matched_signal_ids)

    # Stream 3.3 — persist FraudSignal nodes + TRIGGERED_BY edges to Neo4j
    # so the /provenance endpoint can traverse the live graph (PRD §6
    # graph-native evidence) instead of rescoring on every read. Skip
    # silently when no driver is configured (CI, unit tests, offline
    # scoring) — the in-memory evidence_chain still flows through.
    if ctx.driver is not None and evidence:
        from backend.app.graph.writes import persist_fraud_signals
        try:
            written = await persist_fraud_signals(
                ctx.driver, bundle.company.cin, evidence,
            )
            logger.info(
                "scorer: persisted %d/%d FraudSignal(s) for %s",
                written, len(evidence), bundle.company.cin,
            )
        except Exception as exc:  # noqa: BLE001 — never crash the request
            logger.warning(
                "scorer: FraudSignal persistence failed for %s (%s) — "
                "provenance will use in-memory chain", bundle.company.cin, exc,
            )

    return RiskReport(
        cin=bundle.company.cin,
        company_name=bundle.company.name,
        company_state=bundle.company.state,
        company_nic_code=bundle.company.nic_code,
        company_incorporation_date=bundle.company.incorporation_date.isoformat(),
        fraud_risk_score=final_score,
        risk_band=assign_band(final_score),
        data_confidence=compute_data_confidence(bundle),
        ensemble_disagreement_flag=_ensemble_disagreement(list(breakdown.values())),
        evidence_chain=evidence,
        module_breakdown=breakdown,
        override_applied=override_applied,
        override_matched_signal_ids=override_matched_signal_ids,
        skipped_modules=skipped,
        p_fraud_calibrated=meta_pred.p_fraud,
        p_fraud_interval=meta_pred.interval,
    )


# Severity + score mapping for M0 atlas hits — kept conservative so
# atlas signals never single-handedly push a TN CIN to CRITICAL. The
# point is to give analyses on master-only CINs *some* evidence to
# work with, not to fabricate high-confidence fraud findings from
# clustering alone.
_M0_SEVERITY_THRESHOLDS: dict[ShellSignalType, list[tuple[int, Severity, float]]] = {
    # (min_size, severity, score_contribution) — first match wins, largest first.
    "ADDRESS_CLUSTER": [
        (50, Severity.HIGH, 40.0),
        (15, Severity.MEDIUM, 25.0),
        (0,  Severity.LOW, 10.0),
    ],
    "MASS_INCORPORATION": [
        (100, Severity.HIGH, 35.0),
        (25,  Severity.MEDIUM, 20.0),
        (0,   Severity.LOW, 8.0),
    ],
    "PAPER_SHELL": [
        (0, Severity.MEDIUM, 18.0),
    ],
}


def _grade_m0_signal(cluster: ShellCluster) -> tuple[Severity, float]:
    """Pick severity + score_contribution for one M0 cluster based on its
    member count. ADDRESS_CLUSTER scales hardest because co-location at
    N≥50 is the most damning master-data tell."""
    for min_size, sev, contrib in _M0_SEVERITY_THRESHOLDS[cluster.signal_type]:
        if cluster.size >= min_size:
            return sev, contrib
    return Severity.LOW, 5.0


def _m0_atlas_result(cin: str) -> ModuleResult | None:
    """Build a synthetic ModuleResult from any M0 atlas clusters this CIN
    is a member of. Returns None if the CIN is not in the index (the
    common case for demo CINs that aren't in the TN bulk). The atlas
    is read-only here — first-call build happens in /shells or
    eagerly via the startup wire."""
    atlas = get_atlas()
    cluster_ids = atlas.cin_to_clusters.get(cin)
    if not cluster_ids:
        return None
    signals: list[FraudSignal] = []
    for cid in cluster_ids:
        cluster = atlas.clusters.get(cid)
        if cluster is None:
            continue
        sev, contrib = _grade_m0_signal(cluster)
        signals.append(
            FraudSignal(
                signal_type=f"MASTER_DATA_{cluster.signal_type}",
                severity=sev,
                score_contribution=contrib,
                evidence_string=cluster.evidence_string,
                module_name="m0_master_shell_atlas",
                triggered_by=[{
                    "label": "ShellCluster",
                    "cluster_id": cluster.cluster_id,
                    "size": cluster.size,
                    "anchor": cluster.anchor_value,
                    "source": cluster.source,
                }],
            )
        )
    if not signals:
        return None
    score = clamp_score(sum(s.score_contribution for s in signals))
    return ModuleResult(
        module_name="m0_master_shell_atlas",
        cin=cin,
        year=None,
        score=score,
        signals=signals,
    )


# Expose constants the test suite & API contract reference.
__all__ = [
    "CRITICAL_FLAG_FLOOR_SCORE",
    "ENSEMBLE_DISAGREEMENT_DELTA",
    "NCLT_WD_FLOOR_SCORE",
    "RiskReport",
    "ScoringContext",
    "score",
]

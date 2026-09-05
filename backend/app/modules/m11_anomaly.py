"""Module 11 — Anomaly Detection (PRD §4.11).

Two unsupervised detectors that catch novel fraud patterns the trained
ensemble has never seen:

  - IsolationForest on a 20-dim financial-ratio vector. Decision-function
    score < -0.10 produces a NOVEL_PATTERN_FINANCIAL FraudSignal.
  - LocalOutlierFactor on a 7-dim graph-feature vector
    (PageRank, betweenness, clustering coeff, director count, counterparty
    age, degree, ego-density). negative_outlier_factor_ < -1.50 produces a
    NOVEL_PATTERN_GRAPH FraudSignal.

The module needs a *background* sample of healthy peers to fit the detectors
against. In production those come from the cached fixture+synthetic pool;
the function takes the background as an explicit argument so callers control
selection and reproducibility.

Day-15 acceptance (PRD §10): Module 11 is one of the Tier-1 modules the
RiskScorer fans out across, and contributes to ensemble_disagreement_flag
when its score diverges from the supervised ensemble.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor

from backend.app.ingest.schemas import RawFinancialStatement
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
)
from ml.l05_graph_features import GraphFeatures

logger = logging.getLogger(__name__)

MODULE_NAME = "m11_anomaly"

# PRD §4.11 verbatim
ISOFOREST_SCORE_THRESHOLD = -0.10
LOF_SCORE_THRESHOLD = -1.50
LOF_N_NEIGHBORS = 20

# 20 financial-ratio features computed below. Each row of the fit/predict
# matrix has the same column order.
FINANCIAL_FEATURE_NAMES: tuple[str, ...] = (
    "revenue", "cost_of_materials", "employee_cost", "finance_costs",
    "depreciation", "other_expenses", "pbt", "pat", "total_assets",
    "total_liabilities", "fixed_assets", "cwip", "current_assets",
    "cash_and_equivalents", "receivables", "trade_payables",
    "long_term_borrowings", "short_term_borrowings", "notes_word_count",
    "emphasis_count",
)


def financial_feature_row(fs: RawFinancialStatement) -> np.ndarray:
    """Map a FinancialStatement into the fixed-width 20-feature vector."""
    return np.asarray([
        fs.revenue,
        fs.cost_of_materials,
        fs.employee_cost,
        fs.finance_costs,
        fs.depreciation,
        fs.other_expenses,
        fs.pbt,
        fs.pat,
        fs.total_assets,
        fs.total_liabilities,
        fs.fixed_assets,
        fs.cwip,
        fs.current_assets,
        fs.cash_and_equivalents,
        fs.receivables,
        fs.trade_payables,
        fs.long_term_borrowings,
        fs.short_term_borrowings,
        float(fs.notes_word_count),
        float(fs.emphasis_count),
    ], dtype=np.float64)


def graph_feature_row(features: GraphFeatures) -> np.ndarray:
    """Map L0.5 GraphFeatures into the fixed-width 7-feature vector.

    PRD §4.11 LOF spec: PageRank, betweenness, clustering coeff, director
    count, counterparty age, degree, ego density. Director count and degree
    stay as integers but are cast to float for the LOF matrix.
    """
    return np.asarray([
        features.pagerank,
        features.betweenness,
        features.clustering_coeff,
        float(features.director_count),
        features.counterparty_median_age_days,
        float(features.degree),
        features.ego_density,
    ], dtype=np.float64)


@dataclass(frozen=True)
class AnomalyInputs:
    """One company's anomaly-detection inputs + the background fits against."""

    cin: str
    year: int | None
    financial_row: np.ndarray | None
    graph_row: np.ndarray | None
    background_financials: np.ndarray  # shape (n_bg, 20)
    background_graph: np.ndarray       # shape (n_bg, 7)


def _financial_signal(score: float, cin: str, year: int | None) -> FraudSignal:
    return FraudSignal(
        signal_type="ANOMALY_NOVEL_PATTERN_FINANCIAL",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"IsolationForest decision score {score:.3f} is below the "
            f"{ISOFOREST_SCORE_THRESHOLD:.2f} novelty threshold on the 20-dim "
            f"financial-ratio vector. Pattern is unfamiliar to the trained "
            f"ensemble — PRD §4.11 NOVEL_PATTERN."
        ),
        module_name=MODULE_NAME,
        triggered_by=[{"label": "FinancialStatement", "cin": cin, "year": year}],
    )


def _graph_signal(score: float, cin: str) -> FraudSignal:
    return FraudSignal(
        signal_type="ANOMALY_NOVEL_PATTERN_GRAPH",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"LocalOutlierFactor score {score:.3f} is below the "
            f"{LOF_SCORE_THRESHOLD:.2f} novelty threshold on the 7-dim L0.5 "
            f"graph-feature vector. Network neighbourhood resembles known "
            f"shell-cluster topology."
        ),
        module_name=MODULE_NAME,
        triggered_by=[{"label": "Company", "cin": cin}],
    )


def _score_financial(
    target: np.ndarray, background: np.ndarray, *, seed: int = 42,
) -> float:
    """Decision-function score in IsolationForest's convention: higher = more
    inlier, lower = more outlier. Threshold is -0.10."""
    if background.shape[0] < 5 or background.shape[1] != target.shape[0]:
        return 0.0
    iso = IsolationForest(
        n_estimators=120,
        contamination="auto",
        random_state=seed,
    )
    iso.fit(background)
    return float(iso.decision_function(target.reshape(1, -1))[0])


def _score_graph(
    target: np.ndarray, background: np.ndarray, *, n_neighbors: int = LOF_N_NEIGHBORS,
) -> float:
    """negative_outlier_factor_-style score: more negative = more outlier.
    Returns 0.0 (no firing) when background is too small for the LOF k."""
    if background.shape[0] < n_neighbors + 1 or background.shape[1] != target.shape[0]:
        return 0.0
    combined = np.vstack([background, target.reshape(1, -1)])
    lof = LocalOutlierFactor(
        n_neighbors=min(n_neighbors, combined.shape[0] - 1),
        novelty=False,
    )
    lof.fit_predict(combined)
    # The last row is the target — read its outlier factor.
    return float(lof.negative_outlier_factor_[-1])


def run(inputs: AnomalyInputs) -> ModuleResult:
    """Score the target company on both detectors. Either may abstain when
    its background sample is too thin to fit reliably."""
    signals: list[FraudSignal] = []

    iso_score = 0.0
    if inputs.financial_row is not None:
        iso_score = _score_financial(inputs.financial_row, inputs.background_financials)
        if iso_score < ISOFOREST_SCORE_THRESHOLD:
            signals.append(_financial_signal(iso_score, inputs.cin, inputs.year))

    lof_score = 0.0
    if inputs.graph_row is not None:
        lof_score = _score_graph(inputs.graph_row, inputs.background_graph)
        if lof_score < LOF_SCORE_THRESHOLD:
            signals.append(_graph_signal(lof_score, inputs.cin))

    return ModuleResult(
        module_name=MODULE_NAME,
        cin=inputs.cin,
        year=inputs.year,
        score=clamp_score(sum(s.score_contribution for s in signals)),
        signals=signals,
    )

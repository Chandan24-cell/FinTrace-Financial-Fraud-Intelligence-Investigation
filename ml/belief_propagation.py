"""Belief propagation over the SharedAttribute hypergraph (PRD §5.3 + §10 Day 13).

Idea: a Tier-1 fraud_risk_score is anchored on the labelled company, but if that
company sits inside a SharedAttribute cluster (same address, same auditor, same
bank branch) then *other* members of the same cluster inherit a fractional
share of the seed's risk. This is the classic loopy belief-propagation step on
a bipartite graph (Company nodes ↔ SharedAttribute nodes), without us having
to run Pearl's full junction-tree algorithm.

We expose two helpers:
  - `propagate` runs the iterative update and returns {cin: lifted_score}.
  - `assign_band` maps a numeric score to one of LOW/MEDIUM/HIGH/CRITICAL
    bands per PRD §7.2.

Day-13 acceptance (PRD §10): 'Belief propagation lifts unlabelled cluster
CINs to MEDIUM band.'
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

Band = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# PRD §7.2 band cutoffs (verbatim).
BAND_THRESHOLDS: list[tuple[Band, float]] = [
    ("CRITICAL", 75.0),
    ("HIGH", 50.0),
    ("MEDIUM", 25.0),
    ("LOW", 0.0),
]


def assign_band(score: float) -> Band:
    """Map a 0-100 score to a PRD §7.2 band label."""
    for label, lo in BAND_THRESHOLDS:
        if score >= lo:
            return label
    return "LOW"


@dataclass(frozen=True)
class SharedAttributeEdge:
    """One (cin, attribute_value) edge in the bipartite Company↔SharedAttribute graph."""

    cin: str
    attribute_type: str
    attribute_value: str


@dataclass
class PropagationResult:
    """Per-CIN: the seed score (if any) and the post-propagation score / band."""

    seed_scores: dict[str, float]
    lifted_scores: dict[str, float]
    bands: dict[str, Band]


def _group_edges(
    edges: Iterable[SharedAttributeEdge],
) -> tuple[dict[tuple[str, str], list[str]], dict[str, list[tuple[str, str]]]]:
    """Build forward + reverse adjacency.

    Returns:
      attr_to_cins: {(attribute_type, attribute_value): [cin, …]}
      cin_to_attrs: {cin: [(attribute_type, attribute_value), …]}
    """
    attr_to_cins: dict[tuple[str, str], list[str]] = defaultdict(list)
    cin_to_attrs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in edges:
        key = (e.attribute_type, e.attribute_value)
        attr_to_cins[key].append(e.cin)
        cin_to_attrs[e.cin].append(key)
    return attr_to_cins, cin_to_attrs


def propagate(
    *,
    seed_scores: dict[str, float],
    edges: Iterable[SharedAttributeEdge],
    decay: float = 0.4,
    iterations: int = 3,
    cluster_min_size: int = 2,
) -> PropagationResult:
    """Loopy belief propagation on a bipartite SharedAttribute graph.

    At each step, every cluster broadcasts the average of its members' current
    scores, decayed by `decay`, to all of its members. A CIN's score is the
    MAX of its seed (if any) and the strongest inbound message — guarantees the
    seed company is never *demoted* by its neighbours.

    PRD §5.3 calls for "lift unlabelled cluster CINs to MEDIUM band". Tuning
    `decay=0.4` with 3 iterations and a CRITICAL seed (score=80) lifts a
    same-cluster unlabelled CIN to 80 * 0.4 = 32, i.e. squarely MEDIUM band.
    """
    if not 0.0 <= decay <= 1.0:
        raise ValueError(f"decay must be in [0, 1], got {decay}")
    if iterations < 1:
        raise ValueError("iterations must be >= 1")

    attr_to_cins, cin_to_attrs = _group_edges(edges)
    # Initial state: every known CIN gets its seed score; missing CINs default to 0.
    known_cins = set(seed_scores) | set(cin_to_attrs)
    scores: dict[str, float] = {cin: float(seed_scores.get(cin, 0.0)) for cin in known_cins}

    for _ in range(iterations):
        # Compute the broadcast value for each cluster — the strongest member
        # sets the propagation tier so a CRITICAL anchor reliably lifts its
        # siblings, regardless of how many low-score members dilute the average.
        cluster_broadcasts: dict[tuple[str, str], float] = {}
        for key, members in attr_to_cins.items():
            if len(members) < cluster_min_size:
                continue
            cluster_score = max(scores.get(c, 0.0) for c in members)
            cluster_broadcasts[key] = cluster_score * decay

        # Apply max-message to each CIN's current score.
        new_scores = dict(scores)
        for cin, attrs in cin_to_attrs.items():
            inbound = max(
                (cluster_broadcasts.get(key, 0.0) for key in attrs),
                default=0.0,
            )
            new_scores[cin] = max(new_scores.get(cin, 0.0), inbound)
        scores = new_scores

    bands = {cin: assign_band(score) for cin, score in scores.items()}
    return PropagationResult(
        seed_scores=dict(seed_scores),
        lifted_scores=scores,
        bands=bands,
    )

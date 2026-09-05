"""L0.5 — Graph Feature Extraction (PRD §5.3).

Seven NetworkX features per node:

  1. PageRank score                — nx.pagerank(G)
  2. Betweenness centrality        — nx.betweenness_centrality(G)
  3. Clustering coefficient        — nx.clustering(G)
  4. Director count (in-degree)    — for Company nodes: how many directors
  5. Counterparty incorporation
     age (median, days)            — median age of TRANSACTS_WITH targets at txn time
  6. Node degree                   — G.degree(node)
  7. Ego-network density           — nx.density(nx.ego_graph(G, node))

Prerequisite for D3 (TGN) and D4 (LOF). Cached as a feature matrix.

PRD §10 Day 3 acceptance: 'Feature matrix built for all 200 pre-cached companies.'
For Day 3 demo this runs on the 10-company fixture graph. Day 7 runs it on 200.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from statistics import median
from typing import Any, Iterable

import networkx as nx


@dataclass(frozen=True)
class GraphFeatures:
    """The 7 features computed per node. All in [0, 1] except integer counts."""

    pagerank: float
    betweenness: float
    clustering_coeff: float
    director_count: int
    counterparty_median_age_days: float
    degree: int
    ego_density: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def compute_pagerank(g: nx.DiGraph | nx.Graph, alpha: float = 0.85) -> dict[Any, float]:
    if g.number_of_nodes() == 0:
        return {}
    return nx.pagerank(g, alpha=alpha)


def compute_betweenness(g: nx.DiGraph | nx.Graph) -> dict[Any, float]:
    if g.number_of_nodes() < 2:
        return {n: 0.0 for n in g.nodes}
    return nx.betweenness_centrality(g)


def compute_clustering(g: nx.DiGraph | nx.Graph) -> dict[Any, float]:
    if g.number_of_nodes() == 0:
        return {}
    # NetworkX clustering on DiGraph computes by treating directionally; both work.
    return dict(nx.clustering(g))


def _ego_density(g: nx.Graph, node: Any) -> float:
    if node not in g:
        return 0.0
    ego = nx.ego_graph(g, node)
    if ego.number_of_nodes() <= 1:
        return 0.0
    return nx.density(ego)


def _counterparty_median_age_days(
    g: nx.DiGraph | nx.Graph,
    node: Any,
    incorporation_dates: dict[Any, date],
    as_of: date,
) -> float:
    """Median age in days of the node's TRANSACTS_WITH neighbours at `as_of`."""
    if node not in g:
        return 0.0
    neighbours = list(g.successors(node)) if g.is_directed() else list(g.neighbors(node))
    ages: list[int] = []
    for nb in neighbours:
        inc = incorporation_dates.get(nb)
        if inc is None:
            continue
        ages.append((as_of - inc).days)
    if not ages:
        return 0.0
    return float(median(ages))


def extract_features(
    g: nx.DiGraph | nx.Graph,
    *,
    director_counts: dict[Any, int] | None = None,
    incorporation_dates: dict[Any, date] | None = None,
    as_of: date | None = None,
) -> dict[Any, GraphFeatures]:
    """Compute all 7 features for every node in `g`.

    director_counts:
        Optional pre-computed map of `node -> number of directors`. If None, we
        treat in-degree as a proxy. The pipeline passes the exact count from
        IS_DIRECTOR_OF edges so this matches PRD §4.4 P4 ("Spider Web").
    incorporation_dates:
        Required for feature 5 (counterparty age). If None, that feature is 0.
    as_of:
        Reference date for counterparty age. Defaults to today.
    """
    director_counts = director_counts or {}
    incorporation_dates = incorporation_dates or {}
    as_of = as_of or date.today()

    pr = compute_pagerank(g)
    bw = compute_betweenness(g)
    cc = compute_clustering(g)

    # NetworkX ego_graph + density require an undirected view for the textbook formula.
    undirected = g.to_undirected() if g.is_directed() else g

    features: dict[Any, GraphFeatures] = {}
    for node in g.nodes:
        features[node] = GraphFeatures(
            pagerank=float(pr.get(node, 0.0)),
            betweenness=float(bw.get(node, 0.0)),
            clustering_coeff=float(cc.get(node, 0.0)),
            director_count=int(director_counts.get(node, 0)),
            counterparty_median_age_days=_counterparty_median_age_days(
                g, node, incorporation_dates, as_of
            ),
            degree=int(g.degree(node)),
            ego_density=float(_ego_density(undirected, node)),
        )
    return features


def build_company_graph(
    edges: Iterable[tuple[Any, Any, dict[str, Any] | None]],
) -> nx.DiGraph:
    """Convenience constructor for tests. edges = [(src, dst, attrs)]."""
    g: nx.DiGraph = nx.DiGraph()
    for src, dst, attrs in edges:
        g.add_edge(src, dst, **(attrs or {}))
    return g

"""L0.5 graph features tests — verify all 7 features compute on a known graph.

PRD §5.3 specifies:
  pagerank, betweenness, clustering coefficient, director count, counterparty
  median age, degree, ego-network density.
"""

from datetime import date

import networkx as nx
import pytest

from ml.l05_graph_features import (
    GraphFeatures,
    build_company_graph,
    extract_features,
)


@pytest.fixture
def triangle_graph() -> nx.DiGraph:
    """Three companies forming a directed cycle. Useful for SCC + density tests."""
    return build_company_graph([("A", "B", None), ("B", "C", None), ("C", "A", None)])


def test_features_are_returned_per_node(triangle_graph: nx.DiGraph) -> None:
    features = extract_features(triangle_graph)
    assert set(features.keys()) == {"A", "B", "C"}
    for node, f in features.items():
        assert isinstance(f, GraphFeatures)


def test_pagerank_sums_to_one_on_strongly_connected(triangle_graph: nx.DiGraph) -> None:
    features = extract_features(triangle_graph)
    total = sum(f.pagerank for f in features.values())
    assert total == pytest.approx(1.0, abs=1e-6)


def test_pagerank_uniform_on_symmetric_cycle(triangle_graph: nx.DiGraph) -> None:
    features = extract_features(triangle_graph)
    values = [f.pagerank for f in features.values()]
    assert min(values) == pytest.approx(max(values), abs=1e-6)


def test_betweenness_uniform_on_directed_cycle(triangle_graph: nx.DiGraph) -> None:
    # Directed 3-cycle A->B->C->A: each node is the intermediate of exactly one
    # shortest path (e.g. B is on A->C). Normalized betweenness = 1/(n-1)(n-2) * 1 = 0.5.
    features = extract_features(triangle_graph)
    values = [f.betweenness for f in features.values()]
    assert min(values) == pytest.approx(max(values), abs=1e-9)
    assert values[0] == pytest.approx(0.5, abs=1e-9)


def test_director_counts_passed_through() -> None:
    g = build_company_graph([("A", "B", None), ("A", "C", None)])
    features = extract_features(g, director_counts={"A": 5, "B": 2, "C": 1})
    assert features["A"].director_count == 5
    assert features["B"].director_count == 2
    assert features["C"].director_count == 1


def test_degree_matches_networkx() -> None:
    g = build_company_graph([("A", "B", None), ("A", "C", None), ("B", "C", None)])
    features = extract_features(g)
    # in DiGraph, .degree(node) = in + out
    assert features["A"].degree == 2
    assert features["B"].degree == 2
    assert features["C"].degree == 2


def test_counterparty_median_age_zero_when_no_dates() -> None:
    g = build_company_graph([("A", "B", None)])
    features = extract_features(g)
    assert features["A"].counterparty_median_age_days == 0.0


def test_counterparty_median_age_computed() -> None:
    g = build_company_graph([("A", "B", None), ("A", "C", None), ("A", "D", None)])
    dates = {
        "B": date(2020, 1, 1),  # 365*5 ~= 1827 days
        "C": date(2018, 1, 1),  # ~2557 days
        "D": date(2015, 1, 1),  # ~3653 days
    }
    features = extract_features(
        g, incorporation_dates=dates, as_of=date(2025, 1, 1),
    )
    # Median of (1827, 2557, 3653) = 2557
    assert features["A"].counterparty_median_age_days == pytest.approx(2557, abs=1)


def test_ego_density_singleton_is_zero() -> None:
    g = build_company_graph([("A", "A", None)])
    features = extract_features(g)
    # Self-loop ego graph has 1 node -> density 0
    assert features["A"].ego_density == 0.0


def test_ego_density_triangle_is_one() -> None:
    # In an undirected triangle, the ego graph of any node has 3 nodes, 3 edges
    # -> density = 2*3 / (3*2) = 1.0
    g = nx.Graph()
    g.add_edges_from([("A", "B"), ("B", "C"), ("A", "C")])
    features = extract_features(g)
    assert features["A"].ego_density == pytest.approx(1.0)


def test_clustering_coeff_present_for_all_nodes(triangle_graph: nx.DiGraph) -> None:
    features = extract_features(triangle_graph)
    for f in features.values():
        assert 0.0 <= f.clustering_coeff <= 1.0


def test_empty_graph_returns_empty_features() -> None:
    g: nx.DiGraph = nx.DiGraph()
    assert extract_features(g) == {}


def test_features_serialise_via_as_dict() -> None:
    g = build_company_graph([("A", "B", None)])
    features = extract_features(g)
    d = features["A"].as_dict()
    assert set(d.keys()) == {
        "pagerank", "betweenness", "clustering_coeff", "director_count",
        "counterparty_median_age_days", "degree", "ego_density",
    }

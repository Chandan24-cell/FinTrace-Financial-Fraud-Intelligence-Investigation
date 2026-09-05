"""Tests for the TGN data pipeline (PRD §9 Phase 2 P2-2 + §10 Day 8)."""

from __future__ import annotations

import numpy as np
import pytest
import torch
from torch_geometric.data import TemporalData

from ml.detectors.d3_tgn_pipeline import (
    TemporalEventStream,
    inductive_holdout,
    stream_from_rows,
)


def _rows(triples: list[tuple[str, str, int, float]]) -> list[dict]:
    return [
        {"src_cin": s, "dst_cin": d, "year": y, "amount": a, "nature": "related_party"}
        for s, d, y, a in triples
    ]


def test_stream_from_empty_rows_returns_empty_stream() -> None:
    stream = stream_from_rows([])
    assert stream.num_nodes == 0
    assert len(stream.src) == 0


def test_stream_builds_consistent_node_ids() -> None:
    rows = _rows([
        ("U45201MH2005PTC155294", "U27101MH2010PTC215432", 2017, 1_000_000.0),
        ("U27101MH2010PTC215432", "U45201MH2005PTC155294", 2018, 2_000_000.0),
    ])
    stream = stream_from_rows(rows)
    assert stream.num_nodes == 2
    # Same CIN must always map to the same integer id
    ilfs_id = stream.node_to_id["U45201MH2005PTC155294"]
    amtek_id = stream.node_to_id["U27101MH2010PTC215432"]
    assert stream.src[0] == ilfs_id
    assert stream.dst[0] == amtek_id
    assert stream.src[1] == amtek_id


def test_normalised_amounts_in_unit_interval() -> None:
    rows = _rows([
        ("A", "B", 2023, 100.0),
        ("A", "C", 2023, 100_000_000.0),
        ("B", "C", 2023, 1.0),
    ])
    stream = stream_from_rows(rows)
    assert np.all(np.abs(stream.msg) <= 1.0)
    # Largest amount maps to ±1.0; smallest maps closer to 0.
    largest = float(np.abs(stream.msg).max())
    smallest = float(np.abs(stream.msg).min())
    assert largest == pytest.approx(1.0, abs=1e-6)
    assert smallest < largest


def test_to_temporal_data_returns_valid_pyg_object() -> None:
    rows = _rows([("A", "B", 2023, 1000.0), ("B", "A", 2023, 2000.0)])
    td = stream_from_rows(rows).to_temporal_data()
    assert isinstance(td, TemporalData)
    assert td.src.dtype == torch.long
    assert td.dst.dtype == torch.long
    assert td.t.dtype == torch.long
    assert td.msg.dtype == torch.float
    assert td.msg.shape == (2, 1)  # msg unsqueezed to (n, 1)


def test_inductive_holdout_disjoint_node_sets() -> None:
    """PRD §9 Phase 2 P2-2: 'Hold out 10% entities'.
    The held-out nodes must not appear in the training event stream's endpoints."""
    rng = np.random.default_rng(0)
    rows = []
    cins = [f"U27101MH2020PTC{900000+i:06d}" for i in range(50)]
    for _ in range(200):
        s, d = rng.choice(cins, size=2, replace=False)
        rows.append({"src_cin": s, "dst_cin": d, "year": 2023, "amount": float(rng.uniform(1e3, 1e7))})

    stream = stream_from_rows(rows)
    split = inductive_holdout(stream, holdout_frac=0.10, seed=42)

    assert split.train_nodes.isdisjoint(split.test_nodes)
    n_total = stream.num_nodes
    assert 1 <= len(split.test_nodes) <= max(1, int(round(0.10 * n_total) + 1))

    # No training event touches a holdout node
    for s_id, d_id in zip(split.train.src, split.train.dst):
        assert int(s_id) not in split.test_nodes
        assert int(d_id) not in split.test_nodes

    # Every test event touches at least one holdout node
    for s_id, d_id in zip(split.test.src, split.test.dst):
        assert int(s_id) in split.test_nodes or int(d_id) in split.test_nodes


def test_inductive_holdout_is_deterministic() -> None:
    rows = _rows([(f"A{i}", f"B{i % 3}", 2023, 100.0) for i in range(40)])
    stream = stream_from_rows(rows)
    a = inductive_holdout(stream, holdout_frac=0.10, seed=42)
    b = inductive_holdout(stream, holdout_frac=0.10, seed=42)
    assert a.test_nodes == b.test_nodes
    assert a.train_nodes == b.train_nodes


def test_inductive_holdout_handles_empty_stream() -> None:
    empty = TemporalEventStream(
        src=np.array([], dtype=np.int64),
        dst=np.array([], dtype=np.int64),
        t=np.array([], dtype=np.int64),
        msg=np.array([], dtype=np.float32),
        node_to_id={},
    )
    split = inductive_holdout(empty, holdout_frac=0.10)
    assert split.train_nodes == set()
    assert split.test_nodes == set()


def test_time_field_is_monotonic_within_year() -> None:
    """TGN requires t to be non-decreasing in event order; the loader encodes
    year * 1000 + ordinal so this holds."""
    rows = _rows([
        ("A", "B", 2017, 1.0),
        ("A", "C", 2017, 2.0),
        ("B", "C", 2018, 3.0),
    ])
    stream = stream_from_rows(rows)
    assert np.all(np.diff(stream.t) >= 0)

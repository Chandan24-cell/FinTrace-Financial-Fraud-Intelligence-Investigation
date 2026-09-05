"""TGN data pipeline (PRD §9 Phase 2 P2-2 + §10 Day 8).

Pulls TRANSACTS_WITH edges from Neo4j (or accepts an in-memory event stream)
and builds a torch_geometric.data.TemporalData object that D3TGN.fit() consumes.

Inductive holdout: deterministically partitions entities so that ~holdout_frac
of nodes appear ONLY in the test set. The training set still sees every
edge whose endpoints are both in the inductive training pool.

Acceptance per PRD §9 Phase 2 P2-2:
  'Inductive test: hold out 10% entities. AUC should not drop >2pp.'

The AUC assertion itself is a Day-9 OOF retrain check; Day-8 ships the
data pipeline (loader + split + TemporalData builder).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import torch
from torch_geometric.data import TemporalData

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


_TX_QUERY = """
MATCH (a:Company)-[r:TRANSACTS_WITH]->(b:Company)
RETURN a.cin AS src_cin, b.cin AS dst_cin,
       r.year AS year, r.amount AS amount, r.nature AS nature
ORDER BY r.year ASC, r.amount ASC
"""


@dataclass
class TemporalEventStream:
    """In-memory representation of the transaction graph used by D3 TGN.

    Each array has length n_events.
      src/dst   : integer node ids (mapped from CIN)
      t         : event timestamp (monotonically non-decreasing for TGN)
      msg       : per-event scalar message (e.g. amount norm to [-1, 1])
      node_to_id: CIN -> int mapping used to convert back at inference time
    """

    src: np.ndarray
    dst: np.ndarray
    t: np.ndarray
    msg: np.ndarray
    node_to_id: dict[str, int]

    @property
    def num_nodes(self) -> int:
        return len(self.node_to_id)

    def to_temporal_data(self) -> TemporalData:
        return TemporalData(
            src=torch.as_tensor(self.src, dtype=torch.long),
            dst=torch.as_tensor(self.dst, dtype=torch.long),
            t=torch.as_tensor(self.t, dtype=torch.long),
            msg=torch.as_tensor(self.msg, dtype=torch.float).unsqueeze(-1),
        )


def _normalise_amount(amounts: np.ndarray) -> np.ndarray:
    """Map raw INR amounts onto [-1, 1] using log-scale + tanh — preserves the
    sign convention and avoids numerical issues with the ₹100 cr+ tail."""
    if amounts.size == 0:
        return amounts.astype(np.float32)
    signs = np.sign(amounts)
    logged = np.log1p(np.abs(amounts.astype(np.float64)))
    if logged.max() < 1e-9:
        return np.zeros_like(amounts, dtype=np.float32)
    scaled = (logged / logged.max()) * signs
    return scaled.astype(np.float32)


async def stream_from_neo4j(driver) -> TemporalEventStream:
    """Pull every TRANSACTS_WITH edge from the live graph and shape it for TGN.

    The first time this is called the database may be empty (no Day-3 ingestion
    has run with TRANSACTS_WITH writes). In that case we return an empty stream
    rather than raising — callers fall back to synthetic event generation."""
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(_TX_QUERY)
        rows = [record.data() async for record in result]

    return stream_from_rows(rows)


def stream_from_rows(rows: list[dict]) -> TemporalEventStream:
    """Build a TemporalEventStream from already-fetched dict rows. Easier to test."""
    if not rows:
        return TemporalEventStream(
            src=np.array([], dtype=np.int64),
            dst=np.array([], dtype=np.int64),
            t=np.array([], dtype=np.int64),
            msg=np.array([], dtype=np.float32),
            node_to_id={},
        )

    node_to_id: dict[str, int] = {}
    src_idx: list[int] = []
    dst_idx: list[int] = []
    t_vals: list[int] = []
    amount_vals: list[float] = []

    for row in rows:
        s_cin, d_cin = row["src_cin"], row["dst_cin"]
        if s_cin not in node_to_id:
            node_to_id[s_cin] = len(node_to_id)
        if d_cin not in node_to_id:
            node_to_id[d_cin] = len(node_to_id)
        src_idx.append(node_to_id[s_cin])
        dst_idx.append(node_to_id[d_cin])
        # Encode year as a deterministic int — 'year * 1000 + ordinal' so events
        # within the same year keep their original ordering.
        t_vals.append(int(row["year"]) * 1000 + len(t_vals))
        amount_vals.append(float(row["amount"] or 0.0))

    amounts_arr = np.array(amount_vals, dtype=np.float64)
    return TemporalEventStream(
        src=np.array(src_idx, dtype=np.int64),
        dst=np.array(dst_idx, dtype=np.int64),
        t=np.array(t_vals, dtype=np.int64),
        msg=_normalise_amount(amounts_arr),
        node_to_id=node_to_id,
    )


@dataclass
class InductiveSplit:
    """Inductive-split result. Train events touch only train_nodes; test events
    touch at least one held-out node (the 'unseen' inductive case)."""

    train: TemporalEventStream
    test: TemporalEventStream
    train_nodes: set[int]
    test_nodes: set[int]


def inductive_holdout(
    stream: TemporalEventStream,
    *,
    holdout_frac: float = 0.10,
    seed: int = 42,
) -> InductiveSplit:
    """Partition the event stream so a `holdout_frac` slice of nodes appears
    ONLY in test events. PRD §9 Phase 2 P2-2 uses this for the AUC sanity check.

    Strategy:
      - Sort nodes by their event count (descending). Hold out the *lowest-degree*
        bottom slice — this approximates 'new entities at inference' (rare nodes
        the model has likely seen less of).
      - Train events: both endpoints in train pool.
      - Test events: at least one endpoint in holdout pool.
    """
    if stream.num_nodes == 0:
        return InductiveSplit(stream, stream, set(), set())

    rng = np.random.default_rng(seed)
    degrees: dict[int, int] = {}
    for s, d in zip(stream.src, stream.dst):
        degrees[int(s)] = degrees.get(int(s), 0) + 1
        degrees[int(d)] = degrees.get(int(d), 0) + 1

    n_holdout = max(1, int(round(holdout_frac * stream.num_nodes)))
    # Sort by ascending degree, break ties via the RNG so partitions are deterministic
    nodes_sorted = sorted(
        degrees.items(),
        key=lambda kv: (kv[1], rng.random()),
    )
    holdout_nodes = {n for n, _ in nodes_sorted[:n_holdout]}
    train_nodes = set(degrees.keys()) - holdout_nodes

    src = stream.src
    dst = stream.dst
    in_holdout = np.array([int(s) in holdout_nodes or int(d) in holdout_nodes
                           for s, d in zip(src, dst)], dtype=bool)
    train_mask = ~in_holdout

    def _select(mask: np.ndarray) -> TemporalEventStream:
        return TemporalEventStream(
            src=stream.src[mask],
            dst=stream.dst[mask],
            t=stream.t[mask],
            msg=stream.msg[mask],
            node_to_id=stream.node_to_id,
        )

    return InductiveSplit(
        train=_select(train_mask),
        test=_select(in_holdout),
        train_nodes=train_nodes,
        test_nodes=holdout_nodes,
    )

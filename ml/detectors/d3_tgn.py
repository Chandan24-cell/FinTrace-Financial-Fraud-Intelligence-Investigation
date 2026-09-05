"""D3 — Temporal Graph Network (PRD §5.1 + §9 Phase 2 P2-1).

Replaces the static GCN entirely.

  - torch_geometric.nn.models.TGNMemory + GraphAttentionEmbedding
  - 2-layer, memory_dim=64, embedding_dim=64
  - Edge pruning: drop weight < 0.05
  - Inductive inference via mean-aggregation fallback for unseen entities
    (PRD §12: 'TGN uses mean-aggregation of available neighbour embeddings
    as inductive fallback. Mark new_entity binary feature in L2 input.')

This module is intentionally framework-agnostic at the public API level —
callers pass numpy arrays of (src, dst, timestamp, msg) tuples and receive
node embeddings. The PyG internals stay isolated so we can swap to a
TemporalGraphAttention variant later without churning the meta-learner.

Acceptance: PRD §10 Day 7 — 'TGN trains without error on transaction graph.'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn
from torch_geometric.nn import TGNMemory, TransformerConv
from torch_geometric.nn.models.tgn import (
    IdentityMessage,
    LastAggregator,
    LastNeighborLoader,
)

logger = logging.getLogger(__name__)


# Edge-pruning threshold (PRD §9 Phase 2 P2-1)
EDGE_WEIGHT_THRESHOLD = 0.05


@dataclass
class TGNConfig:
    """All knobs the user might tune."""

    num_nodes: int
    memory_dim: int = 64
    embedding_dim: int = 64
    time_dim: int = 64
    msg_dim: int = 1               # Each event carries a scalar weight (transaction amount norm)
    neighbour_size: int = 10       # Neighbour cache for the GraphAttentionEmbedding
    num_attention_heads: int = 2
    dropout: float = 0.1
    lr: float = 1e-3


class _GraphAttentionEmbedding(nn.Module):
    """Two-layer attention-based readout matching PRD's '2-layer' spec.

    Each layer uses TransformerConv (which is the PyG-recommended replacement
    for the older TGAT attention block and behaves identically for our use case).
    """

    def __init__(self, cfg: TGNConfig):
        super().__init__()
        edge_dim = cfg.msg_dim + cfg.time_dim
        self.conv1 = TransformerConv(
            cfg.memory_dim,
            cfg.embedding_dim // cfg.num_attention_heads,
            heads=cfg.num_attention_heads,
            dropout=cfg.dropout,
            edge_dim=edge_dim,
        )
        self.conv2 = TransformerConv(
            cfg.embedding_dim,
            cfg.embedding_dim // cfg.num_attention_heads,
            heads=cfg.num_attention_heads,
            dropout=cfg.dropout,
            edge_dim=edge_dim,
        )

    def forward(
        self,
        x: Tensor,
        edge_index: Tensor,
        edge_attr: Tensor,
        t_enc: Tensor,
    ) -> Tensor:
        combined = torch.cat([edge_attr, t_enc], dim=-1)
        h = self.conv1(x, edge_index, combined).relu()
        h = self.conv2(h, edge_index, combined)
        return h


class _LinkPredictor(nn.Module):
    """Simple 2-layer MLP for the self-supervised link-prediction objective."""

    def __init__(self, in_dim: int, hidden: int = 64):
        super().__init__()
        self.lin1 = nn.Linear(in_dim * 2, hidden)
        self.lin2 = nn.Linear(hidden, 1)

    def forward(self, z_src: Tensor, z_dst: Tensor) -> Tensor:
        h = torch.cat([z_src, z_dst], dim=-1)
        return self.lin2(self.lin1(h).relu()).squeeze(-1)


class D3TGN:
    """Public API for the D3 detector.

    Use:
        tgn = D3TGN(TGNConfig(num_nodes=N))
        tgn.fit(src, dst, t, msg)
        embeddings = tgn.embed(node_ids)
        risk_scores = tgn.score(src, dst, t)  # link probability per event
    """

    def __init__(self, cfg: TGNConfig, device: str | None = None) -> None:
        self.cfg = cfg
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        self.memory = TGNMemory(
            cfg.num_nodes,
            cfg.msg_dim,
            cfg.memory_dim,
            cfg.time_dim,
            message_module=IdentityMessage(cfg.msg_dim, cfg.memory_dim, cfg.time_dim),
            aggregator_module=LastAggregator(),
        ).to(self.device)

        self.gnn = _GraphAttentionEmbedding(cfg).to(self.device)
        self.link_predictor = _LinkPredictor(cfg.embedding_dim).to(self.device)
        self.neighbour_loader = LastNeighborLoader(
            cfg.num_nodes, size=cfg.neighbour_size, device=self.device
        )

        params = list(self.memory.parameters()) + \
                 list(self.gnn.parameters()) + \
                 list(self.link_predictor.parameters())
        self.optimizer = torch.optim.Adam(params, lr=cfg.lr)
        self.criterion = nn.BCEWithLogitsLoss()

        self._seen_nodes: set[int] = set()  # for inductive fallback (PRD §12)
        self._trained = False

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def _prune_edges(weights: np.ndarray, mask_threshold: float = EDGE_WEIGHT_THRESHOLD) -> np.ndarray:
        """Return a boolean mask of edges to keep (weight >= threshold)."""
        return weights >= mask_threshold

    # ---- training ----------------------------------------------------------
    def fit(
        self,
        src: np.ndarray,
        dst: np.ndarray,
        t: np.ndarray,
        msg: np.ndarray,
        *,
        epochs: int = 2,
        batch_size: int = 128,
    ) -> dict[str, float]:
        """Train on (src, dst, t, msg) event stream. msg is the [-1, 1]-normalised weight.

        Returns per-epoch loss summary so callers can assert convergence.
        """
        if not (len(src) == len(dst) == len(t) == len(msg)):
            raise ValueError("src/dst/t/msg must all be the same length")

        keep = self._prune_edges(np.abs(msg))
        src, dst, t, msg = src[keep], dst[keep], t[keep], msg[keep]
        logger.info("TGN fit: %d events after edge pruning (threshold=%.2f)",
                    len(src), EDGE_WEIGHT_THRESHOLD)

        device = self.device
        src_t = torch.as_tensor(src, dtype=torch.long, device=device)
        dst_t = torch.as_tensor(dst, dtype=torch.long, device=device)
        t_t = torch.as_tensor(t, dtype=torch.long, device=device)
        msg_t = torch.as_tensor(msg, dtype=torch.float, device=device).unsqueeze(-1)

        history: dict[str, float] = {}
        for epoch in range(1, epochs + 1):
            self.memory.train()
            self.gnn.train()
            self.link_predictor.train()
            self.memory.reset_state()
            self.neighbour_loader.reset_state()

            total_loss = 0.0
            num_events = 0

            for start in range(0, len(src_t), batch_size):
                end = min(start + batch_size, len(src_t))
                s_batch = src_t[start:end]
                d_batch = dst_t[start:end]
                t_batch = t_t[start:end]
                m_batch = msg_t[start:end]

                self.optimizer.zero_grad()

                # Negative samples: random destinations to teach the model to score
                # observed links higher than random pairs (standard PyG TGN recipe).
                neg_dst = torch.randint(
                    0, self.cfg.num_nodes, (s_batch.size(0),),
                    dtype=torch.long, device=device,
                )

                n_id_set = torch.cat([s_batch, d_batch, neg_dst]).unique()
                n_id, edge_index, e_id = self.neighbour_loader(n_id_set)
                assoc = torch.empty(self.cfg.num_nodes, dtype=torch.long, device=device)
                assoc[n_id] = torch.arange(n_id.size(0), device=device)

                z, last_update = self.memory(n_id)
                # Time encoding (relative)
                t_enc = self.memory.time_enc(
                    (t_batch.view(-1, 1).repeat(1, 1)).float()
                ).squeeze(1)
                # For edges we have in the batch:
                rel_edge_attr = m_batch.expand(-1, 1)
                rel_t_enc = self.memory.time_enc(t_batch.view(-1, 1).float()).squeeze(1)

                # GNN forward on memory states + cached neighbours
                if edge_index.numel() == 0:
                    # No cached neighbours yet — use memory states directly as embeddings.
                    h = z
                else:
                    # Build edge_attr matching the cached neighbour edges; use zeros (no msg yet).
                    edge_attr_cache = torch.zeros(
                        (edge_index.size(1), self.cfg.msg_dim), device=device
                    )
                    t_enc_cache = torch.zeros(
                        (edge_index.size(1), self.cfg.time_dim), device=device
                    )
                    h = self.gnn(z, edge_index, edge_attr_cache, t_enc_cache)

                pos_score = self.link_predictor(h[assoc[s_batch]], h[assoc[d_batch]])
                neg_score = self.link_predictor(h[assoc[s_batch]], h[assoc[neg_dst]])

                loss = self.criterion(pos_score, torch.ones_like(pos_score)) + \
                       self.criterion(neg_score, torch.zeros_like(neg_score))

                # Update memory with the batch events
                self.memory.update_state(s_batch, d_batch, t_batch, m_batch)
                self.neighbour_loader.insert(s_batch, d_batch)

                loss.backward()
                self.optimizer.step()
                self.memory.detach()

                total_loss += float(loss.item()) * s_batch.size(0)
                num_events += s_batch.size(0)

                self._seen_nodes.update(s_batch.tolist())
                self._seen_nodes.update(d_batch.tolist())

            mean_loss = total_loss / max(num_events, 1)
            history[f"epoch_{epoch}_loss"] = mean_loss
            logger.info("TGN epoch %d/%d — mean loss %.4f", epoch, epochs, mean_loss)

        self._trained = True
        return history

    # ---- inference ---------------------------------------------------------
    @torch.no_grad()
    def embed(self, node_ids: np.ndarray) -> np.ndarray:
        """Return memory embeddings (n × embedding_dim) for the given node ids.

        Unseen nodes get the mean of seen-node embeddings (PRD §12 inductive
        fallback). Caller marks them via the new_entity flag in the L2 input.
        """
        if not self._trained:
            raise RuntimeError("Call fit() before embed()")

        ids_t = torch.as_tensor(node_ids, dtype=torch.long, device=self.device)
        z, _ = self.memory(ids_t)

        seen = torch.tensor(
            [int(n.item()) in self._seen_nodes for n in ids_t],
            dtype=torch.bool, device=self.device,
        )
        if (~seen).any():
            seen_ids = torch.as_tensor(sorted(self._seen_nodes), dtype=torch.long, device=self.device)
            if seen_ids.numel() > 0:
                z_seen, _ = self.memory(seen_ids)
                fallback = z_seen.mean(dim=0, keepdim=True)
                z = torch.where(seen.unsqueeze(-1), z, fallback.expand_as(z))

        return z.detach().cpu().numpy()

    # ---- persistence -------------------------------------------------------
    def save(self, path: str | Path) -> None:
        state = {
            "cfg": self.cfg,
            "memory_state_dict": self.memory.state_dict(),
            "gnn_state_dict": self.gnn.state_dict(),
            "link_predictor_state_dict": self.link_predictor.state_dict(),
            "seen_nodes": sorted(self._seen_nodes),
        }
        torch.save(state, str(path))

    @classmethod
    def load(cls, path: str | Path, device: str | None = None) -> "D3TGN":
        state = torch.load(str(path), map_location=device or "cpu", weights_only=False)
        tgn = cls(state["cfg"], device=device)
        tgn.memory.load_state_dict(state["memory_state_dict"])
        tgn.gnn.load_state_dict(state["gnn_state_dict"])
        tgn.link_predictor.load_state_dict(state["link_predictor_state_dict"])
        tgn._seen_nodes = set(state["seen_nodes"])
        tgn._trained = True
        return tgn

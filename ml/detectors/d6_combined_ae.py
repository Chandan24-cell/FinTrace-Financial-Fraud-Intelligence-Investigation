"""D6 — Combined Autoencoder (tabular + L0.5 graph). PRD §5.1.

PRD: concatenate 20 financial features + 7 graph features = 27-dim input.
Normalize each group independently to [0, 1]. Encoder [27 -> 64 -> 32 -> 16]
with ReLU/BatchNorm/dropout=0.2; decoder mirrors. Reconstruction error is
re-scaled to [0, 1] using the 99th percentile of the train-set error
distribution.

D6 emits one anomaly score per company; downstream F1a treats that as one
feature in the L2 stack. Day-14 acceptance (PRD §10): 'D6 reconstructs L0.5
features.'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import torch
import torch.nn as nn

from ml.l05_graph_features import GraphFeatures

logger = logging.getLogger(__name__)


# PRD §5.1 dim contract — keep at module level so callers can assert shape.
N_TABULAR_FEATURES = 20
N_GRAPH_FEATURES = 7
N_INPUT_FEATURES = N_TABULAR_FEATURES + N_GRAPH_FEATURES  # 27

ENCODER_LAYERS = (N_INPUT_FEATURES, 64, 32, 16)
DROPOUT = 0.2


def _linear_block(in_dim: int, out_dim: int, *, dropout: float) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(in_dim, out_dim),
        nn.BatchNorm1d(out_dim),
        nn.ReLU(),
        nn.Dropout(dropout),
    )


class CombinedAutoencoder(nn.Module):
    """27 -> 64 -> 32 -> 16 -> 32 -> 64 -> 27 autoencoder."""

    def __init__(self, dropout: float = DROPOUT) -> None:
        super().__init__()
        enc_in_outs = list(zip(ENCODER_LAYERS[:-1], ENCODER_LAYERS[1:]))
        self.encoder = nn.Sequential(*[
            _linear_block(i, o, dropout=dropout) for i, o in enc_in_outs
        ])
        dec_in_outs = list(zip(ENCODER_LAYERS[::-1][:-1], ENCODER_LAYERS[::-1][1:]))
        decoder_blocks = []
        for idx, (i, o) in enumerate(dec_in_outs):
            if idx == len(dec_in_outs) - 1:
                # Final layer is plain linear: outputs are normalized features in [0, 1],
                # so leave the projection un-activated and let MSE handle it.
                decoder_blocks.append(nn.Linear(i, o))
            else:
                decoder_blocks.append(_linear_block(i, o, dropout=dropout))
        self.decoder = nn.Sequential(*decoder_blocks)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        return self.encoder(x)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encode(x))


@dataclass(frozen=True)
class GroupNormaliser:
    """Per-feature min/max from the train set. Stored so inference normalises
    with the same constants used at train time."""

    tab_min: np.ndarray
    tab_max: np.ndarray
    graph_min: np.ndarray
    graph_max: np.ndarray

    def transform(self, tabular: np.ndarray, graph: np.ndarray) -> np.ndarray:
        t = _minmax(tabular, self.tab_min, self.tab_max)
        g = _minmax(graph, self.graph_min, self.graph_max)
        return np.concatenate([t, g], axis=1).astype(np.float32)


def _minmax(arr: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    return np.clip((arr - lo) / span, 0.0, 1.0)


def fit_normaliser(tabular: np.ndarray, graph: np.ndarray) -> GroupNormaliser:
    if tabular.shape[1] != N_TABULAR_FEATURES:
        raise ValueError(f"tabular must have {N_TABULAR_FEATURES} cols, got {tabular.shape[1]}")
    if graph.shape[1] != N_GRAPH_FEATURES:
        raise ValueError(f"graph must have {N_GRAPH_FEATURES} cols, got {graph.shape[1]}")
    return GroupNormaliser(
        tab_min=tabular.min(axis=0),
        tab_max=tabular.max(axis=0),
        graph_min=graph.min(axis=0),
        graph_max=graph.max(axis=0),
    )


def graph_features_to_array(features: Iterable[GraphFeatures]) -> np.ndarray:
    """Stack GraphFeatures into a (n_nodes, 7) array."""
    rows = [
        [f.pagerank, f.betweenness, f.clustering_coeff,
         float(f.director_count),
         f.counterparty_median_age_days / 1825.0,  # 5-year scale → roughly [0, 1]
         float(f.degree), f.ego_density]
        for f in features
    ]
    return np.asarray(rows, dtype=np.float32) if rows else np.zeros((0, N_GRAPH_FEATURES), dtype=np.float32)


@dataclass(frozen=True)
class D6Artifacts:
    """Trained model + normaliser + error-scale constants.

    Persistence uses `torch.save` on a flat dict — keeps the file format
    self-describing (state_dict + 4 numpy arrays + 1 float) and
    sidesteps joblib's pickle-the-whole-Module trap that breaks across
    torch versions. `save`/`load` are the only sanctioned I/O paths;
    direct pickling of this dataclass is unsupported.

    Stream 4.1 of the production-grade closure plan.
    """

    model: CombinedAutoencoder
    normaliser: GroupNormaliser
    error_p99: float

    def anomaly_scores(self, tabular: np.ndarray, graph: np.ndarray) -> np.ndarray:
        x = self.normaliser.transform(tabular, graph)
        self.model.eval()
        with torch.no_grad():
            x_t = torch.from_numpy(x)
            recon = self.model(x_t).cpu().numpy()
        err = np.mean((x - recon) ** 2, axis=1)
        return np.clip(err / max(self.error_p99, 1e-9), 0.0, 1.0).astype(np.float32)

    # ---- Persistence -----------------------------------------------------
    # Schema version: bump this when the on-disk layout changes so
    # `load()` can refuse stale artifacts loudly instead of silently
    # mis-decoding. The analytics_cache log surfaces the version.
    _SCHEMA_VERSION = 1

    def save(self, path: str) -> None:
        """Serialise to a single torch checkpoint at `path`."""
        from pathlib import Path as _Path
        _Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "schema_version": self._SCHEMA_VERSION,
                "model_state": self.model.state_dict(),
                "tab_min": self.normaliser.tab_min,
                "tab_max": self.normaliser.tab_max,
                "graph_min": self.normaliser.graph_min,
                "graph_max": self.normaliser.graph_max,
                "error_p99": float(self.error_p99),
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> "D6Artifacts":
        """Reconstruct from a checkpoint written by `save`. Raises
        ValueError on schema-version mismatch so analytics_cache can
        fall back to training a fresh artifact inline."""
        # `weights_only=False` is required to deserialise the numpy
        # arrays we packed into the dict; the dict itself is fully
        # under our control so the loosened safety surface is acceptable.
        blob = torch.load(path, map_location="cpu", weights_only=False)
        version = int(blob.get("schema_version", 0))
        if version != cls._SCHEMA_VERSION:
            raise ValueError(
                f"D6 artifact at {path} is schema v{version}; "
                f"runtime expects v{cls._SCHEMA_VERSION}. Retrain via "
                f"`python -m ml.training.train_d6 --save`.",
            )
        model = CombinedAutoencoder()
        model.load_state_dict(blob["model_state"])
        model.eval()
        normaliser = GroupNormaliser(
            tab_min=np.asarray(blob["tab_min"]),
            tab_max=np.asarray(blob["tab_max"]),
            graph_min=np.asarray(blob["graph_min"]),
            graph_max=np.asarray(blob["graph_max"]),
        )
        return cls(
            model=model,
            normaliser=normaliser,
            error_p99=float(blob["error_p99"]),
        )


def train_d6(
    tabular: np.ndarray,
    graph: np.ndarray,
    *,
    epochs: int = 20,
    lr: float = 1e-3,
    batch_size: int = 32,
    seed: int = 42,
) -> D6Artifacts:
    """Smoke-train the autoencoder on the *full* concatenated feature matrix.
    Returns the trained model + normaliser + 99th-percentile error scale."""
    if tabular.shape[0] != graph.shape[0]:
        raise ValueError("tabular and graph must have the same number of rows")
    if tabular.shape[0] < 2:
        raise ValueError("need at least 2 samples to train D6")

    torch.manual_seed(seed)
    np.random.seed(seed)
    normaliser = fit_normaliser(tabular, graph)
    X = normaliser.transform(tabular, graph)

    model = CombinedAutoencoder()
    optim = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    X_t = torch.from_numpy(X)
    n = X_t.shape[0]
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(n)
        epoch_loss = 0.0
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            if idx.numel() < 2:
                continue  # BatchNorm needs >=2 rows
            batch = X_t[idx]
            optim.zero_grad()
            recon = model(batch)
            loss = loss_fn(recon, batch)
            loss.backward()
            optim.step()
            epoch_loss += float(loss.item()) * batch.shape[0]
        logger.info("D6 epoch %d/%d  avg_mse=%.5f", epoch + 1, epochs, epoch_loss / n)

    model.eval()
    with torch.no_grad():
        recon = model(X_t).cpu().numpy()
    err = np.mean((X - recon) ** 2, axis=1)
    p99 = float(np.percentile(err, 99)) if len(err) else 1e-9
    return D6Artifacts(model=model, normaliser=normaliser, error_p99=max(p99, 1e-9))

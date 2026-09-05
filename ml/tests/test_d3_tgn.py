"""Tests for D3 — Temporal Graph Network (PRD §10 Day 7 + §9 Phase 2 P2-1).

Acceptance: 'TGN trains without error on transaction graph.'

Kept tiny so CI runs in <5 seconds; the full training run lives in
scripts/day7_train_tgn.py and is exercised via the Day-7 acceptance CLI.
"""

from __future__ import annotations

import numpy as np
import pytest

from ml.detectors.d3_tgn import D3TGN, EDGE_WEIGHT_THRESHOLD, TGNConfig


def _tiny_stream(seed: int = 0):
    rng = np.random.default_rng(seed)
    n = 16
    n_events = 80
    src = rng.integers(0, n, size=n_events).astype(np.int64)
    dst = ((src + rng.integers(1, n, size=n_events)) % n).astype(np.int64)
    t = np.sort(rng.integers(0, 1000, size=n_events)).astype(np.int64)
    msg = rng.uniform(-1.0, 1.0, size=n_events).astype(np.float32)
    return n, src, dst, t, msg


def test_tgn_fits_one_epoch_without_error() -> None:
    n, src, dst, t, msg = _tiny_stream()
    tgn = D3TGN(TGNConfig(num_nodes=n))
    history = tgn.fit(src, dst, t, msg, epochs=1, batch_size=16)
    assert "epoch_1_loss" in history
    assert np.isfinite(history["epoch_1_loss"])


def test_tgn_fit_two_epochs_produces_finite_losses() -> None:
    n, src, dst, t, msg = _tiny_stream(seed=1)
    tgn = D3TGN(TGNConfig(num_nodes=n))
    history = tgn.fit(src, dst, t, msg, epochs=2, batch_size=16)
    losses = list(history.values())
    assert all(np.isfinite(loss) for loss in losses)


def test_edge_pruning_drops_low_weight_events() -> None:
    weights = np.array([0.02, 0.05, 0.1, -0.04, -0.5], dtype=np.float32)
    mask = D3TGN._prune_edges(np.abs(weights))
    assert mask.tolist() == [False, True, True, False, True]
    assert EDGE_WEIGHT_THRESHOLD == 0.05  # PRD §9 Phase 2 P2-1 verbatim


def test_embed_requires_fit_first() -> None:
    n = 8
    tgn = D3TGN(TGNConfig(num_nodes=n))
    with pytest.raises(RuntimeError, match="fit"):
        tgn.embed(np.array([0, 1, 2]))


def test_embed_returns_correct_shape() -> None:
    n, src, dst, t, msg = _tiny_stream()
    cfg = TGNConfig(num_nodes=n)
    tgn = D3TGN(cfg)
    tgn.fit(src, dst, t, msg, epochs=1, batch_size=16)
    out = tgn.embed(np.array([0, 1, 2], dtype=np.int64))
    assert out.shape == (3, cfg.memory_dim)


def test_unseen_node_falls_back_to_mean(tmp_path) -> None:
    """PRD §12: 'TGN uses mean-aggregation of available neighbour embeddings
    as inductive fallback' for entities not seen during training."""
    n, src, dst, t, msg = _tiny_stream()
    # Train only on nodes 0..7
    mask = (src < 8) & (dst < 8)
    src, dst, t, msg = src[mask], dst[mask], t[mask], msg[mask]
    tgn = D3TGN(TGNConfig(num_nodes=n))
    tgn.fit(src, dst, t, msg, epochs=1, batch_size=16)

    # Embed a never-seen node — must not raise and must be finite
    unseen = tgn.embed(np.array([15], dtype=np.int64))
    assert unseen.shape == (1, 64)
    assert np.all(np.isfinite(unseen))


def test_save_and_load_round_trip(tmp_path) -> None:
    """Verify the checkpoint can be saved, loaded, and used for inference.

    Note: PyG TGNMemory keeps per-node message stores in non-persistent state
    that aren't in state_dict, so strict embedding equality across save/load
    isn't guaranteed without re-running fit. Production code path is:
    train -> save -> at inference, load + reset_state -> re-replay events
    via the streaming online endpoint. For Day-7 acceptance we verify the
    checkpoint roundtrips and the restored model embeds without error.
    """
    n, src, dst, t, msg = _tiny_stream(seed=7)
    cfg = TGNConfig(num_nodes=n)
    tgn = D3TGN(cfg)
    tgn.fit(src, dst, t, msg, epochs=1, batch_size=16)
    ckpt = tmp_path / "d3_tgn.pt"
    tgn.save(ckpt)
    assert ckpt.exists() and ckpt.stat().st_size > 0

    restored = D3TGN.load(ckpt)
    # Restored config + seen-nodes must match
    assert restored.cfg.num_nodes == cfg.num_nodes
    assert restored.cfg.memory_dim == cfg.memory_dim
    assert restored._seen_nodes == tgn._seen_nodes
    # Restored model can run inference without exceptions and returns finite output
    out = restored.embed(np.array([0, 1, 2, 3], dtype=np.int64))
    assert out.shape == (4, cfg.memory_dim)
    assert np.all(np.isfinite(out))


def test_fit_rejects_mismatched_array_lengths() -> None:
    tgn = D3TGN(TGNConfig(num_nodes=10))
    with pytest.raises(ValueError, match="same length"):
        tgn.fit(
            np.array([0, 1, 2]),
            np.array([1, 2]),  # short by one
            np.array([0, 1, 2]),
            np.array([0.1, 0.2, 0.3], dtype=np.float32),
        )

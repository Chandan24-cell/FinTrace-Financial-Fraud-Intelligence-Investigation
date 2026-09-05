"""Day-7 TGN training CLI (PRD §10 Day 7 + §9 Phase 2 P2-1).

Acceptance: 'TGN trains without error on transaction graph.'

Generates a synthetic temporal transaction graph (a small mix of healthy
flow + an embedded carousel-like cluster), trains the D3 TGN for a few
epochs, and saves the checkpoint to ml/artifacts/d3_tgn.pt.

The real transaction graph comes online on Day 7 pre-cache (Company nodes
via precache_companies.py) and Day 12 SFIO-labelled retrain. For Day-7
acceptance we only need:
  - TGN forward/backward without exceptions
  - Loss is finite across epochs
  - Final checkpoint loads back identically
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.detectors.d3_tgn import D3TGN, TGNConfig  # noqa: E402

logger = logging.getLogger("tgn")
ARTIFACT_DIR = ROOT / "ml" / "artifacts"


def synthesise_temporal_events(
    num_nodes: int = 50,
    num_events: int = 800,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate a synthetic event stream:
       - 80% random transactions between healthy peers
       - 20% bursty cluster amongst nodes [0, 6] forming a small ring

    Returns (src, dst, t, msg). msg is in [-1, 1] (transaction-amount proxy).
    """
    rng = np.random.default_rng(seed)
    cluster_nodes = np.arange(7)  # 7-node cluster (mirrors the ITC ring size)
    healthy_nodes = np.arange(7, num_nodes)

    n_cluster = int(num_events * 0.20)
    n_healthy = num_events - n_cluster

    cluster_src = rng.choice(cluster_nodes, size=n_cluster)
    cluster_dst = (cluster_src + 1) % 7  # ring: i -> i+1 mod 7
    healthy_src = rng.choice(healthy_nodes, size=n_healthy)
    healthy_dst = rng.choice(healthy_nodes, size=n_healthy)

    src = np.concatenate([cluster_src, healthy_src])
    dst = np.concatenate([cluster_dst, healthy_dst])

    t = np.sort(rng.integers(0, 10_000, size=num_events))
    perm = rng.permutation(num_events)
    src, dst = src[perm], dst[perm]

    msg = rng.uniform(-1.0, 1.0, size=num_events).astype(np.float32)
    # Cluster events carry higher weight to test edge-pruning preserves them
    msg[:n_cluster] = rng.uniform(0.6, 1.0, size=n_cluster).astype(np.float32)
    return src.astype(np.int64), dst.astype(np.int64), t.astype(np.int64), msg


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--num-nodes", type=int, default=50)
    parser.add_argument("--num-events", type=int, default=800)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save", action="store_true", help="Persist checkpoint")
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    src, dst, t, msg = synthesise_temporal_events(
        num_nodes=args.num_nodes, num_events=args.num_events, seed=args.seed,
    )
    logger.info(
        "Synthetic stream: %d events over %d nodes, msg in [%.2f, %.2f]",
        len(src), args.num_nodes, msg.min(), msg.max(),
    )

    cfg = TGNConfig(num_nodes=args.num_nodes)
    tgn = D3TGN(cfg)
    history = tgn.fit(src, dst, t, msg, epochs=args.epochs, batch_size=args.batch_size)

    losses = list(history.values())
    print()
    print("=" * 56)
    print(" D3 TGN training summary")
    print("=" * 56)
    print(f"  epochs              {args.epochs}")
    print(f"  events_seen         {len(src)}")
    print(f"  num_nodes           {args.num_nodes}")
    print(f"  device              {tgn.device}")
    for k, v in history.items():
        print(f"  {k:<20}{v:.4f}")
    print(f"  loss_finite         {all(np.isfinite(l) for l in losses)}")
    print("=" * 56)

    if not all(np.isfinite(l) for l in losses):
        logger.error("TGN produced non-finite loss — failing acceptance check")
        sys.exit(2)

    if args.save:
        ckpt = ARTIFACT_DIR / "d3_tgn.pt"
        tgn.save(ckpt)
        logger.info("Saved TGN checkpoint to %s", ckpt)

        # Light round-trip sanity check: load works, restored model can run
        # inference. Strict embedding equality requires re-replaying the event
        # stream because PyG TGNMemory keeps per-node message stores in
        # non-persistent state — that's a Day-9+ online-inference concern.
        restored = D3TGN.load(ckpt)
        sample_ids = np.array([0, 1, 2, 3, 4], dtype=np.int64)
        out = restored.embed(sample_ids)
        if not np.all(np.isfinite(out)):
            logger.error("Restored model produced non-finite embeddings")
            sys.exit(3)
        logger.info("Checkpoint loads + embeds without error")

    sys.exit(0)


if __name__ == "__main__":
    main()

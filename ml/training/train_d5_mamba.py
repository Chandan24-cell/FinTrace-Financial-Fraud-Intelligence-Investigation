"""D5 Mamba SSM offline training driver — Stream 4.2.

Runs on **Linux + CUDA** only — `mamba-ssm` ships CUDA kernels and is
not available on Windows or CPU-only environments (PRD §2.1). The
production deployment on Fly.io is CPU-only, so this script's role is
*offline training only* — produce `ml/artifacts/d5_mamba.pt`, commit-
or rsync-deploy it, and the runtime will load it via
`backend/app/analytics_cache.py`. When no Mamba checkpoint is present
the runtime falls back to the TCN architecture that PRD §5.1 explicitly
permits (already trained + persisted in `d5_tcn.pt`).

Recommended Colab cell layout:

    # Cell 1 — install Mamba (T4 GPU, Linux, CUDA 12.x)
    !pip install --quiet mamba-ssm causal-conv1d

    # Cell 2 — clone the repo + cd in (assumes you've authenticated git)
    !git clone https://github.com/<org>/sme-fraud-detection.git
    %cd sme-fraud-detection
    !pip install --quiet -r requirements-ml.txt

    # Cell 3 — invoke the trainer
    !python -m ml.training.train_d5_mamba --epochs 25 --save

    # Cell 4 — download the artifact back to your laptop
    from google.colab import files
    files.download('ml/artifacts/d5_mamba.pt')

Local-CPU smoke (no Mamba install) is unsupported on purpose — the
script raises at import time if `mamba_ssm` isn't available so failures
surface immediately instead of mid-training. Use the TCN fallback if
you need a CPU run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend.app.ingest.sources import FixtureSource  # noqa: E402
from ml.detectors.d5_mamba import (  # noqa: E402
    FEATURE_DIM,
    MAX_SEQ_LEN,
    build_sequences_from_company_bundles,
)

logger = logging.getLogger("train_d5_mamba")
ARTIFACT_PATH = ROOT / "ml" / "artifacts" / "d5_mamba.pt"


def _import_mamba():
    """Defer the Mamba import so the script can be linted / type-checked
    on Windows + CPU. Raises a friendly error when the SDK isn't
    available at training time."""
    try:
        import torch
        from mamba_ssm import Mamba  # noqa: F401  — checked symbol
        return torch
    except ImportError as exc:  # pragma: no cover — install-time path
        raise SystemExit(
            "mamba-ssm is not installed. This driver only runs on "
            "Linux + CUDA. Install with:\n"
            "  pip install mamba-ssm causal-conv1d\n"
            "Or use the TCN fallback in ml/detectors/d5_mamba.py.\n"
            f"Original error: {exc}",
        ) from exc


def _build_mamba_classifier(d_model: int = 32, d_state: int = 16, n_layers: int = 2):
    """Stack of Mamba blocks → mean-pool → linear-head returning a
    scalar fraud probability per sequence. Architecture mirrors the TCN
    fallback's IO contract so analytics_cache can hot-swap the
    state_dict at boot."""
    torch = _import_mamba()
    from mamba_ssm import Mamba  # noqa: WPS433 — checked above
    import torch.nn as nn

    class _MambaClassifier(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = nn.Linear(FEATURE_DIM, d_model)
            self.blocks = nn.ModuleList([
                Mamba(d_model=d_model, d_state=d_state, d_conv=4, expand=2)
                for _ in range(n_layers)
            ])
            self.head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, 1),
                nn.Sigmoid(),
            )

        def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
            # x: (B, T, F), mask: (B, T) with 1s on real events.
            h = self.embed(x)
            for block in self.blocks:
                h = block(h) + h  # residual
            # Masked mean-pool — treat padding as no-information.
            mask_f = mask.unsqueeze(-1).float()
            pooled = (h * mask_f).sum(dim=1) / (mask_f.sum(dim=1).clamp(min=1.0))
            return self.head(pooled).squeeze(-1)

    return torch, _MambaClassifier()


async def _load_all_bundles():
    src = FixtureSource()
    out = []
    for cin in await src.list_available_cins():
        b = await src.fetch_bundle(cin)
        if b is not None:
            out.append(b)
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline Mamba D5 trainer (Linux+CUDA only)")
    p.add_argument("--epochs", type=int, default=25)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save", action="store_true",
                   help="write artifact to ml/artifacts/d5_mamba.pt")
    p.add_argument("--output", type=str, default=None)
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    args = _parse_args()

    torch, model = _build_mamba_classifier()
    if torch.cuda.is_available():
        model = model.cuda()
        logger.info("train_d5_mamba: CUDA detected — training on GPU")
    else:
        logger.warning(
            "train_d5_mamba: no CUDA device — Mamba SSM is unusably slow on CPU. "
            "Aborting to keep training honest. Run on Colab T4.",
        )
        return 2

    bundles = asyncio.run(_load_all_bundles())
    if not bundles:
        logger.error("train_d5_mamba: no fixture bundles available — aborting")
        return 2

    batch = build_sequences_from_company_bundles(bundles)
    if not batch.cins:
        logger.error("train_d5_mamba: no sequences buildable from fixtures — aborting")
        return 2

    # Synthetic "is_risky" label is the legacy heuristic shipping in
    # the fixtures themselves (revenue + adverse-flag pattern). When
    # Stream 4.4 lands the real SFIO label augmentation, swap this for
    # the joined label set.
    cin_to_idx = {c: i for i, c in enumerate(batch.cins)}
    y_np = np.zeros(len(batch.cins), dtype=np.float32)
    for b in bundles:
        i = cin_to_idx.get(b.company.cin)
        if i is None:
            continue
        # Latest FS adverse flag is the cheapest pseudo-label we can
        # extract without leaking the SFIO list into training.
        fs_sorted = sorted(b.financials, key=lambda f: f.year)
        if fs_sorted and getattr(fs_sorted[-1], "adverse_flag", False):
            y_np[i] = 1.0

    x_t = torch.from_numpy(batch.x).float().cuda()
    m_t = torch.from_numpy(batch.mask).float().cuda()
    y_t = torch.from_numpy(y_np).float().cuda()

    optim = torch.optim.AdamW(model.parameters(), lr=args.lr)
    loss_fn = torch.nn.BCELoss()
    n = x_t.shape[0]
    torch.manual_seed(args.seed)
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(n)
        epoch_loss = 0.0
        for i in range(0, n, args.batch_size):
            idx = perm[i:i + args.batch_size]
            if idx.numel() == 0:
                continue
            optim.zero_grad()
            pred = model(x_t[idx], m_t[idx])
            loss = loss_fn(pred, y_t[idx])
            loss.backward()
            optim.step()
            epoch_loss += float(loss.item()) * idx.numel()
        logger.info(
            "train_d5_mamba: epoch %d/%d  avg_bce=%.5f",
            epoch + 1, args.epochs, epoch_loss / max(n, 1),
        )

    model.eval()
    if not (args.save or args.output):
        logger.info("train_d5_mamba: --save not set; skipping persistence")
        return 0

    out_path = Path(args.output) if args.output else ARTIFACT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), str(out_path))
    logger.info(
        "train_d5_mamba: Mamba checkpoint written to %s (state_dict only — "
        "analytics_cache reconstructs the model class at load time)",
        out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

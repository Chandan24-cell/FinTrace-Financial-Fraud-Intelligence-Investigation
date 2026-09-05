"""D5 — Mamba SSM (sequence detector). PRD §5.1.

PRD §2.1 lists `mamba-ssm` as Linux+CUDA only. We ship two artefacts:

  1. A pure-Python data pipeline that builds per-company transaction sequences
     (amount, counterparty_type, gst_status, timestamp_delta). It runs anywhere.
  2. A `TemporalConvolutionalNet` (TCN) torch module — the CPU-friendly
     fallback explicitly named in PRD §5.1 for production inference on Fly.io
     free, where Mamba can't run.

Training the actual Mamba weights happens in a Colab notebook (RTX 3050 or
T4) — see `ml/training/d5_mamba_train.ipynb` (stub). Production loads the
exported TCN fallback or the Mamba checkpoint depending on the runtime.

Day-14 acceptance (PRD §10): 'D5 trains on Colab pipeline.' We deliver the
data-prep pipeline (`build_sequences`) + the trainable TCN fallback so a
CPU smoke-run completes end-to-end without `mamba-ssm`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


# PRD §5.1 sequence config — keep them at module level so the Colab notebook
# imports the same constants we test against.
MAX_SEQ_LEN = 128
FEATURE_DIM = 4   # amount, counterparty_type, gst_status, timestamp_delta_days


@dataclass(frozen=True)
class SequenceBatch:
    """Numpy batch ready to hand to torch.from_numpy(...).

    `x` shape: (batch, MAX_SEQ_LEN, FEATURE_DIM). Padding is zeros.
    `mask` shape: (batch, MAX_SEQ_LEN) — 1 for real events, 0 for padding.
    """

    x: np.ndarray
    mask: np.ndarray
    cins: list[str]


# --- Encoders for the four sequence features --------------------------------
# Counterparty-type is a tiny ordinal vocabulary; gst_status is binary.
_COUNTERPARTY_VOCAB = {
    "unknown": 0,
    "supplier": 1,
    "customer": 2,
    "related_party": 3,
    "intercompany": 4,
    "bank": 5,
}


def _encode_amount(amount: float) -> float:
    """Log-scale and sign-preserve, clipped to [-1, 1] like D3 does."""
    if amount == 0.0:
        return 0.0
    sign = 1.0 if amount > 0 else -1.0
    return float(sign * min(1.0, np.log1p(abs(amount)) / np.log1p(1e10)))


def _encode_counterparty(kind: str | None) -> float:
    if not kind:
        return float(_COUNTERPARTY_VOCAB["unknown"]) / max(_COUNTERPARTY_VOCAB.values())
    idx = _COUNTERPARTY_VOCAB.get(kind.strip().lower(), 0)
    return float(idx) / max(_COUNTERPARTY_VOCAB.values())


def _encode_gst_status(status: str | None) -> float:
    if not status:
        return 0.0
    s = status.strip().lower()
    if s in {"missing_trader", "cancelled"}:
        return 1.0
    if s in {"active"}:
        return 0.0
    return 0.5


def _encode_delta_days(delta: int) -> float:
    """Map non-negative day-delta to [0, 1] via log1p / log1p(3650)."""
    return float(min(1.0, np.log1p(max(delta, 0)) / np.log1p(3650)))


def _row_features(row: dict[str, Any]) -> np.ndarray:
    return np.asarray([
        _encode_amount(float(row.get("amount", 0.0))),
        _encode_counterparty(row.get("counterparty_type")),
        _encode_gst_status(row.get("gst_status")),
        _encode_delta_days(int(row.get("delta_days", 0))),
    ], dtype=np.float32)


def build_sequence_for_cin(rows: Iterable[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Pad/truncate one company's transaction list to (MAX_SEQ_LEN, FEATURE_DIM).

    Rows must be sorted in ascending time. PRD §5.1: truncation keeps the
    *most recent* MAX_SEQ_LEN events, since signal usually concentrates near
    the breakdown.
    """
    rows = list(rows)
    rows = rows[-MAX_SEQ_LEN:]
    x = np.zeros((MAX_SEQ_LEN, FEATURE_DIM), dtype=np.float32)
    mask = np.zeros((MAX_SEQ_LEN,), dtype=np.float32)
    offset = MAX_SEQ_LEN - len(rows)
    for i, row in enumerate(rows):
        x[offset + i] = _row_features(row)
        mask[offset + i] = 1.0
    return x, mask


def build_sequences(
    transactions_by_cin: dict[str, list[dict[str, Any]]],
) -> SequenceBatch:
    """Build the full SequenceBatch from a {cin: rows} dict.

    `rows` must each be a dict with keys: amount (float), counterparty_type (str),
    gst_status (str), delta_days (int). Sort order is preserved.
    """
    cins = sorted(transactions_by_cin.keys())
    xs = np.zeros((len(cins), MAX_SEQ_LEN, FEATURE_DIM), dtype=np.float32)
    masks = np.zeros((len(cins), MAX_SEQ_LEN), dtype=np.float32)
    for i, cin in enumerate(cins):
        x, m = build_sequence_for_cin(transactions_by_cin[cin])
        xs[i] = x
        masks[i] = m
    return SequenceBatch(x=xs, mask=masks, cins=cins)


def build_sequences_from_company_bundles(
    bundles: Iterable[Any],
    *,
    today: date | None = None,
) -> SequenceBatch:
    """Per PRD §5.1: turn each company's known transactions (charges + FS events)
    into a Mamba-ready sequence. We synthesise rows from the bundle data we have.

    For the fixture/synthetic mode we don't have a transaction ledger — instead
    we materialise each LoanDisbursement charge + each FS year-end as one event.
    Real ingestion (Day 17+) will plug live TRANSACTS_WITH rows in here.
    """
    today = today or date.today()
    transactions_by_cin: dict[str, list[dict[str, Any]]] = {}
    for bundle in bundles:
        rows: list[dict[str, Any]] = []
        for charge in bundle.charges:
            delta = (today - charge.creation_date).days
            rows.append({
                "amount": charge.amount,
                "counterparty_type": "bank",
                "gst_status": "active",
                "delta_days": delta,
            })
        for fs in sorted(bundle.financials, key=lambda f: f.year):
            ye = date(fs.year, 3, 31)
            delta = (today - ye).days
            rows.append({
                "amount": fs.revenue,
                "counterparty_type": "customer",
                "gst_status": "active" if not fs.adverse_flag else "missing_trader",
                "delta_days": delta,
            })
        if rows:
            rows.sort(key=lambda r: -int(r["delta_days"]))  # oldest first
            transactions_by_cin[bundle.company.cin] = rows
    return build_sequences(transactions_by_cin)


# --- TCN fallback (PRD §5.1: kernel=3, 4 layers, 64 filters) ----------------
class _TCNBlock(nn.Module):
    """One residual dilated-conv block. Causal padding keeps the sequence
    length unchanged while exposing increasingly large receptive fields."""

    def __init__(self, n_filters: int, kernel_size: int, dilation: int, dropout: float):
        super().__init__()
        self.pad = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(n_filters, n_filters, kernel_size, dilation=dilation)
        self.conv2 = nn.Conv1d(n_filters, n_filters, kernel_size, dilation=dilation)
        self.bn1 = nn.BatchNorm1d(n_filters)
        self.bn2 = nn.BatchNorm1d(n_filters)
        self.act = nn.ReLU()
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        x = nn.functional.pad(x, (self.pad, 0))
        x = self.act(self.bn1(self.conv1(x)))
        x = self.dropout(x)
        x = nn.functional.pad(x, (self.pad, 0))
        x = self.act(self.bn2(self.conv2(x)))
        x = self.dropout(x)
        return self.act(x + residual)


class TemporalConvolutionalNet(nn.Module):
    """CPU-friendly fallback for Mamba. Same input/output contract as the
    Mamba model: takes (batch, seq_len, feature_dim), returns one anomaly
    score per sample."""

    def __init__(
        self,
        feature_dim: int = FEATURE_DIM,
        n_filters: int = 64,
        n_layers: int = 4,
        kernel_size: int = 3,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.input_proj = nn.Conv1d(feature_dim, n_filters, kernel_size=1)
        self.blocks = nn.ModuleList([
            _TCNBlock(n_filters, kernel_size, dilation=2 ** i, dropout=dropout)
            for i in range(n_layers)
        ])
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.head = nn.Linear(n_filters, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        # x: (batch, seq_len, feature_dim) -> (batch, feature_dim, seq_len)
        h = self.input_proj(x.transpose(1, 2))
        for block in self.blocks:
            h = block(h)
        if mask is not None:
            # Zero out padding positions before pooling so they don't dilute the mean.
            h = h * mask.unsqueeze(1)
        pooled = self.pool(h).squeeze(-1)
        return torch.sigmoid(self.head(pooled)).squeeze(-1)


def smoke_train_tcn(
    batch: SequenceBatch,
    labels: np.ndarray,
    *,
    epochs: int = 3,
    lr: float = 1e-3,
    seed: int = 42,
) -> tuple[TemporalConvolutionalNet, float]:
    """Tiny CPU smoke-train so the Colab notebook has a known-good baseline.
    Returns (model, final_train_loss)."""
    torch.manual_seed(seed)
    model = TemporalConvolutionalNet()
    optim = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.BCELoss()
    x = torch.from_numpy(batch.x)
    mask = torch.from_numpy(batch.mask)
    y = torch.from_numpy(labels.astype(np.float32))
    final_loss = 0.0
    for epoch in range(epochs):
        model.train()
        optim.zero_grad()
        pred = model(x, mask)
        loss = loss_fn(pred, y)
        loss.backward()
        optim.step()
        final_loss = float(loss.item())
        logger.info("D5/TCN epoch %d/%d  loss=%.4f", epoch + 1, epochs, final_loss)
    return model, final_loss

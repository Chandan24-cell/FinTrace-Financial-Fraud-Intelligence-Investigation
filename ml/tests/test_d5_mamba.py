"""Tests for D5 — Mamba sequence pipeline + TCN fallback (PRD §5.1 + §10 Day 14)."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from ml.detectors.d5_mamba import (
    FEATURE_DIM,
    MAX_SEQ_LEN,
    TemporalConvolutionalNet,
    build_sequence_for_cin,
    build_sequences,
    smoke_train_tcn,
)


def test_sequence_shape_matches_prd_contract() -> None:
    rows = [
        {"amount": 100.0, "counterparty_type": "supplier", "gst_status": "active", "delta_days": 30}
        for _ in range(50)
    ]
    x, mask = build_sequence_for_cin(rows)
    assert x.shape == (MAX_SEQ_LEN, FEATURE_DIM)
    assert mask.shape == (MAX_SEQ_LEN,)
    # 50 real events should leave (128-50)=78 padding rows on the left.
    assert int(mask.sum()) == 50
    assert mask[:78].sum() == 0.0
    assert mask[78:].sum() == 50.0


def test_sequence_truncates_to_max_len_keeping_most_recent() -> None:
    rows = [{"amount": float(i), "counterparty_type": "customer",
             "gst_status": "active", "delta_days": i} for i in range(200)]
    x, mask = build_sequence_for_cin(rows)
    assert int(mask.sum()) == MAX_SEQ_LEN
    # Final timestep amount should correspond to the last (most recent) input row.
    assert x[-1, 0] > x[0, 0]


def test_empty_rows_produce_all_padding() -> None:
    x, mask = build_sequence_for_cin([])
    assert x.shape == (MAX_SEQ_LEN, FEATURE_DIM)
    assert mask.sum() == 0.0


def test_amount_encoding_clipped_to_unit_band() -> None:
    rows = [{"amount": 1e20, "counterparty_type": "bank", "gst_status": "active", "delta_days": 1}]
    x, _ = build_sequence_for_cin(rows)
    assert -1.0 <= float(x[-1, 0]) <= 1.0


def test_negative_amount_preserves_sign() -> None:
    rows = [{"amount": -1_000_000.0, "counterparty_type": "bank", "gst_status": "active", "delta_days": 1}]
    x, _ = build_sequence_for_cin(rows)
    assert float(x[-1, 0]) < 0.0


def test_missing_trader_gst_status_encoded_to_one() -> None:
    rows = [{"amount": 100.0, "counterparty_type": "customer",
             "gst_status": "missing_trader", "delta_days": 0}]
    x, _ = build_sequence_for_cin(rows)
    assert float(x[-1, 2]) == 1.0


def test_build_sequences_stacks_batch_correctly() -> None:
    rows_a = [{"amount": 1.0, "counterparty_type": "a", "gst_status": "active", "delta_days": 0}]
    rows_b = [{"amount": 2.0, "counterparty_type": "b", "gst_status": "active", "delta_days": 0}]
    batch = build_sequences({"A": rows_a, "B": rows_b})
    assert batch.x.shape == (2, MAX_SEQ_LEN, FEATURE_DIM)
    assert batch.cins == ["A", "B"]


def test_tcn_forward_returns_one_score_per_sample() -> None:
    model = TemporalConvolutionalNet()
    model.eval()
    batch_size = 4
    with torch.no_grad():
        x = torch.zeros((batch_size, MAX_SEQ_LEN, FEATURE_DIM))
        out = model(x)
    assert out.shape == (batch_size,)
    assert ((0.0 <= out) & (out <= 1.0)).all().item()


def test_tcn_smoke_train_runs() -> None:
    rng = np.random.default_rng(0)
    n = 8
    batch = build_sequences({
        f"C{i}": [{"amount": float(rng.uniform(1, 1000)),
                   "counterparty_type": "supplier",
                   "gst_status": "active",
                   "delta_days": int(rng.integers(0, 365))}
                  for _ in range(rng.integers(2, 20))]
        for i in range(n)
    })
    labels = rng.integers(0, 2, size=n).astype(np.float32)
    _, loss = smoke_train_tcn(batch, labels, epochs=1)
    assert np.isfinite(loss)


@pytest.mark.asyncio
async def test_build_from_real_bundles_does_not_crash() -> None:
    from backend.app.ingest.sources import FixtureSource
    from ml.detectors.d5_mamba import build_sequences_from_company_bundles
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    bundles = []
    for cin in cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is not None:
            bundles.append(b)
    batch = build_sequences_from_company_bundles(bundles)
    assert batch.x.shape[1:] == (MAX_SEQ_LEN, FEATURE_DIM)
    assert all(isinstance(c, str) for c in batch.cins)

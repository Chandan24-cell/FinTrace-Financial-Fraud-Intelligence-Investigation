"""D6 save/load round-trip — Stream 4.1.

The autoencoder + GroupNormaliser + p99 error scale must serialise to a
single .pt file and reconstruct to functional equivalence (same anomaly
scores on the same input). The schema-version field gates stale
artifacts so a layout change doesn't silently miscalibrate the
detector at boot.
"""

from __future__ import annotations

import numpy as np
import pytest

from ml.detectors.d6_combined_ae import D6Artifacts, train_d6


@pytest.fixture
def trained_d6(tmp_path) -> tuple[D6Artifacts, str, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    tab = rng.normal(size=(8, 20)).astype(np.float32)
    graph = rng.uniform(size=(8, 7)).astype(np.float32)
    artifacts = train_d6(tab, graph, epochs=3, seed=0)
    out = tmp_path / "d6.pt"
    artifacts.save(str(out))
    return artifacts, str(out), tab, graph


def test_save_creates_a_non_empty_file(trained_d6):
    _, path, _, _ = trained_d6
    from pathlib import Path
    assert Path(path).exists()
    assert Path(path).stat().st_size > 1024  # autoencoder weights are not tiny


def test_load_reproduces_anomaly_scores_within_float_tolerance(trained_d6):
    original, path, tab, graph = trained_d6
    reloaded = D6Artifacts.load(path)
    a = original.anomaly_scores(tab, graph)
    b = reloaded.anomaly_scores(tab, graph)
    np.testing.assert_allclose(a, b, rtol=1e-5, atol=1e-6)


def test_load_preserves_normaliser_constants(trained_d6):
    original, path, _, _ = trained_d6
    reloaded = D6Artifacts.load(path)
    np.testing.assert_array_equal(original.normaliser.tab_min, reloaded.normaliser.tab_min)
    np.testing.assert_array_equal(original.normaliser.tab_max, reloaded.normaliser.tab_max)
    np.testing.assert_array_equal(original.normaliser.graph_min, reloaded.normaliser.graph_min)
    np.testing.assert_array_equal(original.normaliser.graph_max, reloaded.normaliser.graph_max)
    assert original.error_p99 == reloaded.error_p99


def test_load_refuses_mismatched_schema_version(tmp_path):
    """Bump the version stamp post-save; load must raise loudly so
    analytics_cache's fallback path takes over instead of silently
    miscalibrating."""
    rng = np.random.default_rng(0)
    tab = rng.normal(size=(4, 20)).astype(np.float32)
    graph = rng.uniform(size=(4, 7)).astype(np.float32)
    artifacts = train_d6(tab, graph, epochs=1, seed=0)
    out = tmp_path / "d6.pt"
    artifacts.save(str(out))

    import torch
    blob = torch.load(str(out), map_location="cpu", weights_only=False)
    blob["schema_version"] = blob["schema_version"] + 99
    torch.save(blob, str(out))

    with pytest.raises(ValueError, match="schema"):
        D6Artifacts.load(str(out))


def test_load_works_from_missing_schema_version_field(tmp_path):
    """An artifact written before schema_version existed should also be
    rejected — the field defaults to 0, which never matches the live
    version, so the load fails loudly."""
    rng = np.random.default_rng(0)
    tab = rng.normal(size=(4, 20)).astype(np.float32)
    graph = rng.uniform(size=(4, 7)).astype(np.float32)
    artifacts = train_d6(tab, graph, epochs=1, seed=0)
    out = tmp_path / "d6.pt"
    artifacts.save(str(out))

    import torch
    blob = torch.load(str(out), map_location="cpu", weights_only=False)
    del blob["schema_version"]
    torch.save(blob, str(out))

    with pytest.raises(ValueError, match="schema v0"):
        D6Artifacts.load(str(out))

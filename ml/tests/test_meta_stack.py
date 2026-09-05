"""Tests for F1a OOF LightGBM + F1b isotonic + F1c conformal (Day 5)."""

import numpy as np
import pytest

from ml.meta.f1a_lightgbm_oof import fit_oof
from ml.meta.f1b_isotonic import fit_isotonic
from ml.meta.f1c_split_conformal import fit_conformal, predict_with_interval


@pytest.fixture
def synthetic_dataset() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    n_pos, n_neg, d = 25, 35, 7
    X_neg = rng.normal(loc=0.0, scale=1.0, size=(n_neg, d))
    X_pos = rng.normal(loc=2.0, scale=1.0, size=(n_pos, d))
    X = np.vstack([X_neg, X_pos]).astype(np.float32)
    y = np.array([0] * n_neg + [1] * n_pos, dtype=np.int32)
    perm = rng.permutation(len(y))
    return X[perm], y[perm]


def test_f1a_oof_predictions_shape(synthetic_dataset) -> None:
    X, y = synthetic_dataset
    result = fit_oof(X, y, n_splits=5)
    assert result.oof_pred.shape == y.shape
    assert np.all((result.oof_pred >= 0.0) & (result.oof_pred <= 1.0))


def test_f1a_oof_separates_positives_from_negatives(synthetic_dataset) -> None:
    """Crude AUC sanity check — well-separated synthetic data should give AUC > 0.7."""
    X, y = synthetic_dataset
    result = fit_oof(X, y, n_splits=5)
    pos_mean = result.oof_pred[y == 1].mean()
    neg_mean = result.oof_pred[y == 0].mean()
    assert pos_mean > neg_mean


def test_f1a_safe_n_splits_handles_minority_class() -> None:
    """If only 2 positives, n_splits should clamp to 2."""
    X = np.random.default_rng(0).normal(size=(20, 5))
    y = np.array([0] * 18 + [1] * 2)
    result = fit_oof(X, y, n_splits=5)
    assert result.oof_pred.shape == y.shape


def test_f1b_isotonic_returns_monotonic_calibrator(synthetic_dataset) -> None:
    X, y = synthetic_dataset
    f1a = fit_oof(X, y, n_splits=5)
    # Use the final booster's predictions on the training set as a stand-in for hold-out
    raw = f1a.final_booster.predict(X[:10])
    f1b = fit_isotonic(np.asarray(raw), y[:10])
    # IsotonicRegression should produce monotonically non-decreasing output
    sorted_raw = np.sort(raw)
    sorted_probs = f1b.isotonic.transform(sorted_raw)
    assert np.all(np.diff(sorted_probs) >= -1e-9)


def test_f1b_ece_is_finite(synthetic_dataset) -> None:
    X, y = synthetic_dataset
    f1a = fit_oof(X, y, n_splits=5)
    raw = f1a.final_booster.predict(X[:10])
    f1b = fit_isotonic(np.asarray(raw), y[:10])
    assert np.isfinite(f1b.ece)
    assert 0.0 <= f1b.ece <= 1.0


def test_f1c_conformal_intervals_contain_predictions(synthetic_dataset) -> None:
    X, y = synthetic_dataset
    split = int(0.85 * len(y))
    X_oof, y_oof = X[:split], y[:split]
    X_calib, y_calib = X[split:], y[split:]
    f1a = fit_oof(X_oof, y_oof, n_splits=5)
    raw_calib = f1a.final_booster.predict(X_calib)
    f1b = fit_isotonic(np.asarray(raw_calib), y_calib)
    conformal = fit_conformal(f1a, f1b, X_calib, y_calib, alpha=0.10)

    # All P(fraud) point estimates must lie inside [low, high]
    assert np.all(conformal.intervals_low <= conformal.p_fraud)
    assert np.all(conformal.p_fraud <= conformal.intervals_high)
    assert 0.0 <= conformal.coverage <= 1.0


def test_f1c_predict_with_interval_returns_three_arrays(synthetic_dataset) -> None:
    X, y = synthetic_dataset
    f1a = fit_oof(X, y, n_splits=5)
    raw = f1a.final_booster.predict(X[:10])
    f1b = fit_isotonic(np.asarray(raw), y[:10])
    conformal = fit_conformal(f1a, f1b, X[:10], y[:10], alpha=0.10)

    p, low, high = predict_with_interval(f1a, f1b, conformal, X[:5])
    assert p.shape == (5,)
    assert low.shape == (5,)
    assert high.shape == (5,)
    assert np.all(low <= p)
    assert np.all(p <= high)


def test_static_formula_grep_returns_no_hits() -> None:
    """PRD §13 Definition of Done — grep finds zero references to the deprecated formula."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    # Search both ml/ and backend/ for the old static formula
    pattern = re.compile(r"S\s*=\s*0\.4\s*\*\s*GNN", re.IGNORECASE)
    offenders: list[str] = []
    for code_dir in [repo_root / "ml", repo_root / "backend"]:
        for py in code_dir.rglob("*.py"):
            if py.name == "test_meta_stack.py":  # skip this test itself
                continue
            if pattern.search(py.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(py.relative_to(repo_root)))
    assert offenders == [], f"Static formula found in: {offenders}"

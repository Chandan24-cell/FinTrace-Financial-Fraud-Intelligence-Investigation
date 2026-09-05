"""Tests for F1a + TGN OOF retrain (PRD §9 Phase 2 P2-3, Day 9)."""

from __future__ import annotations

import numpy as np
import pytest

from ml.meta.f1a_with_tgn import (
    TGN_EMBED_PCA_DIM,
    augmented_matrix,
    fit_oof_with_tgn,
)


@pytest.fixture
def small_dataset() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(7)
    n_pos, n_neg, base_dim = 25, 35, 7
    n_entities = 12
    embed_dim = 64

    X_neg = rng.normal(loc=0.0, scale=1.0, size=(n_neg, base_dim))
    X_pos = rng.normal(loc=2.0, scale=1.0, size=(n_pos, base_dim))
    X = np.vstack([X_neg, X_pos]).astype(np.float32)
    y = np.array([0] * n_neg + [1] * n_pos, dtype=np.int32)

    # Entity-level features. Positives map to "risky" entities so the augmentation
    # actually carries signal.
    entity_index = rng.integers(0, n_entities, size=len(y))
    tgn_scores = rng.uniform(0.0, 1.0, size=n_entities).astype(np.float32)
    tgn_embeddings = rng.normal(size=(n_entities, embed_dim)).astype(np.float32)

    perm = rng.permutation(len(y))
    return {
        "X_base": X[perm],
        "y": y[perm],
        "entity_index": entity_index[perm],
        "tgn_scores_per_entity": tgn_scores,
        "tgn_embeddings_per_entity": tgn_embeddings,
    }


def test_augmented_matrix_has_score_plus_pca_columns(small_dataset) -> None:
    X_aug, pca, score_idx, embed_idx = augmented_matrix(
        small_dataset["X_base"],
        entity_index=small_dataset["entity_index"],
        tgn_scores_per_entity=small_dataset["tgn_scores_per_entity"],
        tgn_embeddings_per_entity=small_dataset["tgn_embeddings_per_entity"],
    )
    base_dim = small_dataset["X_base"].shape[1]
    assert X_aug.shape[0] == small_dataset["X_base"].shape[0]
    # 1 score col + up to 32 PCA cols, but n_components clamps to min(n_entities, embed_dim)
    expected_pca = min(TGN_EMBED_PCA_DIM, *small_dataset["tgn_embeddings_per_entity"].shape)
    assert X_aug.shape[1] == base_dim + 1 + expected_pca
    assert score_idx == base_dim
    assert len(embed_idx) == expected_pca
    assert pca.n_components_ == expected_pca


def test_augmented_matrix_broadcasts_per_entity(small_dataset) -> None:
    X_aug, _, score_idx, _ = augmented_matrix(
        small_dataset["X_base"],
        entity_index=small_dataset["entity_index"],
        tgn_scores_per_entity=small_dataset["tgn_scores_per_entity"],
        tgn_embeddings_per_entity=small_dataset["tgn_embeddings_per_entity"],
    )
    scores = small_dataset["tgn_scores_per_entity"]
    for row_i, ent_i in enumerate(small_dataset["entity_index"]):
        assert X_aug[row_i, score_idx] == pytest.approx(scores[ent_i], abs=1e-5)


def test_fit_oof_with_tgn_returns_well_formed_result(small_dataset) -> None:
    result = fit_oof_with_tgn(
        small_dataset["X_base"],
        small_dataset["y"],
        entity_index=small_dataset["entity_index"],
        tgn_scores_per_entity=small_dataset["tgn_scores_per_entity"],
        tgn_embeddings_per_entity=small_dataset["tgn_embeddings_per_entity"],
        n_splits=5,
        seed=42,
    )
    assert result.oof_result.oof_pred.shape == small_dataset["y"].shape
    assert np.all((result.oof_result.oof_pred >= 0.0) & (result.oof_result.oof_pred <= 1.0))
    assert result.base_feature_count == small_dataset["X_base"].shape[1]


def test_fit_oof_with_tgn_separates_positives(small_dataset) -> None:
    """Smoke test: OOF predictions should rank positives higher than negatives."""
    result = fit_oof_with_tgn(
        small_dataset["X_base"],
        small_dataset["y"],
        entity_index=small_dataset["entity_index"],
        tgn_scores_per_entity=small_dataset["tgn_scores_per_entity"],
        tgn_embeddings_per_entity=small_dataset["tgn_embeddings_per_entity"],
        n_splits=5,
    )
    pos_mean = result.oof_result.oof_pred[small_dataset["y"] == 1].mean()
    neg_mean = result.oof_result.oof_pred[small_dataset["y"] == 0].mean()
    assert pos_mean > neg_mean


def test_augmented_matrix_rejects_wrong_shapes(small_dataset) -> None:
    with pytest.raises(ValueError):
        augmented_matrix(
            small_dataset["X_base"].ravel(),  # 1-D — invalid
            entity_index=small_dataset["entity_index"],
            tgn_scores_per_entity=small_dataset["tgn_scores_per_entity"],
            tgn_embeddings_per_entity=small_dataset["tgn_embeddings_per_entity"],
        )
    with pytest.raises(ValueError):
        augmented_matrix(
            small_dataset["X_base"],
            entity_index=small_dataset["entity_index"],
            tgn_scores_per_entity=small_dataset["tgn_scores_per_entity"][:-1],
            tgn_embeddings_per_entity=small_dataset["tgn_embeddings_per_entity"],
        )

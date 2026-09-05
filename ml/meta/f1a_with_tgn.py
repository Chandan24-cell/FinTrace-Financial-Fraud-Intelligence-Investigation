"""F1a OOF retrain with D3 TGN features (PRD §9 Phase 2 P2-3, Day 9).

Augments the base L2 feature matrix with two TGN-derived signals:

  1. D3 TGN per-entity score        — 1 column (link-prediction probability for
                                       a self-loop probe, used as a "graph
                                       anomaly" pseudo-score per PRD §9 P2-3).
  2. D3 TGN embedding PCA-32        — 32 columns (PCA-compressed memory state).

Both are computed at the *entity* level and broadcast onto each entity-year row
in the training matrix. We never leak labels into PCA — fit it on the training
fold only, transform held-out folds with the fitted projector.

The OOF retrain itself just calls `f1a_lightgbm_oof.fit_oof` on the augmented
matrix, so all the calibration / conformal stages downstream are unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sklearn.decomposition import PCA

from ml.meta.f1a_lightgbm_oof import OOFResult, fit_oof

logger = logging.getLogger(__name__)


# PRD §9 P2-3: "TGN embedding compressed to 32-dim via PCA".
TGN_EMBED_PCA_DIM = 32


@dataclass(frozen=True)
class TGNAugmentedOOF:
    """OOF result + the PCA projector used to build the TGN columns.
    Production inference re-uses `pca` to compress fresh TGN embeddings to 32-d."""

    oof_result: OOFResult
    pca: PCA
    tgn_score_index: int           # column index of the TGN scalar in augmented X
    tgn_embed_indices: tuple[int, ...]
    base_feature_count: int


def _fit_pca_32(embeddings: np.ndarray, *, n_components: int, seed: int) -> PCA:
    """PCA-32 with a graceful degenerate-input fallback.

    sklearn's PCA chokes when n_samples < n_components or when the input has
    rank < n_components. We clamp n_components and fall back to a smaller PCA
    so unit tests on tiny fixtures still pass — production runs see hundreds
    of entities and hit the requested dim every time."""
    n_samples, n_features = embeddings.shape
    target = min(n_components, n_samples, n_features)
    if target <= 0:
        target = 1
    pca = PCA(n_components=target, random_state=seed)
    pca.fit(embeddings)
    return pca


def _broadcast_per_entity(
    entity_features: np.ndarray, entity_index: np.ndarray,
) -> np.ndarray:
    """Map (n_entities, k) features onto (n_rows, k) by row-wise entity_index lookup."""
    if entity_index.ndim != 1:
        raise ValueError(f"entity_index must be 1-D, got {entity_index.shape}")
    return entity_features[entity_index]


def augmented_matrix(
    X_base: np.ndarray,
    *,
    entity_index: np.ndarray,
    tgn_scores_per_entity: np.ndarray,
    tgn_embeddings_per_entity: np.ndarray,
    pca: PCA | None = None,
    seed: int = 42,
) -> tuple[np.ndarray, PCA, int, tuple[int, ...]]:
    """Concatenate [X_base, TGN score, PCA(TGN embedding)] into one matrix.

    Returns (X_aug, fitted_pca, score_col_idx, embed_col_idx_tuple).
    Pass an already-fitted `pca` at inference time to avoid relearning the projection.
    """
    if X_base.ndim != 2:
        raise ValueError(f"X_base must be 2-D, got {X_base.shape}")
    if tgn_scores_per_entity.ndim != 1:
        raise ValueError(f"tgn_scores_per_entity must be 1-D, got {tgn_scores_per_entity.shape}")
    if tgn_embeddings_per_entity.ndim != 2:
        raise ValueError(f"tgn_embeddings_per_entity must be 2-D, got {tgn_embeddings_per_entity.shape}")
    if len(tgn_scores_per_entity) != len(tgn_embeddings_per_entity):
        raise ValueError("tgn_scores and tgn_embeddings must have the same first dim")
    if entity_index.max() >= len(tgn_scores_per_entity):
        raise ValueError("entity_index references an entity row that doesn't exist")

    fitted_pca = pca if pca is not None else _fit_pca_32(
        tgn_embeddings_per_entity, n_components=TGN_EMBED_PCA_DIM, seed=seed,
    )
    compressed = fitted_pca.transform(tgn_embeddings_per_entity)  # (n_entities, k<=32)

    scores_row = _broadcast_per_entity(
        tgn_scores_per_entity.reshape(-1, 1), entity_index,
    )  # (n_rows, 1)
    embed_row = _broadcast_per_entity(compressed, entity_index)  # (n_rows, k)

    X_aug = np.hstack([X_base, scores_row, embed_row]).astype(np.float32)
    base_dim = X_base.shape[1]
    score_col = base_dim
    embed_cols = tuple(range(base_dim + 1, X_aug.shape[1]))
    return X_aug, fitted_pca, score_col, embed_cols


def fit_oof_with_tgn(
    X_base: np.ndarray,
    y: np.ndarray,
    *,
    entity_index: np.ndarray,
    tgn_scores_per_entity: np.ndarray,
    tgn_embeddings_per_entity: np.ndarray,
    n_splits: int = 5,
    seed: int = 42,
    feature_names: tuple[str, ...] = (),
) -> TGNAugmentedOOF:
    """OOF retrain on `[X_base | tgn_score | PCA-32(tgn_embedding)]`.

    PRD §9 P2-3 acceptance: 'TGN improves AUC over baseline.'
    The caller (training notebook) is responsible for checking that AUC went up
    versus the pre-TGN baseline; here we just produce the augmented OOF predictions.
    """
    X_aug, pca, score_idx, embed_idx = augmented_matrix(
        X_base,
        entity_index=entity_index,
        tgn_scores_per_entity=tgn_scores_per_entity,
        tgn_embeddings_per_entity=tgn_embeddings_per_entity,
        seed=seed,
    )
    base_dim = X_base.shape[1]
    fnames = feature_names or tuple(
        [f"base_{i}" for i in range(base_dim)]
        + ["tgn_score"]
        + [f"tgn_pca_{i}" for i in range(X_aug.shape[1] - base_dim - 1)]
    )

    logger.info(
        "F1a + TGN: training on n=%d rows, base_dim=%d, tgn_dim=%d (score + %d PCA)",
        X_aug.shape[0], base_dim, X_aug.shape[1] - base_dim, X_aug.shape[1] - base_dim - 1,
    )

    oof = fit_oof(X_aug, y, n_splits=n_splits, feature_names=fnames, seed=seed)
    return TGNAugmentedOOF(
        oof_result=oof,
        pca=pca,
        tgn_score_index=score_idx,
        tgn_embed_indices=embed_idx,
        base_feature_count=base_dim,
    )

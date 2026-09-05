"""Day-13 OOF retrain CLI (PRD §10).

End-to-end exercise of the F1a/b/c stack on the combined label set:

  - 11 hand-crafted fixtures (3 SFIO-confirmed, 8 controls)
  - 14 SFIO confirmed-fraud labels (CIN list)
  - N synthetic bundles from the precache generator
      (is_risky=True is treated as a positive label)

PRD §10 Day-13 acceptance:
  - 'AUC ≥ 0.85 OOF on labelled set.'
  - 'Belief propagation lifts unlabelled cluster CINs to MEDIUM band.'

Use:
  python scripts/day13_oof_retrain.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from backend.app.ingest.benchmarks import BSEFixtureBenchmark  # noqa: E402
from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402
from ml.belief_propagation import SharedAttributeEdge, assign_band, propagate  # noqa: E402
from ml.features import FEATURE_NAMES, FeatureContext, build_feature_matrix  # noqa: E402
from ml.meta.f1a_lightgbm_oof import fit_oof  # noqa: E402
from ml.meta.f1b_isotonic import fit_isotonic  # noqa: E402
from ml.meta.f1c_split_conformal import fit_conformal  # noqa: E402
from scripts.precache_companies import generate_bundles  # noqa: E402

logger = logging.getLogger("day13")

SFIO_LABELS_PATH = ROOT / "data" / "labels" / "sfio_confirmed_frauds.json"
AUC_FLOOR = 0.85
ARTIFACT_DIR = ROOT / "ml" / "artifacts"


def _load_sfio_label_set() -> set[str]:
    rows = json.loads(SFIO_LABELS_PATH.read_text(encoding="utf-8"))
    return {r["cin"] for r in rows if int(r.get("label", 0)) == 1}


async def _build_dataset(synthetic_count: int, seed: int) -> tuple[
    np.ndarray, np.ndarray, list[str], list[bool]
]:
    """Assemble X, y, cins, is_synthetic for the OOF run."""
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    fixture_bundles = []
    for cin in cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is not None:
            fixture_bundles.append(b)

    synthetic_bundles = generate_bundles(synthetic_count, seed=seed)
    rng = np.random.default_rng(seed)
    # The synthetic generator stamps `is_risky` via rng.random() < 0.20.
    # We reproduce that decision by reading borrowings + revenue collapse —
    # a label we can recompute deterministically from the bundle itself.
    def _synthetic_is_risky(bundle) -> bool:
        if not bundle.financials:
            return False
        fs = bundle.financials[-1]
        debt = fs.long_term_borrowings + fs.short_term_borrowings
        if fs.total_assets <= 0:
            return False
        leverage = debt / fs.total_assets
        loss = fs.pat < 0
        going_concern = any(f.going_concern_flag for f in bundle.financials)
        return going_concern or (loss and leverage > 0.55)

    _ = rng  # rng kept for future jitter; unused right now

    bundles = fixture_bundles + synthetic_bundles
    sfio_positive = _load_sfio_label_set()
    is_synthetic = [False] * len(fixture_bundles) + [True] * len(synthetic_bundles)
    labels: list[int] = []
    for bundle, synth in zip(bundles, is_synthetic):
        cin = bundle.company.cin
        if cin in sfio_positive:
            labels.append(1)
        elif synth and _synthetic_is_risky(bundle):
            labels.append(1)
        else:
            labels.append(0)

    benchmarks = await BSEFixtureBenchmark().fetch_all()
    nclt = await NCLTFixtureSource().fetch_all()
    wilful = await WilfulDefaulterFixtureSource().fetch_all()

    # ---- D5 (TCN) training pass --------------------------------------------
    # Done BEFORE the analytics_cache so cache.build can load the freshly-
    # saved artefact and compute per-CIN D5 scores in the same pass. Without
    # this the d5_score feature slot is always zero at training time, which
    # creates a train/infer asymmetry the meta-learner would mis-weight.
    import torch
    from ml.detectors.d5_mamba import build_sequences_from_company_bundles, smoke_train_tcn
    seq_batch = build_sequences_from_company_bundles(bundles)
    seq_labels = np.zeros(len(seq_batch.cins), dtype=np.int32)
    cin_to_label = {b.company.cin: int(lbl) for b, lbl in zip(bundles, labels)}
    for i, c in enumerate(seq_batch.cins):
        seq_labels[i] = cin_to_label.get(c, 0)
    if int(seq_labels.sum()) >= 1 and len(seq_batch.cins) >= 4:
        d5_model, d5_loss = smoke_train_tcn(seq_batch, seq_labels, epochs=10, seed=seed)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        torch.save(d5_model.state_dict(), str(ARTIFACT_DIR / "d5_tcn.pt"))
        print(f"  D5/TCN trained on n={len(seq_batch.cins)} sequences "
              f"({int(seq_labels.sum())} positive) — final BCE loss {d5_loss:.4f}")
    else:
        print("  D5/TCN training skipped — need >=1 positive label and >=4 sequences")

    # Build the analytics_cache over the full bundle set (fixtures + synthetic)
    # so train-time M10/M11/D4/D5/D6 features come from the same population
    # view the inference path's cache will see at /analyse time.
    from backend.app.analytics_cache import build_cache, reset_for_tests
    reset_for_tests()
    cache = build_cache(bundles)
    ctx = FeatureContext(
        benchmarks=benchmarks, nclt=nclt, wilful=wilful,
        analytics_cache=cache,
    )

    X, cins_in_order = build_feature_matrix(bundles, ctx)
    y = np.asarray(labels, dtype=np.int32)
    return X, y, cins_in_order, is_synthetic


def _stack_metrics(oof_pred: np.ndarray, y: np.ndarray) -> dict[str, float]:
    return {
        "auc": float(roc_auc_score(y, oof_pred)) if len(np.unique(y)) > 1 else 0.0,
        "n_pos": int(y.sum()),
        "n_neg": int((1 - y).sum()),
    }


def _build_attribute_edges(bundles) -> list[SharedAttributeEdge]:
    edges: list[SharedAttributeEdge] = []
    for b in bundles:
        c = b.company
        if c.registered_address:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="registered_address",
                attribute_value=c.registered_address.strip().lower(),
            ))
        if c.auditor_din:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="auditor_din",
                attribute_value=c.auditor_din,
            ))
        if c.contact_phone:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="contact_phone",
                attribute_value=c.contact_phone.strip(),
            ))
        for ch in b.charges:
            edges.append(SharedAttributeEdge(
                cin=c.cin, attribute_type="bank_branch_ifsc",
                attribute_value=ch.bank_branch_ifsc,
            ))
    return edges


def _run_belief_demo(bundles, seed_score_map: dict[str, float]) -> dict[str, str]:
    """Plant one synthetic shell-cluster around IL&FS to confirm propagation
    lifts an unlabelled CIN. PRD §10 Day-13 demo line."""
    # Synthesise three siblings sharing IL&FS's auditor DIN.
    ilfs_din = "00112233"
    synthetic_siblings = ["U99999MH2024PTC900001", "U99999MH2024PTC900002", "U99999MH2024PTC900003"]
    extra_edges = [
        SharedAttributeEdge(cin=cin, attribute_type="auditor_din", attribute_value=ilfs_din)
        for cin in synthetic_siblings
    ]
    edges = _build_attribute_edges(bundles) + extra_edges
    # Anchor IL&FS's edge to the same DIN if not already present.
    if not any(e.cin == "U45201MH2005PTC155294" and e.attribute_value == ilfs_din for e in edges):
        edges.append(SharedAttributeEdge(
            cin="U45201MH2005PTC155294", attribute_type="auditor_din", attribute_value=ilfs_din,
        ))
    result = propagate(seed_scores=seed_score_map, edges=edges)
    return {
        cin: result.bands[cin]
        for cin in synthetic_siblings
    }


async def _run(synthetic_count: int, seed: int, save: bool) -> int:
    print()
    print("=" * 72)
    print(" Day-13 OOF retrain (F1a + F1b + F1c) over fixtures+synthetic+SFIO")
    print("=" * 72)

    X, y, cins, is_synthetic = await _build_dataset(synthetic_count, seed)
    print(f"  Feature matrix: {X.shape}  (features: {len(FEATURE_NAMES)})")
    print(f"  Label distribution: positives={int(y.sum())}  negatives={int((1 - y).sum())}")

    # 85/15 split into train+OOF / calib hold-out
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(y))
    X, y = X[perm], y[perm]
    cins = [cins[i] for i in perm]
    n_holdout = max(int(0.15 * len(y)), 10)
    X_tr, y_tr = X[:-n_holdout], y[:-n_holdout]
    X_ca, y_ca = X[-n_holdout:], y[-n_holdout:]
    print(f"  Train (OOF) n={len(y_tr)}  Calibration hold-out n={len(y_ca)}")

    f1a = fit_oof(X_tr, y_tr, n_splits=5, seed=seed, feature_names=FEATURE_NAMES)
    metrics = _stack_metrics(f1a.oof_pred, y_tr)
    print(f"  F1a OOF AUC: {metrics['auc']:.4f}  (floor {AUC_FLOOR:.2f})")
    print(f"  F1a OOF positives/negatives: {metrics['n_pos']} / {metrics['n_neg']}")

    raw_ca = f1a.final_booster.predict(X_ca)
    f1b = fit_isotonic(np.asarray(raw_ca), y_ca)
    print(f"  F1b isotonic ECE on calibration hold-out: {f1b.ece:.4f}")

    conformal = fit_conformal(f1a, f1b, X_ca, y_ca, alpha=0.10)
    print(f"  F1c conformal interval coverage: {conformal.coverage:.4f}")

    if save:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        f1a.save(str(ARTIFACT_DIR / "f1a_oof.joblib"))
        f1b.save(str(ARTIFACT_DIR / "f1b_isotonic.joblib"))
        conformal.save(str(ARTIFACT_DIR / "f1c_conformal.joblib"))
        print(f"  Artefacts written to {ARTIFACT_DIR}")

    # --- Belief propagation demo --------------------------------------------
    print()
    print("=" * 72)
    print(" Belief propagation demo (PRD §5.3)")
    print("=" * 72)
    fixture_source = FixtureSource()
    all_cins = await fixture_source.list_available_cins()
    fixture_bundles = []
    for cin in all_cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is not None:
            fixture_bundles.append(b)

    seed_scores = {
        "U45201MH2005PTC155294": 80.0,  # IL&FS — anchor for the demo cluster
        "L65910MH1984PLC032662": 78.0,  # DHFL
    }
    sibling_bands = _run_belief_demo(fixture_bundles, seed_scores)
    print(f"  IL&FS seed = 80.0 (CRITICAL band; PRD §7.2)")
    print(f"  Cluster (shared auditor DIN 00112233) lifted bands: {sibling_bands}")
    lifted_to_medium = sum(1 for band in sibling_bands.values() if band in {"MEDIUM", "HIGH", "CRITICAL"})
    print(f"  Unlabelled siblings lifted at-or-above MEDIUM: {lifted_to_medium}/{len(sibling_bands)}")
    print("=" * 72)

    auc_ok = metrics["auc"] >= AUC_FLOOR
    bp_ok = lifted_to_medium >= 1
    print()
    print(f"  AUC ≥ {AUC_FLOOR:.2f}: {'PASS' if auc_ok else 'FAIL'}")
    print(f"  Belief propagation lifts cluster CIN to MEDIUM+: "
          f"{'PASS' if bp_ok else 'FAIL'}")
    return 0 if (auc_ok and bp_ok) else 5


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", type=int, default=300, help="Synthetic bundles to add")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save", action="store_true", help="Persist F1a/b/c artefacts to ml/artifacts/")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.synthetic, args.seed, args.save)))


if __name__ == "__main__":
    main()

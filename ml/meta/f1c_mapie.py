"""Backwards-compatibility shim — Stream 4.8 renamed this file to
`f1c_split_conformal.py` so the filename matches the implementation
(split-conformal, not MAPIE). However the F1c artefact on disk at
`ml/artifacts/f1c_conformal.joblib` was pickled when the class lived
here, so joblib.load() tries to import `ml.meta.f1c_mapie` and fails
with ModuleNotFoundError — which silently collapses every
`p_fraud_calibrated` / `p_fraud_interval` to null at /analyse time
(diagnosed 2026-05-22 against the SFIO eval audit).

This module re-exports the relocated symbols under their original
import path so joblib can resolve the pickled class without us
needing to re-pickle every artefact. Once F1c is retrained the
ConformalArtifacts inside will reference `f1c_split_conformal` and
this shim becomes dead code — but it's free to leave for old
artefact compatibility.
"""

from ml.meta.f1c_split_conformal import (  # noqa: F401
    ConformalArtifacts,
    fit_conformal,
    predict_with_interval,
)

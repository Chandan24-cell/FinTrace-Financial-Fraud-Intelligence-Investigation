"""Offline training scripts for the ML detector stack.

These modules are runnable as scripts (`python -m ml.training.train_d6 ...`)
and produce artifacts in `ml/artifacts/` that the live backend loads at
boot via `backend/app/analytics_cache.py`. Each script is idempotent,
deterministic given its seed, and prints a one-line summary on exit so
CI can grep for success.
"""

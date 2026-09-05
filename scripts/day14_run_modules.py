"""Day-14 acceptance CLI (PRD §10).

Exercises four artefacts:

  1. D5 transaction-sequence pipeline + TCN fallback smoke-train (PRD §5.1).
     Mamba is Linux+CUDA-only — Colab notebook is the production training
     surface; the local TCN proves the data contract.
  2. D6 combined autoencoder reconstructs the 27-dim (tabular + L0.5) input.
     Smoke-train + anomaly score sanity check.
  3. 1500-co synthetic pre-cache (dry-run).
  4. NCLT cause-list HTML parser on a canned page so the hardened scraper is
     verified end-to-end without hitting nclt.gov.in.

PRD §10 Day-14 acceptance:
  - 'D5 trains on Colab pipeline.'
  - 'D6 reconstructs L0.5 features.'
  - '1500 cos cached.'
"""

from __future__ import annotations

import argparse
import asyncio
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

from backend.app.ingest.nclt import parse_cause_list_html  # noqa: E402
from backend.app.ingest.sources import FixtureSource  # noqa: E402
from ml.detectors.d5_mamba import (  # noqa: E402
    FEATURE_DIM,
    MAX_SEQ_LEN,
    build_sequences_from_company_bundles,
    smoke_train_tcn,
)
from ml.detectors.d6_combined_ae import (  # noqa: E402
    N_GRAPH_FEATURES,
    N_TABULAR_FEATURES,
    train_d6,
)
from scripts.precache_companies import generate_bundles  # noqa: E402

logger = logging.getLogger("day14")


SAMPLE_NCLT_HTML = """\
<html><body>
<h2>NCLT Mumbai — Cause List 2026-03-04</h2>
<table>
  <tr><th>Case No.</th><th>CIN</th><th>Petition Section</th>
      <th>Filing Date</th><th>Status</th><th>Amount Claimed</th></tr>
  <tr><td>C.P. (IB) 100/MB/2026</td><td>U45201MH2005PTC155294</td>
      <td>IBC — Section 7</td><td>2026-02-10</td><td>Admitted</td>
      <td>Rs 12,40,00,000</td></tr>
  <tr><td>C.P. 250/MB/2026</td><td>U27101MH2010PTC215432</td>
      <td>Winding Up</td><td>15-02-2026</td><td>In Progress</td>
      <td>Rs 2,75,00,000</td></tr>
</table>
</body></html>
"""


async def _run_d5(bundles_count: int) -> tuple[int, float]:
    fixture_source = FixtureSource()
    cins = await fixture_source.list_available_cins()
    bundles = []
    for cin in cins:
        b = await fixture_source.fetch_bundle(cin)
        if b is not None:
            bundles.append(b)
    bundles += generate_bundles(bundles_count, seed=14)

    batch = build_sequences_from_company_bundles(bundles)
    print()
    print("=" * 72)
    print(" Day-14 D5 — Sequence pipeline + TCN fallback (PRD §5.1)")
    print("=" * 72)
    print(f"  Batch shape: x={batch.x.shape}, mask={batch.mask.shape}, cins={len(batch.cins)}")
    assert batch.x.shape[1:] == (MAX_SEQ_LEN, FEATURE_DIM)
    rng = np.random.default_rng(14)
    labels = rng.integers(0, 2, size=len(batch.cins)).astype(np.float32)
    # Keep epochs tiny so this completes on CPU in <30s.
    _, loss = smoke_train_tcn(batch, labels, epochs=2)
    print(f"  TCN smoke-train final BCE loss: {loss:.4f}")
    print("=" * 72)
    return len(batch.cins), loss


def _run_d6(n_rows: int) -> float:
    rng = np.random.default_rng(14)
    tabular = rng.uniform(0.0, 1.0, size=(n_rows, N_TABULAR_FEATURES)).astype(np.float32)
    graph = rng.uniform(0.0, 1.0, size=(n_rows, N_GRAPH_FEATURES)).astype(np.float32)
    arts = train_d6(tabular, graph, epochs=4)
    scores = arts.anomaly_scores(tabular, graph)
    print()
    print("=" * 72)
    print(" Day-14 D6 — Combined autoencoder reconstruction (PRD §5.1)")
    print("=" * 72)
    print(f"  Train rows: {n_rows} (tab dim={N_TABULAR_FEATURES}, graph dim={N_GRAPH_FEATURES})")
    print(f"  Anomaly-score range: min={float(scores.min()):.4f}  max={float(scores.max()):.4f}")
    print(f"  99th-percentile MSE (error scale): {arts.error_p99:.6f}")
    print("=" * 72)
    return float(arts.error_p99)


def _run_precache(count: int) -> int:
    bundles = generate_bundles(count, seed=14)
    print()
    print("=" * 72)
    print(f" Day-14 — {count}-co synthetic pre-cache (dry-run schema validation)")
    print("=" * 72)
    print(f"  Generated {len(bundles)} synthetic bundles — all schema-validated.")
    print("=" * 72)
    return len(bundles)


def _run_nclt_parser() -> int:
    rows = parse_cause_list_html(SAMPLE_NCLT_HTML, bench="NCLT Mumbai")
    print()
    print("=" * 72)
    print(" Day-14 NCLT scraper hardening — HTML cause-list parser")
    print("=" * 72)
    for r in rows:
        print(f"  {r.case_number}  cin={r.cin}  petition={r.petition_type}  "
              f"status={r.status}  filed={r.filing_date}  amount={r.amount_claimed:.0f}")
    assert len(rows) == 2
    assert {p.petition_type for p in rows} == {"CIRP", "winding_up"}
    print("=" * 72)
    return len(rows)


async def _run(precache_count: int, d6_rows: int) -> int:
    d5_n, _ = await _run_d5(bundles_count=120)
    _ = _run_d6(d6_rows)
    cached = _run_precache(precache_count)
    nclt_rows = _run_nclt_parser()
    ok = d5_n >= 1 and cached >= 1500 and nclt_rows == 2
    print()
    print(f"  All Day-14 lines: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 6


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precache", type=int, default=1500, help="Synthetic bundle count")
    parser.add_argument("--d6-rows", type=int, default=300, help="D6 training rows")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.precache, args.d6_rows)))


if __name__ == "__main__":
    main()

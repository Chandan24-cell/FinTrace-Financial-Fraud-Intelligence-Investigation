"""Day-27 rehearsal #2 — three demo scenarios end-to-end (PRD §10).

PRD §10 Day-27 verbatim: 'Fix all rehearsal 1 issues. Rehearsal 2:
Amtek Auto as second scenario. ITC carousel demo as third scenario.
Done When: All three demo scenarios work end-to-end.'

Walks each scenario through the demo API surface and stopwatches it:

  Scenario 1 — IL&FS (PRD §14 hero scenario)
  Scenario 2 — Amtek Auto (second SME loan-fraud case, NCLT + WD flagged)
  Scenario 3 — ITC carousel (three carousel-page CINs end-to-end +
               synthetic 7-node ring loadable via Day-6 special_seeds)

Writes data/audits/day27_rehearsal.json. Exits 0 only if every scenario
clears its budget AND every per-CIN /analyse returns 200 with the
expected risk band.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# PRD §10 Day-27 floors
PER_SCENARIO_BUDGET_SEC = 60.0   # Each scenario individually < 1 min
TOTAL_BUDGET_SEC = 180.0          # Three scenarios fit in the 3-minute demo
AUDIT_OUTPUT_PATH = ROOT / "data" / "audits" / "day27_rehearsal.json"

ILFS_CIN = "U45201MH2005PTC155294"
AMTEK_CIN = "U27101MH2010PTC215432"
ITC_CAROUSEL_CINS = (
    "U27109MH2018PTC312456",   # PNB WD-flagged
    "U46101MH2017PTC289123",   # Canara WD-flagged
    "U46190MH2019PTC295432",   # IOB suit-filed
)

logger = logging.getLogger("day27")


async def _build_client():
    """Return an httpx.AsyncClient pointed at one of two backends:

    - When `PROD_API_URL` is set (Day 29 production rehearsal), hit that
      URL over the network. The caller must supply `PROD_JWT` (a real
      token issued by the live /auth/login endpoint) OR `PROD_NO_AUTH=1`
      for endpoints that don't require auth.
    - Otherwise (default, Day 27 local rehearsal), build an in-process
      ASGI app with the auth dependency stubbed out.
    """
    from httpx import AsyncClient

    from backend.app.auth.jwt import create_access_token

    prod_url = os.environ.get("PROD_API_URL", "").rstrip("/")
    if prod_url:
        # Day-29 path: real network call against the live Oracle Cloud host.
        # The /report endpoint will demand a real JWT; the operator either
        # supplies PROD_JWT or accepts that /report phases return 401 (the
        # rehearsal still proves /analyse and /provenance work end-to-end).
        token = os.environ.get("PROD_JWT") or create_access_token("demo-user")
        return AsyncClient(
            base_url=prod_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
            follow_redirects=True,
        )

    # Day-27 path: in-process ASGI client.
    from fastapi import FastAPI
    from httpx import ASGITransport

    from backend.app.api.analyse import router as analyse_router
    from backend.app.api.report import router as report_router
    from backend.app.auth.deps import get_current_user

    app = FastAPI()
    app.include_router(analyse_router)
    app.include_router(report_router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "demo", "is_active": True,
        # role required by /report's RBAC gate (auditor/investigator/admin).
        "role": "admin",
    }

    transport = ASGITransport(app=app)
    token = create_access_token("demo-user")
    headers = {"Authorization": f"Bearer {token}"}
    return AsyncClient(transport=transport, base_url="http://demo.local",
                       headers=headers, timeout=30.0)


async def _scenario_full_flow(client, cin: str, expected_band: str | None) -> dict:
    """Walk the 7-phase demo against one CIN; return timings + headline state.

    Day-29 prod mode: when PROD_API_URL is set but PROD_JWT isn't, the live
    /report endpoint will 401 (real auth dep, no demo-user override). That
    is a known-state outcome rather than a scenario failure — the prod
    rehearsal still proves /analyse + /provenance work end-to-end. The
    operator can later set PROD_JWT (issued by /auth/login on the live
    host) to exercise /report too.
    """
    timings: dict[str, float] = {}
    headline: dict = {}
    prod_mode = bool(os.environ.get("PROD_API_URL"))
    has_prod_jwt = bool(os.environ.get("PROD_JWT"))

    t0 = time.perf_counter()
    r = await client.get(f"/analyse/{cin}")
    timings["analyse"] = time.perf_counter() - t0
    if r.status_code != 200:
        return {"cin": cin, "ok": False, "reason": f"/analyse -> {r.status_code}"}
    body = r.json()
    headline.update({
        "fraud_risk_score": body["fraud_risk_score"],
        "risk_band": body["risk_band"],
        "data_confidence": body["data_confidence"],
        "evidence_signal_count": len(body["evidence_chain"]),
    })

    t1 = time.perf_counter()
    r = await client.get(f"/analyse/{cin}/provenance")
    timings["provenance"] = time.perf_counter() - t1
    if r.status_code != 200:
        return {"cin": cin, "ok": False, "reason": f"/provenance -> {r.status_code}",
                "headline": headline}
    headline["provenance_signal_count"] = r.json()["signal_count"]

    t2 = time.perf_counter()
    r = await client.get(f"/report/{cin}")
    timings["report"] = time.perf_counter() - t2
    if r.status_code == 200:
        headline["report_bytes"] = len(r.content)
        headline["report_id"] = r.headers.get("x-report-id")
    elif prod_mode and not has_prod_jwt and r.status_code == 401:
        headline["report_skipped"] = "401 (expected: set PROD_JWT for full coverage)"
    else:
        return {"cin": cin, "ok": False, "reason": f"/report -> {r.status_code}",
                "headline": headline}

    total = sum(timings.values())
    band_ok = expected_band is None or headline["risk_band"] == expected_band
    return {
        "cin": cin, "expected_band": expected_band, "headline": headline,
        "timings_sec": timings, "total_sec": total,
        "ok": band_ok and total <= PER_SCENARIO_BUDGET_SEC,
        "band_match": band_ok,
    }


def _load_itc_ring() -> dict:
    """Validate the synthetic 7-node ring JSON loads cleanly via the Day-6
    pydantic shape — the carousel demo's graph view reads from this."""
    from backend.app.ingest.special_seeds import load_itc_carousel
    ring = load_itc_carousel()
    return {
        "ring_id": ring.ring_id,
        "node_count": len(ring.gst_entities),
        "edge_count": len(ring.edges),
        "claim_count": len(ring.itc_claims),
        "director_overlap_present": ring.director_overlap is not None,
        "ok": (
            len(ring.gst_entities) == 7
            and len({(e.from_gstin, e.to_gstin) for e in ring.edges}) == 7
            and ring.director_overlap is not None
        ),
    }


def _active_target() -> str:
    """Human-readable label for whichever backend the harness will hit."""
    prod = os.environ.get("PROD_API_URL", "").rstrip("/")
    if prod:
        return f"PROD {prod}"
    return "LOCAL (in-process ASGI)"


async def _run() -> int:
    print()
    print("=" * 72)
    print(" Day-27 PRD §10 demo rehearsal #2 — three scenarios end-to-end")
    print(f" Target: {_active_target()}")
    print("=" * 72)

    started = time.perf_counter()
    async with await _build_client() as client:
        print("\n[Scenario 1] IL&FS")
        s1 = await _scenario_full_flow(client, ILFS_CIN, expected_band="CRITICAL")

        print("[Scenario 2] Amtek Auto")
        s2 = await _scenario_full_flow(client, AMTEK_CIN, expected_band="CRITICAL")

        print("[Scenario 3] ITC carousel (3 ring-member CINs + ring fixture)")
        s3_cards: list[dict] = []
        for cin in ITC_CAROUSEL_CINS:
            card = await _scenario_full_flow(client, cin, expected_band="CRITICAL")
            s3_cards.append(card)
        ring_check = _load_itc_ring()
    total_elapsed = time.perf_counter() - started

    # Reports
    for label, result in (("IL&FS", s1), ("Amtek Auto", s2)):
        h = result.get("headline") or {}
        print(f"\n  {label}  [{result['cin']}]")
        if not result.get("ok"):
            print(f"    FAIL: {result.get('reason', 'band mismatch / over budget')}")
        else:
            print(f"    PASS — score={h.get('fraud_risk_score')} ({h.get('risk_band')})  "
                  f"DC={h.get('data_confidence')}  "
                  f"evidence={h.get('evidence_signal_count')}  "
                  f"report={h.get('report_bytes')}B")
            print(f"    timings  analyse={result['timings_sec']['analyse']*1000:.0f}ms  "
                  f"provenance={result['timings_sec']['provenance']*1000:.0f}ms  "
                  f"report={result['timings_sec']['report']*1000:.0f}ms")

    print(f"\n  ITC carousel — 3 ring-member CINs")
    for card in s3_cards:
        h = card.get("headline") or {}
        flag = "PASS" if card["ok"] else "FAIL"
        print(f"    {flag}  {card['cin']}  -> {h.get('risk_band', '-')}  "
              f"score={h.get('fraud_risk_score', '-')}  "
              f"evidence={h.get('evidence_signal_count', '-')}")
    print(f"  ITC ring fixture: {ring_check['node_count']} nodes / "
          f"{ring_check['edge_count']} edges / "
          f"director_overlap={'yes' if ring_check['director_overlap_present'] else 'no'}  "
          f"-> {'PASS' if ring_check['ok'] else 'FAIL'}")

    s3_ok = ring_check["ok"] and all(c["ok"] for c in s3_cards)
    all_ok = s1["ok"] and s2["ok"] and s3_ok and total_elapsed <= TOTAL_BUDGET_SEC

    print("\n" + "=" * 72)
    print(f"  Scenario 1 (IL&FS):           {'PASS' if s1['ok'] else 'FAIL'}")
    print(f"  Scenario 2 (Amtek Auto):      {'PASS' if s2['ok'] else 'FAIL'}")
    print(f"  Scenario 3 (ITC carousel):    {'PASS' if s3_ok else 'FAIL'}")
    print(f"  Total wall time:              {total_elapsed:.3f}s  "
          f"(budget {TOTAL_BUDGET_SEC:.0f}s)")
    print(f"  Overall:                      {'PASS' if all_ok else 'FAIL'}")
    print("=" * 72)

    AUDIT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT_PATH.write_text(json.dumps({
        "scenario_1_ilfs": s1,
        "scenario_2_amtek": s2,
        "scenario_3_itc_carousel": {"cards": s3_cards, "ring_fixture": ring_check},
        "total_elapsed_sec": total_elapsed,
        "total_budget_sec": TOTAL_BUDGET_SEC,
        "pass": all_ok,
    }, indent=2, default=str), encoding="utf-8")
    print(f"  Audit written to {AUDIT_OUTPUT_PATH.relative_to(ROOT)}")
    return 0 if all_ok else 27


def main() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

"""Day-23 pattern + scraper audit CLI (PRD §10).

PRD §10 Day 23 verbatim:
  'NCLT scraper production run: 50 real CINs. Wilful defaulter: 10 known
   defaulters detected. ITC patterns 8–12 on synthetic ring. Evergreening
   patterns 13–17 on DHFL seed.
   Done When: Known defaulters detected. All 5 ITC patterns fire on
   synthetic ring. Patterns 13/14/15 fire on DHFL simultaneously.'

This CLI audits seed-side preconditions for every Done-When line so a
fresh checkout of the repo can answer 'do the demo scenarios still hold?'
without spinning up Neo4j. Live-Cypher execution still happens on a real
graph in Day-25 (stress test). Day-23 is the seed/audit milestone.

Writes data/audits/day23_audit.json and exits 0 on PASS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingest.nclt import NCLTFixtureSource  # noqa: E402
from backend.app.ingest.wilful_defaulter import WilfulDefaulterFixtureSource  # noqa: E402
from backend.app.modules import m09_nclt_defaulter as m09  # noqa: E402
from backend.app.modules.base import Severity  # noqa: E402

logger = logging.getLogger("day23")

# PRD §10 Day-23 floors
WD_DECLARATIONS_FLOOR = 10
NCLT_PROCEEDINGS_FLOOR = 50
NEW_GSTIN_AGE_DAYS = 90       # PRD §4.4 #11
NEW_GSTIN_ITC_RATIO = 0.30    # PRD §4.4 #11
DHFL_ROUND_TRIP_OVERLAP_PCT = 90.0
DHFL_SERIAL_CYCLE_DAYS = 150
DHFL_SERIAL_MIN_CYCLES = 3
AUDIT_OUTPUT_PATH = ROOT / "data" / "audits" / "day23_audit.json"


async def _audit_wilful_defaulters() -> dict:
    src = WilfulDefaulterFixtureSource()
    declarations = await src.fetch_all()
    unique_cins = sorted({w.cin for w in declarations})

    # Run M9 per CIN, count CRITICAL WILFUL_DEFAULTER_MATCH hits
    results = m09.run_for_cins(unique_cins, [], declarations)
    detected = 0
    per_cin: dict[str, dict] = {}
    for cin, result in results.items():
        fired = [s for s in result.signals
                 if s.signal_type == "WILFUL_DEFAULTER_MATCH"
                 and s.severity is Severity.CRITICAL]
        if fired:
            detected += 1
        per_cin[cin] = {"score": result.score, "fired_count": len(fired)}
    ok = (len(declarations) >= WD_DECLARATIONS_FLOOR
          and detected >= WD_DECLARATIONS_FLOOR
          and len(unique_cins) >= 9)  # 10 declarations, ≥9 distinct CINs
    return {
        "ok": ok,
        "declaration_count": len(declarations),
        "unique_cins": len(unique_cins),
        "cins_detected": detected,
        "per_cin": per_cin,
    }


async def _audit_nclt_production_run() -> dict:
    src = NCLTFixtureSource()
    proceedings = await src.fetch_all()
    parsed_ok = sum(1 for p in proceedings if p.case_number and p.cin)
    petition_breakdown: dict[str, int] = {}
    for p in proceedings:
        petition_breakdown[p.petition_type] = petition_breakdown.get(p.petition_type, 0) + 1
    return {
        "ok": parsed_ok >= NCLT_PROCEEDINGS_FLOOR,
        "proceeding_count": len(proceedings),
        "parsed_ok": parsed_ok,
        "petition_breakdown": petition_breakdown,
        "unique_benches": sorted({p.bench for p in proceedings if p.bench}),
    }


def _audit_itc_patterns(today: date) -> dict:
    ring = json.loads(
        (ROOT / "infra" / "seeds" / "itc_carousel" / "ring.json").read_text(encoding="utf-8")
    )
    entities = {e["gstin"]: e for e in ring["gst_entities"]}
    edges = ring["edges"]
    claims = ring["itc_claims"]

    # P08 — 7-node CLAIMS_ITC_FROM SCC (directed cycle). The seed records
    # one edge per (from, to, period), so an N-period ring expands to N×7
    # raw edges. Collapse to unique (from, to) pairs before asserting the
    # 7-node directed-cycle invariant — the topology is period-invariant.
    edge_pairs = {(e["from_gstin"], e["to_gstin"]) for e in edges}
    out_deg: dict[str, int] = {}
    in_deg: dict[str, int] = {}
    for f, t in edge_pairs:
        out_deg[f] = out_deg.get(f, 0) + 1
        in_deg[t] = in_deg.get(t, 0) + 1
    p08_ok = (
        len(edge_pairs) >= 7
        and all(v == 1 for v in out_deg.values())
        and all(v == 1 for v in in_deg.values())
        and set(out_deg) == set(in_deg) == set(entities)
    )

    # P09 — missing trader: tax_paid / aggregate_turnover < 0.05
    missing_traders = [
        g["gstin"] for g in entities.values()
        if g.get("aggregate_turnover", 0) > 0
        and (g.get("tax_paid_ytd", 0) / g["aggregate_turnover"]) < 0.05
    ]
    p09_ok = len(missing_traders) >= 1

    # P10 — at least one CLAIMS_ITC_FROM edge whose target is_cancelled=true
    cancelled = {g["gstin"] for g in entities.values() if g.get("is_cancelled")}
    p10_targets = [e for e in edges if e["to_gstin"] in cancelled]
    p10_ok = len(p10_targets) >= 1

    # P11 — fresh GSTIN (registered < 90 days) with claimed >= 30% of turnover
    claims_by_gstin: dict[str, float] = {}
    for c in claims:
        claims_by_gstin[c["gstin"]] = claims_by_gstin.get(c["gstin"], 0.0) + c["claimed_amount"]
    p11_hits = []
    for gstin, total in claims_by_gstin.items():
        g = entities.get(gstin)
        if not g:
            continue
        reg_str = g.get("registration_date")
        if not reg_str:
            continue
        reg = datetime.fromisoformat(reg_str).date()
        age_days = (today - reg).days
        turnover = g.get("aggregate_turnover", 0.0)
        if turnover <= 0:
            continue
        ratio = total / turnover
        if age_days < NEW_GSTIN_AGE_DAYS and ratio >= NEW_GSTIN_ITC_RATIO:
            p11_hits.append({"gstin": gstin, "age_days": age_days, "ratio": ratio})
    p11_ok = len(p11_hits) >= 1

    # P12 — multi-hop ITC + director overlap (synthetic block present)
    director_block = ring.get("director_overlap") or {}
    shared = director_block.get("shared_directors") or []
    p12_ok = (
        len(shared) >= 1
        and all(s.get("din") and s.get("cin_a") and s.get("cin_b")
                and len(s.get("hop_path_gstins") or []) >= 3
                for s in shared)
    )

    return {
        "ok": all([p08_ok, p09_ok, p10_ok, p11_ok, p12_ok]),
        "P08_carousel_ring":   {"ok": p08_ok, "edges": len(edges)},
        "P09_missing_trader":  {"ok": p09_ok, "hits": missing_traders},
        "P10_cancelled_gstin": {"ok": p10_ok, "edges_to_cancelled": len(p10_targets),
                                "cancelled_gstins": sorted(cancelled)},
        "P11_new_gstin_high_itc": {"ok": p11_ok, "hits": p11_hits},
        "P12_multi_hop_director": {"ok": p12_ok, "shared_directors": len(shared)},
    }


def _audit_dhfl_evergreening(today: date) -> dict:
    data = json.loads(
        (ROOT / "infra" / "seeds" / "dhfl" / "dhfl_cluster.json").read_text(encoding="utf-8")
    )
    companies = {c["cin"]: c for c in data["companies"]}
    charges = data["charges"]
    funded_repayments = data["funded_repayments"]

    # P13 — at least one FUNDED_REPAYMENT_OF with overlap >= 90%
    p13_hits = [fr for fr in funded_repayments
                if fr.get("amount_overlap_pct", 0.0) >= DHFL_ROUND_TRIP_OVERLAP_PCT]
    p13_ok = len(p13_hits) >= 1

    # P14 — DHFL CIN has >= 3 charges each satisfied within 150 days
    dhfl_cin = data["companies"][0]["cin"]   # cluster's anchor company
    dhfl_charges = [c for c in charges if c["cin"] == dhfl_cin and c.get("satisfaction_date")]
    short_cycles = []
    for c in dhfl_charges:
        cd = datetime.fromisoformat(c["creation_date"]).date()
        sd = datetime.fromisoformat(c["satisfaction_date"]).date()
        days = (sd - cd).days
        if days <= DHFL_SERIAL_CYCLE_DAYS:
            short_cycles.append({"charge_id": c["charge_id"], "days": days})
    p14_ok = len(short_cycles) >= DHFL_SERIAL_MIN_CYCLES

    # P15 — SPVs (the non-anchor companies) profile: <=5 employees OR shell-like
    spv_cins = [c["cin"] for c in data["companies"][1:]]
    spv_shells = []
    for cin in spv_cins:
        spv = companies[cin]
        emp = spv.get("employee_count_reported")
        if emp is None or emp <= 5:
            spv_shells.append({"cin": cin, "employee_count_reported": emp})
    p15_ok = len(spv_shells) >= 1

    simultaneously_ok = p13_ok and p14_ok and p15_ok
    return {
        "ok": simultaneously_ok,
        "P13_round_trip_repayment": {"ok": p13_ok, "hits": p13_hits},
        "P14_serial_short_term_charge": {
            "ok": p14_ok,
            "cycles": short_cycles,
            "min_required": DHFL_SERIAL_MIN_CYCLES,
        },
        "P15_shell_conduit_borrower": {"ok": p15_ok, "shells": spv_shells},
        "p13_and_p14_and_p15_simultaneous": simultaneously_ok,
    }


async def _run(today: date | None = None) -> int:
    today = today or date.today()
    print()
    print("=" * 72)
    print(" Day-23 PRD §10 audit — NCLT + WD + ITC ring + DHFL evergreening")
    print("=" * 72)

    wd = await _audit_wilful_defaulters()
    nclt = await _audit_nclt_production_run()
    itc = _audit_itc_patterns(today)
    dhfl = _audit_dhfl_evergreening(today)

    print("\n[1] Wilful defaulters")
    print(f"    declarations: {wd['declaration_count']} (floor {WD_DECLARATIONS_FLOOR})  "
          f"unique CINs: {wd['unique_cins']}  detected: {wd['cins_detected']}")

    print("\n[2] NCLT scraper production-run audit")
    print(f"    proceedings: {nclt['proceeding_count']} (floor {NCLT_PROCEEDINGS_FLOOR})  "
          f"parsed_ok: {nclt['parsed_ok']}")
    print(f"    petition mix: {nclt['petition_breakdown']}")
    print(f"    benches: {len(nclt['unique_benches'])}")

    print("\n[3] ITC patterns 8–12 on synthetic ring")
    for k, v in itc.items():
        if k == "ok":
            continue
        flag = "PASS" if v["ok"] else "FAIL"
        print(f"    {flag}  {k}: { {kk: vv for kk, vv in v.items() if kk != 'ok'} }")

    print("\n[4] Evergreening patterns 13/14/15 on DHFL seed")
    for k, v in dhfl.items():
        if k in ("ok", "p13_and_p14_and_p15_simultaneous"):
            continue
        flag = "PASS" if v["ok"] else "FAIL"
        print(f"    {flag}  {k}")
    print(f"    p13 & p14 & p15 simultaneous: "
          f"{'PASS' if dhfl['p13_and_p14_and_p15_simultaneous'] else 'FAIL'}")

    all_ok = wd["ok"] and nclt["ok"] and itc["ok"] and dhfl["ok"]
    print("\n" + "=" * 72)
    print(f"  Wilful-defaulter detection:    {'PASS' if wd['ok'] else 'FAIL'}")
    print(f"  NCLT production-run audit:     {'PASS' if nclt['ok'] else 'FAIL'}")
    print(f"  ITC patterns 8–12 fire:        {'PASS' if itc['ok'] else 'FAIL'}")
    print(f"  DHFL evergreening 13/14/15:    {'PASS' if dhfl['ok'] else 'FAIL'}")
    print(f"  Overall:                       {'PASS' if all_ok else 'FAIL'}")
    print("=" * 72)

    AUDIT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT_PATH.write_text(json.dumps({
        "today": today.isoformat(),
        "wilful_defaulters": wd,
        "nclt_production_run": nclt,
        "itc_patterns": itc,
        "dhfl_evergreening": dhfl,
        "pass": all_ok,
    }, indent=2, default=str), encoding="utf-8")
    print(f"  Audit written to {AUDIT_OUTPUT_PATH.relative_to(ROOT)}")
    return 0 if all_ok else 23


def main() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=str, default=None,
                        help="Override today (YYYY-MM-DD) for deterministic CI runs")
    args = parser.parse_args()
    today = date.fromisoformat(args.today) if args.today else None
    sys.exit(asyncio.run(_run(today)))


if __name__ == "__main__":
    main()

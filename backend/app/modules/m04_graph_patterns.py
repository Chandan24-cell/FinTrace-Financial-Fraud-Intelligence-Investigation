"""Module 4 — Neo4j graph-pattern detection (PRD §4.4).

17 patterns total. Each is a Cypher query that yields zero-or-more matches; each
match becomes one `FraudSignal` with `TRIGGERED_BY` edges to the exact nodes
that participated. Evidence strings cite the matching nodes / amounts / counts
verbatim — PRD §4.2 specific-numbers rule applies module-wide.

  P1–P7    Core SME      (Circular Trading SCC, Related-Party Director Overlap,
                          Disqualified Director Active, Spider Web,
                          Multi-Hop Related-Party Chain, Revenue Concentration,
                          Entity Resolution WCC)
  P8–P12   ITC Carousel  (Ring SCC, Missing Trader, Cancelled GSTIN,
                          New GSTIN High ITC, Multi-Hop ITC + Director)
  P13–P17  Evergreening  (Round-Trip Repayment, Serial Short-Term Charge,
                          Shell Conduit, NPA Vintage Mismatch,
                          Multi-Entity Exposure Concentration)

Day-9 acceptance (PRD §10): 'All 17 patterns run. SCC finds rings on synthetic data.'
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from neo4j import AsyncDriver

from backend.app.config import get_settings
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
    fmt_inr,
)

logger = logging.getLogger(__name__)

MODULE_NAME = "m04_graph_patterns"

# Hard per-pattern Cypher budget. GDS calls (SCC, WCC) can blow up on
# adversarial graphs; without a ceiling, one pattern can hang the whole
# /analyse request. On timeout the pattern silently returns no signals
# instead of bubbling a 500 — the other 16 patterns still run.
_PATTERN_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Pattern catalogue — one row per PRD §4.4 pattern.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PatternSpec:
    pattern_id: str             # P01..P17
    signal_type: str
    severity: Severity
    score_contribution: float
    description: str            # one-line human label
    run: Callable[[AsyncDriver, str], Awaitable[list[FraudSignal]]]


# ===========================================================================
# Helpers
# ===========================================================================
async def _records(driver: AsyncDriver, cypher: str, **params) -> list[dict]:
    """Run a read-only Cypher and return materialised dict rows.

    Wrapped in a 5-second timeout — GDS SCC / WCC on dense graphs can
    hang; returning [] keeps /analyse responsive at the cost of one
    pattern's signals. The remaining 16 patterns still execute."""
    settings = get_settings()

    async def _drain() -> list[dict]:
        async with driver.session(database=settings.neo4j_database) as session:
            cursor = await session.run(cypher, **params)
            return [r.data() async for r in cursor]

    try:
        return await asyncio.wait_for(_drain(), timeout=_PATTERN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        # First 80 chars of the Cypher is enough to identify which pattern.
        snippet = " ".join(cypher.split())[:80]
        logger.warning(
            "M4 pattern Cypher timed out after %.1fs — returning empty result. Cypher: %s…",
            _PATTERN_TIMEOUT_SECONDS,
            snippet,
        )
        return []


def _signal(
    *,
    pattern: PatternSpec,
    evidence: str,
    triggered_by: list[dict],
) -> FraudSignal:
    return FraudSignal(
        signal_type=pattern.signal_type,
        severity=pattern.severity,
        score_contribution=pattern.score_contribution,
        evidence_string=evidence,
        module_name=MODULE_NAME,
        triggered_by=triggered_by,
    )


# ===========================================================================
# P1 — Circular Trading SCC (PRD §4.4 #1)
# ===========================================================================
_CYPHER_P1 = """
MATCH path = (c:Company {cin: $cin})-[:TRANSACTS_WITH*2..6]->(c)
WITH path, [n IN nodes(path) | n.cin] AS chain
WHERE size(chain) >= 3
RETURN DISTINCT chain LIMIT 5
"""

async def _run_p01(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P1, cin=cin)
    if not rows:
        return []
    out: list[FraudSignal] = []
    for r in rows:
        chain = r["chain"]
        out.append(_signal(
            pattern=_PATTERNS["P01"],
            evidence=(
                f"Circular trading cycle detected — TRANSACTS_WITH path of "
                f"{len(chain) - 1} hops returns to {cin}: {' → '.join(chain)}. "
                f"Classic round-tripping signature."
            ),
            triggered_by=[{"label": "Company", "cin": c} for c in chain[:-1]],
        ))
    return out


# ===========================================================================
# P2 — Related-Party Director Overlap (PRD §4.4 #2)
# ===========================================================================
_CYPHER_P2 = """
MATCH (a:Company {cin: $cin})<-[:IS_DIRECTOR_OF]-(d:Director)-[:IS_DIRECTOR_OF]->(b:Company)
WHERE a <> b
WITH a, b, collect(DISTINCT d.din) AS shared_dins
WHERE size(shared_dins) >= 2
RETURN b.cin AS counterparty_cin, b.name AS counterparty_name,
       shared_dins, size(shared_dins) AS n_shared
ORDER BY n_shared DESC
LIMIT 10
"""

async def _run_p02(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P2, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P02"],
            evidence=(
                f"{r['n_shared']} directors are shared with related party "
                f"{r['counterparty_name']} ({r['counterparty_cin']}) — "
                f"DINs {sorted(r['shared_dins'])}. Materially undisclosed "
                f"related-party transactions are common in this configuration."
            ),
            triggered_by=(
                [{"label": "Company", "cin": r["counterparty_cin"]}] +
                [{"label": "Director", "din": d} for d in r["shared_dins"]]
            ),
        ))
    return out


# ===========================================================================
# P3 — Disqualified Director Active (PRD §4.4 #3)
# ===========================================================================
_CYPHER_P3 = """
MATCH (c:Company {cin: $cin})<-[r:IS_DIRECTOR_OF]-(d:Director)
WHERE d.is_disqualified = true AND (r.is_current = true OR r.to_date IS NULL)
RETURN d.din AS din, d.name AS name, d.num_directorships AS num_directorships
"""

async def _run_p03(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P3, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P03"],
            evidence=(
                f"MCA-disqualified director {r['name']} (DIN {r['din']}, "
                f"{r['num_directorships']} active directorships) is still "
                f"sitting on the board — Companies Act §164(2) breach."
            ),
            triggered_by=[{"label": "Director", "din": r["din"]}],
        ))
    return out


# ===========================================================================
# P4 — Spider Web (one director ≥ 10 active directorships) (PRD §4.4 #4)
# ===========================================================================
_CYPHER_P4 = """
MATCH (c:Company {cin: $cin})<-[:IS_DIRECTOR_OF]-(d:Director)
WHERE coalesce(d.num_directorships, 0) >= 10
RETURN d.din AS din, d.name AS name, d.num_directorships AS num_directorships
ORDER BY num_directorships DESC
"""

async def _run_p04(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P4, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P04"],
            evidence=(
                f"'Spider-web' director {r['name']} (DIN {r['din']}) holds "
                f"{r['num_directorships']} concurrent directorships. SFIO uses "
                f"≥10 as a shell-network proxy."
            ),
            triggered_by=[{"label": "Director", "din": r["din"]}],
        ))
    return out


# ===========================================================================
# P5 — Multi-Hop Related-Party Chain (PRD §4.4 #5)
# ===========================================================================
_CYPHER_P5 = """
MATCH path = (c:Company {cin: $cin})-[:TRANSACTS_WITH*3..5]->(target:Company)
WHERE target.cin <> c.cin
WITH path, [n IN nodes(path) | n.cin] AS chain
RETURN DISTINCT chain LIMIT 5
"""

async def _run_p05(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P5, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        chain = r["chain"]
        out.append(_signal(
            pattern=_PATTERNS["P05"],
            evidence=(
                f"Multi-hop transaction chain ({len(chain) - 1} hops) reaches a "
                f"distant counterparty: {' → '.join(chain)}. Layered routing is a "
                f"red flag for related-party concealment."
            ),
            triggered_by=[{"label": "Company", "cin": c} for c in chain],
        ))
    return out


# ===========================================================================
# P6 — Revenue Concentration (one counterparty > 40% of TRANSACTS_WITH volume)
# (PRD §4.4 #6)
# ===========================================================================
_CYPHER_P6 = """
MATCH (c:Company {cin: $cin})-[r:TRANSACTS_WITH]->(b:Company)
WITH c, sum(r.amount) AS total
WHERE total > 0
MATCH (c)-[r2:TRANSACTS_WITH]->(b:Company)
WITH c, b, sum(r2.amount) AS pair, total
WHERE pair / total > 0.40
RETURN b.cin AS counterparty_cin, b.name AS counterparty_name,
       pair AS amount_to_counterparty, total AS total_outbound,
       (pair / total) * 100.0 AS pct
ORDER BY pct DESC
"""

async def _run_p06(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P6, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P06"],
            evidence=(
                f"Revenue concentration: {r['pct']:.1f}% "
                f"({fmt_inr(r['amount_to_counterparty'])} of {fmt_inr(r['total_outbound'])}) "
                f"of outbound transactions flow to {r['counterparty_name']} "
                f"({r['counterparty_cin']}) — well above the 40% threshold."
            ),
            triggered_by=[{"label": "Company", "cin": r["counterparty_cin"]}],
        ))
    return out


# ===========================================================================
# P7 — Entity Resolution WCC (two companies sharing ≥ 2 attributes — phone /
# address / auditor / IFSC) — PRD §4.4 #7
# ===========================================================================
_CYPHER_P7 = """
MATCH (c:Company {cin: $cin})-[:SHARES_ATTRIBUTE]->(a:SharedAttribute)<-[:SHARES_ATTRIBUTE]-(b:Company)
WHERE c <> b
WITH c, b, collect(DISTINCT a.attribute_type) AS shared_types,
     collect(DISTINCT a.attribute_value) AS shared_vals
WHERE size(shared_types) >= 2
RETURN b.cin AS counterparty_cin, b.name AS counterparty_name,
       shared_types, shared_vals
LIMIT 10
"""

async def _run_p07(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P7, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        types = sorted(r["shared_types"])
        out.append(_signal(
            pattern=_PATTERNS["P07"],
            evidence=(
                f"Entity-resolution WCC: shares {len(types)} attributes "
                f"({', '.join(types)}) with {r['counterparty_name']} "
                f"({r['counterparty_cin']}) — same physical operation under two CINs."
            ),
            triggered_by=[{"label": "Company", "cin": r["counterparty_cin"]}],
        ))
    return out


# ===========================================================================
# P8 — ITC Carousel Ring SCC (PRD §4.4 #8)
# ===========================================================================
_CYPHER_P8 = """
MATCH (c:Company {cin: $cin})-[:HAS_GST_ENTITY]->(g:GSTEntity)
MATCH path = (g)-[:CLAIMS_ITC_FROM*3..7]->(g)
WITH path, [n IN nodes(path) | n.gstin] AS chain,
     [r IN relationships(path) | r.amount] AS amounts
WHERE size(chain) >= 4
RETURN chain, amounts LIMIT 3
"""

async def _run_p08(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P8, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        chain = r["chain"]
        amounts = r.get("amounts") or []
        total = sum(a for a in amounts if a is not None)
        out.append(_signal(
            pattern=_PATTERNS["P08"],
            evidence=(
                f"ITC carousel ring detected: {len(chain) - 1}-hop "
                f"CLAIMS_ITC_FROM cycle returns to seed GSTIN. "
                f"Total ITC churned: {fmt_inr(total)} across {len(amounts)} edges."
            ),
            triggered_by=[{"label": "GSTEntity", "gstin": g} for g in chain],
        ))
    return out


# ===========================================================================
# P9 — Missing Trader (tax_paid / itc_generated < 0.05) (PRD §4.4 #9)
# ===========================================================================
_CYPHER_P9 = """
MATCH (c:Company {cin: $cin})-[:HAS_GST_ENTITY]->(g:GSTEntity)-[:IS_MISSING_TRADER]->(m:MissingTrader)
RETURN g.gstin AS gstin, g.aggregate_turnover AS turnover,
       g.tax_paid_ytd AS tax_paid, m.liability_gap AS liability_gap
"""

async def _run_p09(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P9, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        ratio = (r["tax_paid"] or 0) / (r["turnover"] or 1)
        out.append(_signal(
            pattern=_PATTERNS["P09"],
            evidence=(
                f"Missing-trader profile: GSTIN {r['gstin']} declared turnover "
                f"{fmt_inr(r['turnover'] or 0)} but paid only {fmt_inr(r['tax_paid'] or 0)} "
                f"in tax (ratio {ratio:.4f} < 0.05). Liability gap: "
                f"{fmt_inr(r['liability_gap'] or 0)}."
            ),
            triggered_by=[{"label": "GSTEntity", "gstin": r["gstin"]}],
        ))
    return out


# ===========================================================================
# P10 — Cancelled GSTIN Still Claiming ITC (PRD §4.4 #10)
# ===========================================================================
_CYPHER_P10 = """
MATCH (c:Company {cin: $cin})-[:HAS_GST_ENTITY]->(g:GSTEntity)
MATCH (g)-[r:CLAIMS_ITC_FROM]->(target:GSTEntity)
WHERE target.is_cancelled = true
RETURN g.gstin AS source_gstin, target.gstin AS target_gstin,
       target.cancellation_date AS cancelled_on, r.amount AS amount, r.period AS period
"""

async def _run_p10(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P10, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P10"],
            evidence=(
                f"ITC claim of {fmt_inr(r['amount'] or 0)} for period {r['period']} "
                f"sourced from cancelled GSTIN {r['target_gstin']} "
                f"(cancelled on {r['cancelled_on']}). Such claims are inadmissible per "
                f"GST §16(2)."
            ),
            triggered_by=[
                {"label": "GSTEntity", "gstin": r["source_gstin"]},
                {"label": "GSTEntity", "gstin": r["target_gstin"]},
            ],
        ))
    return out


# ===========================================================================
# P11 — New GSTIN High ITC (registered < 90 days, ITC ≥ 30% of turnover)
# (PRD §4.4 #11)
# ===========================================================================
_CYPHER_P11 = """
MATCH (c:Company {cin: $cin})-[:HAS_GST_ENTITY]->(g:GSTEntity)
WHERE g.registration_date IS NOT NULL
WITH g, duration.inDays(g.registration_date, date()).days AS age_days
WHERE age_days < 90
MATCH (g)-[:HAS_ITC_CLAIM]->(i:ITCClaim)
WITH g, age_days, sum(i.claimed_amount) AS total_claimed
WHERE g.aggregate_turnover > 0 AND total_claimed / g.aggregate_turnover >= 0.30
RETURN g.gstin AS gstin, age_days, total_claimed,
       g.aggregate_turnover AS turnover,
       (total_claimed / g.aggregate_turnover) * 100.0 AS pct
"""

async def _run_p11(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P11, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P11"],
            evidence=(
                f"Newly registered GSTIN {r['gstin']} (age {r['age_days']} days) "
                f"claimed {fmt_inr(r['total_claimed'])} of ITC — "
                f"{r['pct']:.1f}% of declared turnover {fmt_inr(r['turnover'])}. "
                f"Above 30% threshold."
            ),
            triggered_by=[{"label": "GSTEntity", "gstin": r["gstin"]}],
        ))
    return out


# ===========================================================================
# P12 — Multi-Hop ITC + Director Overlap (PRD §4.4 #12)
# ===========================================================================
_CYPHER_P12 = """
MATCH (c:Company {cin: $cin})-[:HAS_GST_ENTITY]->(g_src:GSTEntity)
MATCH path = (g_src)-[:CLAIMS_ITC_FROM*2..4]->(g_dst:GSTEntity)
MATCH (other:Company)-[:HAS_GST_ENTITY]->(g_dst)
MATCH (c)<-[:IS_DIRECTOR_OF]-(d:Director)-[:IS_DIRECTOR_OF]->(other)
WHERE c <> other
RETURN DISTINCT other.cin AS other_cin, other.name AS other_name,
       d.din AS din, d.name AS director_name, g_dst.gstin AS dst_gstin
LIMIT 5
"""

async def _run_p12(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P12, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P12"],
            evidence=(
                f"Multi-hop ITC chain terminates at GSTIN {r['dst_gstin']} owned by "
                f"{r['other_name']} ({r['other_cin']}), which shares director "
                f"{r['director_name']} (DIN {r['din']}) with the source. "
                f"Related-party ITC routing concealed via 2–4 intermediaries."
            ),
            triggered_by=[
                {"label": "Company", "cin": r["other_cin"]},
                {"label": "Director", "din": r["din"]},
                {"label": "GSTEntity", "gstin": r["dst_gstin"]},
            ],
        ))
    return out


# ===========================================================================
# P13 — Round-Trip Repayment (FUNDED_REPAYMENT_OF with overlap > 90%)
# (PRD §4.4 #13)
# ===========================================================================
_CYPHER_P13 = """
MATCH (c:Company {cin: $cin})-[:RECEIVED_LOAN]->(repaid:LoanDisbursement)
MATCH (funding:LoanDisbursement)-[r:FUNDED_REPAYMENT_OF]->(repaid)
WHERE r.amount_overlap_pct >= 90.0
MATCH (other:Company)-[:RECEIVED_LOAN]->(funding)
RETURN funding.loan_id AS funding_loan_id, repaid.loan_id AS repaid_loan_id,
       other.cin AS other_cin, other.name AS other_name,
       r.days_between AS days_between, r.amount_overlap_pct AS overlap_pct,
       repaid.amount AS amount
ORDER BY days_between
"""

async def _run_p13(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P13, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P13"],
            evidence=(
                f"Round-trip repayment: loan {r['repaid_loan_id']} ({fmt_inr(r['amount'])}) "
                f"repaid {r['days_between']} days after disbursement of "
                f"{r['funding_loan_id']} from {r['other_name']} ({r['other_cin']}) "
                f"with {r['overlap_pct']:.1f}% amount overlap — textbook evergreening."
            ),
            triggered_by=[
                {"label": "LoanDisbursement", "loan_id": r["funding_loan_id"]},
                {"label": "LoanDisbursement", "loan_id": r["repaid_loan_id"]},
                {"label": "Company", "cin": r["other_cin"]},
            ],
        ))
    return out


# ===========================================================================
# P14 — Serial Short-Term Charge Cycling (3+ disbursement / satisfaction
# cycles ≤ 150 days within 18 months) (PRD §4.4 #14)
# ===========================================================================
_CYPHER_P14 = """
MATCH (c:Company {cin: $cin})-[:RECEIVED_LOAN]->(l:LoanDisbursement)
WHERE l.satisfaction_date IS NOT NULL
  AND duration.inDays(l.disbursement_date, l.satisfaction_date).days <= 150
WITH c, collect({loan_id: l.loan_id, start: l.disbursement_date,
                 end: l.satisfaction_date, amount: l.amount}) AS cycles
WHERE size(cycles) >= 3
RETURN cycles
"""

async def _run_p14(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P14, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        cycles = r["cycles"]
        loan_ids = [c["loan_id"] for c in cycles]
        total = sum(c["amount"] for c in cycles if c["amount"] is not None)
        out.append(_signal(
            pattern=_PATTERNS["P14"],
            evidence=(
                f"Serial short-term charge cycling: {len(cycles)} loans "
                f"({', '.join(loan_ids)}) each satisfied within 150 days, "
                f"aggregate {fmt_inr(total)}. Classic evergreening signature."
            ),
            triggered_by=[{"label": "LoanDisbursement", "loan_id": lid} for lid in loan_ids],
        ))
    return out


# ===========================================================================
# P15 — Shell Conduit (counterparty < 24 months old, ≤ 5 employees) (PRD §4.4 #15)
# ===========================================================================
_CYPHER_P15 = """
MATCH (c:Company {cin: $cin})-[:RECEIVED_LOAN]->(l:LoanDisbursement)
MATCH (funding:LoanDisbursement)-[:FUNDED_REPAYMENT_OF]->(l)
MATCH (shell:Company)-[:RECEIVED_LOAN]->(funding)
WHERE coalesce(shell.employee_count_reported, 999) <= 5
  AND shell.incorporation_date IS NOT NULL
  AND duration.inMonths(shell.incorporation_date, date()).months <= 24
RETURN DISTINCT shell.cin AS shell_cin, shell.name AS shell_name,
       shell.employee_count_reported AS employees,
       shell.incorporation_date AS incorporated_on,
       funding.loan_id AS funding_loan_id
"""

async def _run_p15(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P15, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P15"],
            evidence=(
                f"Shell-conduit borrower: {r['shell_name']} ({r['shell_cin']}) — "
                f"{r['employees']} employees, incorporated {r['incorporated_on']} — "
                f"is the source of funding loan {r['funding_loan_id']} used to repay "
                f"this company's debt. Hallmark conduit profile."
            ),
            triggered_by=[
                {"label": "Company", "cin": r["shell_cin"]},
                {"label": "LoanDisbursement", "loan_id": r["funding_loan_id"]},
            ],
        ))
    return out


# ===========================================================================
# P16 — NPA Vintage Mismatch (active wilful-defaulter declaration + new
# disbursement after declared_date) (PRD §4.4 #16)
# ===========================================================================
_CYPHER_P16 = """
MATCH (c:Company {cin: $cin})-[:HAS_WILFUL_DEFAULTER_FLAG]->(w:WilfulDefaulter)
MATCH (c)-[:RECEIVED_LOAN]->(l:LoanDisbursement)
WHERE l.disbursement_date >= w.declared_date
RETURN w.bank_name AS bank, w.declared_date AS declared_date,
       w.amount AS declared_amount,
       l.loan_id AS loan_id, l.lender_name AS lender,
       l.disbursement_date AS disbursement_date, l.amount AS new_amount
"""

async def _run_p16(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P16, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P16"],
            evidence=(
                f"NPA-vintage mismatch: declared wilful defaulter by {r['bank']} "
                f"on {r['declared_date']} ({fmt_inr(r['declared_amount'])}); yet "
                f"received fresh disbursement {r['loan_id']} of "
                f"{fmt_inr(r['new_amount'])} from {r['lender']} on "
                f"{r['disbursement_date']}. RBI MD on Wilful Defaulters §2.5 breach."
            ),
            triggered_by=[
                {"label": "LoanDisbursement", "loan_id": r["loan_id"]},
            ],
        ))
    return out


# ===========================================================================
# P17 — Multi-Entity Exposure Concentration (same lender > 60% of active
# borrowings across the ER-WCC cluster) (PRD §4.4 #17)
# ===========================================================================
_CYPHER_P17 = """
MATCH (c:Company {cin: $cin})-[:RECEIVED_LOAN]->(l:LoanDisbursement)
WHERE l.satisfaction_date IS NULL
WITH c, l.lender_name AS lender, l.amount AS amount
WITH c, lender, sum(amount) AS lender_total
MATCH (c)-[:RECEIVED_LOAN]->(l2:LoanDisbursement)
WHERE l2.satisfaction_date IS NULL
WITH c, lender, lender_total, sum(l2.amount) AS total
WHERE total > 0 AND lender_total / total >= 0.60
RETURN lender, lender_total, total, (lender_total / total) * 100.0 AS pct
ORDER BY pct DESC
"""

async def _run_p17(driver: AsyncDriver, cin: str) -> list[FraudSignal]:
    rows = await _records(driver, _CYPHER_P17, cin=cin)
    out: list[FraudSignal] = []
    for r in rows:
        out.append(_signal(
            pattern=_PATTERNS["P17"],
            evidence=(
                f"Lender concentration risk: {r['pct']:.1f}% of active borrowings "
                f"({fmt_inr(r['lender_total'])} of {fmt_inr(r['total'])}) sit with a "
                f"single lender — {r['lender']}. Above 60% systemic-risk threshold."
            ),
            triggered_by=[],
        ))
    return out


# ---------------------------------------------------------------------------
# Pattern registry — keep this in lockstep with PRD §4.4.
# ---------------------------------------------------------------------------
_PATTERNS: dict[str, PatternSpec] = {
    "P01": PatternSpec("P01", "GRAPH_CIRCULAR_TRADING_SCC",     Severity.CRITICAL, 40.0, "Circular Trading SCC", _run_p01),
    "P02": PatternSpec("P02", "GRAPH_RELATED_PARTY_DIRECTOR",   Severity.HIGH,     25.0, "Related-Party Director Overlap", _run_p02),
    "P03": PatternSpec("P03", "GRAPH_DISQUALIFIED_DIRECTOR",    Severity.CRITICAL, 40.0, "Disqualified Director Active", _run_p03),
    "P04": PatternSpec("P04", "GRAPH_SPIDER_WEB_DIRECTOR",      Severity.HIGH,     25.0, "Spider-Web Director (≥10 directorships)", _run_p04),
    "P05": PatternSpec("P05", "GRAPH_MULTI_HOP_RELATED_PARTY",  Severity.MEDIUM,   15.0, "Multi-Hop Related-Party Chain", _run_p05),
    "P06": PatternSpec("P06", "GRAPH_REVENUE_CONCENTRATION",    Severity.HIGH,     25.0, "Revenue Concentration > 40%", _run_p06),
    "P07": PatternSpec("P07", "GRAPH_ENTITY_RESOLUTION_WCC",    Severity.HIGH,     25.0, "Entity-Resolution WCC", _run_p07),

    "P08": PatternSpec("P08", "GRAPH_ITC_CAROUSEL_RING",        Severity.CRITICAL, 40.0, "ITC Carousel Ring SCC", _run_p08),
    "P09": PatternSpec("P09", "GRAPH_MISSING_TRADER",           Severity.CRITICAL, 40.0, "Missing Trader (tax/turnover < 5%)", _run_p09),
    "P10": PatternSpec("P10", "GRAPH_CANCELLED_GSTIN_ITC",      Severity.HIGH,     25.0, "Cancelled GSTIN Still Claiming ITC", _run_p10),
    "P11": PatternSpec("P11", "GRAPH_NEW_GSTIN_HIGH_ITC",       Severity.HIGH,     25.0, "New GSTIN High ITC", _run_p11),
    "P12": PatternSpec("P12", "GRAPH_MULTI_HOP_ITC_DIRECTOR",   Severity.CRITICAL, 40.0, "Multi-Hop ITC + Director Overlap", _run_p12),

    "P13": PatternSpec("P13", "GRAPH_ROUND_TRIP_REPAYMENT",     Severity.CRITICAL, 40.0, "Round-Trip Repayment > 90% overlap", _run_p13),
    "P14": PatternSpec("P14", "GRAPH_SERIAL_SHORT_TERM_CHARGE", Severity.HIGH,     25.0, "Serial Short-Term Charge Cycling", _run_p14),
    "P15": PatternSpec("P15", "GRAPH_SHELL_CONDUIT_BORROWER",   Severity.HIGH,     25.0, "Shell Conduit Borrower", _run_p15),
    "P16": PatternSpec("P16", "GRAPH_NPA_VINTAGE_MISMATCH",     Severity.CRITICAL, 40.0, "NPA Vintage Mismatch", _run_p16),
    "P17": PatternSpec("P17", "GRAPH_LENDER_CONCENTRATION",     Severity.HIGH,     25.0, "Multi-Entity Lender Concentration", _run_p17),
}


def patterns() -> list[PatternSpec]:
    """Stable-ordered list of all 17 patterns — handy for tests and dashboards."""
    return [_PATTERNS[k] for k in sorted(_PATTERNS)]


async def run(driver: AsyncDriver, cin: str) -> ModuleResult:
    """Execute every pattern against the graph for one company. Each match
    is converted into a FraudSignal whose `triggered_by` carries the exact
    node references the upstream writer turns into TRIGGERED_BY edges."""
    signals: list[FraudSignal] = []
    for spec in patterns():
        try:
            fired = await spec.run(driver, cin)
        except Exception as exc:
            logger.warning("Pattern %s failed for %s: %s", spec.pattern_id, cin, exc)
            continue
        for s in fired:
            signals.append(s)
    return ModuleResult(
        module_name=MODULE_NAME,
        cin=cin,
        year=None,
        score=clamp_score(sum(s.score_contribution for s in signals)),
        signals=signals,
    )

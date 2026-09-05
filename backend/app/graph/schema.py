"""Neo4j schema: constraints + indexes for all node labels in PRD §3.

Run via: `python -m backend.app.graph.schema` (after starting Neo4j locally).
Idempotent — uses IF NOT EXISTS on every statement.
"""

from __future__ import annotations

import asyncio
import logging

from neo4j import AsyncGraphDatabase

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


# --- Uniqueness constraints (one per natural key) ---------------------------
CONSTRAINTS: list[str] = [
    # Auth (Day 2) — User lives in Neo4j too, per the single-data-store constraint.
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
    "CREATE CONSTRAINT user_email IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE",

    "CREATE CONSTRAINT company_cin IF NOT EXISTS FOR (c:Company) REQUIRE c.cin IS UNIQUE",
    "CREATE CONSTRAINT director_din IF NOT EXISTS FOR (d:Director) REQUIRE d.din IS UNIQUE",
    "CREATE CONSTRAINT financial_statement_key IF NOT EXISTS FOR (f:FinancialStatement) REQUIRE (f.cin, f.year) IS UNIQUE",
    "CREATE CONSTRAINT fraud_signal_id IF NOT EXISTS FOR (s:FraudSignal) REQUIRE s.signal_id IS UNIQUE",
    "CREATE CONSTRAINT shared_attribute_key IF NOT EXISTS FOR (a:SharedAttribute) REQUIRE (a.attribute_type, a.attribute_value) IS UNIQUE",
    "CREATE CONSTRAINT industry_benchmark_key IF NOT EXISTS FOR (b:IndustryBenchmark) REQUIRE (b.nic_code, b.year, b.metric) IS UNIQUE",
    "CREATE CONSTRAINT gst_entity_gstin IF NOT EXISTS FOR (g:GSTEntity) REQUIRE g.gstin IS UNIQUE",
    "CREATE CONSTRAINT itc_claim_key IF NOT EXISTS FOR (i:ITCClaim) REQUIRE (i.gstin, i.period) IS UNIQUE",
    "CREATE CONSTRAINT loan_disbursement_id IF NOT EXISTS FOR (l:LoanDisbursement) REQUIRE l.loan_id IS UNIQUE",
    "CREATE CONSTRAINT nclt_case_number IF NOT EXISTS FOR (n:NCLTProceeding) REQUIRE n.case_number IS UNIQUE",
    "CREATE CONSTRAINT wilful_defaulter_key IF NOT EXISTS FOR (w:WilfulDefaulter) REQUIRE (w.cin, w.bank_name, w.declared_date) IS UNIQUE",
    # Day 6 — Evergreening + ITC carousel extension nodes
    "CREATE CONSTRAINT loan_repayment_key IF NOT EXISTS FOR (r:LoanRepayment) REQUIRE (r.loan_id, r.repayment_date) IS UNIQUE",
    "CREATE CONSTRAINT evergreening_cluster_id IF NOT EXISTS FOR (e:EvergreeningCluster) REQUIRE e.cluster_id IS UNIQUE",
    "CREATE CONSTRAINT missing_trader_gstin IF NOT EXISTS FOR (m:MissingTrader) REQUIRE m.gstin IS UNIQUE",
]


# --- Lookup indexes (high-cardinality query paths) --------------------------
INDEXES: list[str] = [
    "CREATE INDEX company_gstin IF NOT EXISTS FOR (c:Company) ON (c.gstin)",
    "CREATE INDEX company_nic IF NOT EXISTS FOR (c:Company) ON (c.nic_code)",
    "CREATE INDEX company_state IF NOT EXISTS FOR (c:Company) ON (c.state)",
    "CREATE INDEX director_name IF NOT EXISTS FOR (d:Director) ON (d.name)",
    "CREATE INDEX director_disqualified IF NOT EXISTS FOR (d:Director) ON (d.is_disqualified)",
    "CREATE INDEX fraud_signal_type IF NOT EXISTS FOR (s:FraudSignal) ON (s.signal_type)",
    "CREATE INDEX fraud_signal_severity IF NOT EXISTS FOR (s:FraudSignal) ON (s.severity)",
    "CREATE INDEX data_quality_cin IF NOT EXISTS FOR (e:DataQualityError) ON (e.cin)",
]


async def apply_schema() -> None:
    settings = get_settings()
    async with AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    ) as driver:
        await driver.verify_connectivity()
        async with driver.session(database=settings.neo4j_database) as session:
            for stmt in CONSTRAINTS + INDEXES:
                logger.info("Applying: %s", stmt)
                await session.run(stmt)

            version = await session.run("CALL gds.version() YIELD gdsVersion RETURN gdsVersion")
            gds_record = await version.single()
            logger.info("GDS version: %s", gds_record["gdsVersion"] if gds_record else "<not installed>")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    asyncio.run(apply_schema())


if __name__ == "__main__":
    main()

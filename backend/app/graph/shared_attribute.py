"""SharedAttribute hypergraph materialisation (PRD §4.10 Module 10 prep).

PRD §3 defines SharedAttribute as an intermediate node connecting any number of
Companies that share the same attribute value (e.g. all companies registered at
the same address). It's a hypergraph encoding — one node represents the shared
fact; SHARES_ATTRIBUTE edges connect every company involved.

Why this matters:
  Module 10 (Day 11) detects shell networks. A pairwise edge "Company A shares
  address with Company B" misses the 'one address, 50 companies' pattern. The
  SharedAttribute intermediate node lets a single Cypher query return all 50.

Attribute types materialised here (Day 4):
  - registered_address      threshold >= 5 companies -> HIGH (Module 10)
  - auditor_din             threshold >= 50 cos same district -> MEDIUM
  - bank_branch_ifsc        threshold >= 10 related cos -> MEDIUM

Phone-prefix clustering deferred to Day 11 when contact data is structured.
"""

from __future__ import annotations

import logging

from neo4j import AsyncDriver

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


# Each entry: (attribute_type, source property on Company)
_COMPANY_PROPERTY_RULES: list[tuple[str, str]] = [
    ("registered_address", "registered_address"),
    ("auditor_din",        "auditor_din"),
]


_MATERIALISE_FROM_COMPANY = """
MATCH (c:Company)
WHERE c[$prop] IS NOT NULL AND c[$prop] <> ''
WITH c[$prop] AS attribute_value, collect(c) AS companies
WHERE size(companies) >= $min_count
MERGE (a:SharedAttribute {attribute_type: $attribute_type, attribute_value: attribute_value})
SET a.company_count = size(companies),
    a.last_materialised_at = datetime()
WITH a, companies
UNWIND companies AS company
MERGE (company)-[r:SHARES_ATTRIBUTE]->(a)
SET r.attribute_type = $attribute_type
RETURN count(DISTINCT a) AS attributes_created
"""


_MATERIALISE_FROM_CHARGES = """
MATCH (c:Company)-[:RECEIVED_LOAN]->(l:LoanDisbursement)
WHERE l.bank_branch_ifsc IS NOT NULL AND l.bank_branch_ifsc <> ''
WITH l.bank_branch_ifsc AS attribute_value, collect(DISTINCT c) AS companies
WHERE size(companies) >= $min_count
MERGE (a:SharedAttribute {attribute_type: 'bank_branch_ifsc', attribute_value: attribute_value})
SET a.company_count = size(companies),
    a.last_materialised_at = datetime()
WITH a, companies
UNWIND companies AS company
MERGE (company)-[r:SHARES_ATTRIBUTE]->(a)
SET r.attribute_type = 'bank_branch_ifsc'
RETURN count(DISTINCT a) AS attributes_created
"""


async def materialise_shared_attributes(
    driver: AsyncDriver, *, min_count: int = 2
) -> dict[str, int]:
    """Re-materialise SharedAttribute nodes. Idempotent — safe to re-run.

    Returns a dict {attribute_type: number_of_nodes_created_or_updated}.
    """
    settings = get_settings()
    summary: dict[str, int] = {}

    async with driver.session(database=settings.neo4j_database) as session:
        for attribute_type, prop in _COMPANY_PROPERTY_RULES:
            result = await session.run(
                _MATERIALISE_FROM_COMPANY,
                attribute_type=attribute_type,
                prop=prop,
                min_count=min_count,
            )
            record = await result.single()
            summary[attribute_type] = int(record["attributes_created"]) if record else 0
            logger.info(
                "Materialised %d SharedAttribute(%s) nodes",
                summary[attribute_type], attribute_type,
            )

        result = await session.run(_MATERIALISE_FROM_CHARGES, min_count=min_count)
        record = await result.single()
        summary["bank_branch_ifsc"] = int(record["attributes_created"]) if record else 0
        logger.info(
            "Materialised %d SharedAttribute(bank_branch_ifsc) nodes",
            summary["bank_branch_ifsc"],
        )

    return summary

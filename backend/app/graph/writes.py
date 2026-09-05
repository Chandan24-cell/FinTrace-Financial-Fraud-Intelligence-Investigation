"""Async Cypher writers for the ingestion pipeline (PRD Â§3 schema).

All writes are idempotent (MERGE on natural key) so a re-run is safe.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver

from backend.app.config import get_settings
from backend.app.ingest.benchmarks import BenchmarkPoint
from backend.app.ingest.gst import RawGSTEntity
from backend.app.ingest.nclt import RawNCLTProceeding
from backend.app.modules.base import FraudSignal
from backend.app.ingest.schemas import (
    CompanyBundle,
    RawCharge,
    RawCompany,
    RawDirector,
    RawFinancialStatement,
)
from backend.app.ingest.special_seeds import (
    RawCarouselGSTEntity,
    RawClaimsITCFrom,
    RawFundedRepaymentOf,
    RawITCClaim,
    RawLoanRepayment,
)
from backend.app.ingest.validation import DataQualityError
from backend.app.ingest.wilful_defaulter import RawWilfulDefaulter

logger = logging.getLogger(__name__)


_UPSERT_COMPANY = """
MERGE (c:Company {cin: $cin})
SET c.name = $name,
    c.gstin = $gstin,
    c.nic_code = $nic_code,
    c.state = $state,
    c.employee_count_reported = $employee_count_reported,
    c.incorporation_date = date($incorporation_date),
    c.registered_address = $registered_address,
    c.contact_phone = $contact_phone,
    c.auditor_din = $auditor_din,
    c.last_ingested_at = datetime()
RETURN c.cin AS cin
"""

_UPSERT_DIRECTOR = """
MERGE (d:Director {din: $din})
ON CREATE SET d.name = $name,
              d.dob = CASE WHEN $dob IS NULL THEN NULL ELSE date($dob) END,
              d.is_disqualified = $is_disqualified,
              d.num_directorships = $num_directorships
ON MATCH  SET d.num_directorships = $num_directorships,
              d.is_disqualified = $is_disqualified
WITH d
MATCH (c:Company {cin: $cin})
MERGE (d)-[r:IS_DIRECTOR_OF {from_date: date($appointment_date)}]->(c)
SET r.designation = $designation,
    r.to_date = CASE WHEN $cessation_date IS NULL THEN NULL ELSE date($cessation_date) END,
    r.is_current = ($cessation_date IS NULL)
RETURN d.din AS din
"""

_UPSERT_FINANCIAL = """
MATCH (c:Company {cin: $cin})
MERGE (f:FinancialStatement {cin: $cin, year: $year})
SET f += $props
MERGE (c)-[:HAS_FINANCIALS {year: $year}]->(f)
RETURN f.year AS year
"""

_UPSERT_CHARGE = """
MATCH (c:Company {cin: $cin})
MERGE (l:LoanDisbursement {loan_id: $charge_id})
SET l.cin = $cin,
    l.lender_name = $lender_name,
    l.amount = $amount,
    l.disbursement_date = date($creation_date),
    l.satisfaction_date = CASE WHEN $satisfaction_date IS NULL THEN NULL ELSE date($satisfaction_date) END,
    l.loan_type = $charge_type,
    l.bank_branch_ifsc = $bank_branch_ifsc
MERGE (c)-[:RECEIVED_LOAN]->(l)
RETURN l.loan_id AS loan_id
"""

_UPSERT_DQE = """
CREATE (e:DataQualityError {
    cin: $cin,
    year: $year,
    error_type: $error_type,
    field: $field,
    expected_value: $expected_value,
    actual_value: $actual_value,
    timestamp: datetime($timestamp)
})
WITH e
OPTIONAL MATCH (c:Company {cin: $cin})
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
    MERGE (c)-[:HAS_DATA_QUALITY_ERROR]->(e)
)
RETURN id(e) AS id
"""

_SET_DATA_CONFIDENCE = """
MATCH (c:Company {cin: $cin})
SET c.data_confidence = $data_confidence,
    c.last_data_confidence_at = datetime()
RETURN c.cin AS cin
"""


async def upsert_company(driver: AsyncDriver, company: RawCompany) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_COMPANY,
            cin=company.cin,
            name=company.name,
            gstin=company.gstin,
            nic_code=company.nic_code,
            state=company.state,
            employee_count_reported=company.employee_count_reported,
            incorporation_date=company.incorporation_date.isoformat(),
            registered_address=company.registered_address,
            contact_phone=company.contact_phone,
            auditor_din=company.auditor_din,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert Company {company.cin}")
    return record["cin"]


async def upsert_director(driver: AsyncDriver, cin: str, director: RawDirector) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_DIRECTOR,
            din=director.din,
            name=director.name,
            dob=director.dob.isoformat() if director.dob else None,
            is_disqualified=director.is_disqualified,
            num_directorships=director.num_directorships,
            cin=cin,
            designation=director.designation,
            appointment_date=director.appointment_date.isoformat(),
            cessation_date=director.cessation_date.isoformat() if director.cessation_date else None,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert Director {director.din}")
    return record["din"]


async def upsert_financial(driver: AsyncDriver, financial: RawFinancialStatement) -> int:
    settings = get_settings()
    props: dict[str, Any] = financial.model_dump(exclude={"cin", "year"})
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_FINANCIAL,
            cin=financial.cin,
            year=financial.year,
            props=props,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert FinancialStatement {financial.cin}/{financial.year}")
    return record["year"]


async def upsert_charge(driver: AsyncDriver, charge: RawCharge) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_CHARGE,
            cin=charge.cin,
            charge_id=charge.charge_id,
            lender_name=charge.lender_name,
            amount=charge.amount,
            creation_date=charge.creation_date.isoformat(),
            satisfaction_date=charge.satisfaction_date.isoformat() if charge.satisfaction_date else None,
            charge_type=charge.charge_type,
            bank_branch_ifsc=charge.bank_branch_ifsc,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert LoanDisbursement {charge.charge_id}")
    return record["loan_id"]


async def write_data_quality_error(driver: AsyncDriver, dqe: DataQualityError) -> None:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        await session.run(
            _UPSERT_DQE,
            cin=dqe.cin,
            year=dqe.year,
            error_type=dqe.error_type,
            field=dqe.field,
            expected_value=dqe.expected_value,
            actual_value=dqe.actual_value,
            timestamp=dqe.timestamp.isoformat(),
        )


async def set_data_confidence(driver: AsyncDriver, cin: str, score: int) -> None:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        await session.run(_SET_DATA_CONFIDENCE, cin=cin, data_confidence=score)


async def write_bundle(driver: AsyncDriver, bundle: CompanyBundle) -> None:
    """Convenience: write company + all related nodes in one call."""
    await upsert_company(driver, bundle.company)
    for d in bundle.directors:
        await upsert_director(driver, bundle.company.cin, d)
    for f in bundle.financials:
        await upsert_financial(driver, f)
    for ch in bundle.charges:
        await upsert_charge(driver, ch)


# --- Day 4 additions: IndustryBenchmark + GSTEntity ------------------------

_UPSERT_INDUSTRY_BENCHMARK = """
MERGE (b:IndustryBenchmark {nic_code: $nic_code, year: $year, metric: $metric})
SET b.p25 = $p25,
    b.median = $median,
    b.p75 = $p75,
    b.benford_applicable = $benford_applicable,
    b.last_refreshed_at = datetime()
RETURN b.metric AS metric
"""

_UPSERT_GST_ENTITY = """
MERGE (g:GSTEntity {gstin: $gstin})
SET g.pan = $pan,
    g.registration_date = date($registration_date),
    g.cancellation_date = CASE WHEN $cancellation_date IS NULL THEN NULL ELSE date($cancellation_date) END,
    g.is_cancelled = $is_cancelled,
    g.taxpayer_type = $taxpayer_type,
    g.aggregate_turnover = $aggregate_turnover,
    g.tax_paid_ytd = $tax_paid_ytd,
    g.last_refreshed_at = datetime()
WITH g
MATCH (c:Company {cin: $cin})
MERGE (c)-[:HAS_GST_ENTITY]->(g)
RETURN g.gstin AS gstin
"""


async def upsert_industry_benchmark(driver: AsyncDriver, point: BenchmarkPoint) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_INDUSTRY_BENCHMARK,
            nic_code=point.nic_code,
            year=point.year,
            metric=point.metric,
            p25=point.p25,
            median=point.median,
            p75=point.p75,
            benford_applicable=point.benford_applicable,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(
            f"Failed to upsert IndustryBenchmark {point.nic_code}/{point.year}/{point.metric}"
        )
    return record["metric"]


async def upsert_gst_entity(driver: AsyncDriver, gst: RawGSTEntity) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_GST_ENTITY,
            gstin=gst.gstin,
            pan=gst.pan,
            cin=gst.cin,
            registration_date=gst.registration_date.isoformat(),
            cancellation_date=gst.cancellation_date.isoformat() if gst.cancellation_date else None,
            is_cancelled=gst.is_cancelled,
            taxpayer_type=gst.taxpayer_type,
            aggregate_turnover=gst.aggregate_turnover,
            tax_paid_ytd=gst.tax_paid_ytd,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert GSTEntity {gst.gstin}")
    return record["gstin"]


# --- Day 5 additions: NCLT + WilfulDefaulter -------------------------------

_UPSERT_NCLT = """
MERGE (n:NCLTProceeding {case_number: $case_number})
SET n.cin = $cin,
    n.petition_type = $petition_type,
    n.filing_date = date($filing_date),
    n.status = $status,
    n.amount_claimed = $amount_claimed,
    n.bench = $bench,
    n.last_refreshed_at = datetime()
WITH n
MATCH (c:Company {cin: $cin})
MERGE (c)-[:HAS_NCLT_PROCEEDING]->(n)
RETURN n.case_number AS case_number
"""

_UPSERT_WILFUL_DEFAULTER = """
MERGE (w:WilfulDefaulter {cin: $cin, bank_name: $bank_name, declared_date: date($declared_date)})
SET w.din = $din,
    w.amount = $amount,
    w.source = $source,
    w.last_refreshed_at = datetime()
WITH w
MATCH (c:Company {cin: $cin})
MERGE (c)-[r:HAS_WILFUL_DEFAULTER_FLAG]->(w)
SET r.declared_date = date($declared_date)
RETURN id(w) AS id
"""


async def upsert_nclt_proceeding(driver: AsyncDriver, proceeding: RawNCLTProceeding) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_NCLT,
            case_number=proceeding.case_number,
            cin=proceeding.cin,
            petition_type=proceeding.petition_type,
            filing_date=proceeding.filing_date.isoformat(),
            status=proceeding.status,
            amount_claimed=proceeding.amount_claimed,
            bench=proceeding.bench,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert NCLTProceeding {proceeding.case_number}")
    return record["case_number"]


async def upsert_wilful_defaulter(driver: AsyncDriver, declaration: RawWilfulDefaulter) -> int:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_WILFUL_DEFAULTER,
            cin=declaration.cin,
            din=declaration.din,
            bank_name=declaration.bank_name,
            declared_date=declaration.declared_date.isoformat(),
            amount=declaration.amount,
            source=declaration.source,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert WilfulDefaulter for {declaration.cin}")
    return int(record["id"])


# --- Day 6 additions: LoanRepayment, FUNDED_REPAYMENT_OF, ITC carousel ----

_UPSERT_LOAN_REPAYMENT = """
MATCH (l:LoanDisbursement {loan_id: $loan_id})
MERGE (r:LoanRepayment {loan_id: $loan_id, repayment_date: date($repayment_date)})
SET r.cin = $cin,
    r.amount = $amount,
    r.source_account_ifsc = $source_account_ifsc,
    r.days_to_repayment = $days_to_repayment
MERGE (l)<-[:REPAID_LOAN]-(r)
WITH r
MATCH (c:Company {cin: $cin})
MERGE (c)-[:REPAID_LOAN]->(r)
RETURN r.loan_id AS loan_id
"""

_UPSERT_FUNDED_REPAYMENT_OF = """
MATCH (funding:LoanDisbursement {loan_id: $funding_loan_id})
MATCH (repaid:LoanDisbursement {loan_id: $funded_repayment_loan_id})
MERGE (funding)-[r:FUNDED_REPAYMENT_OF]->(repaid)
SET r.days_between = $days_between,
    r.amount_overlap_pct = $amount_overlap_pct
RETURN id(r) AS id
"""

_UPSERT_CAROUSEL_GST_ENTITY = """
MERGE (g:GSTEntity {gstin: $gstin})
SET g.name = $name,
    g.pan = $pan,
    g.registration_date = date($registration_date),
    g.is_cancelled = $is_cancelled,
    g.taxpayer_type = $taxpayer_type,
    g.aggregate_turnover = $aggregate_turnover,
    g.tax_paid_ytd = $tax_paid_ytd,
    g.cancellation_date = CASE WHEN $cancellation_date IS NULL THEN NULL ELSE date($cancellation_date) END,
    g.is_synthetic = true
WITH g
FOREACH (_ IN CASE WHEN $cin IS NULL THEN [] ELSE [1] END |
    MERGE (c:Company {cin: $cin})
    SET c.name = $name,
        c.nic_code = coalesce(c.nic_code, 51100),
        c.state = coalesce(c.state, "MH"),
        c.incorporation_date = coalesce(c.incorporation_date, date($registration_date)),
        c.gstin = $gstin,
        c.last_refreshed_at = datetime()
    MERGE (c)-[:HAS_GST_ENTITY]->(g)
)
WITH g
FOREACH (_ IN CASE WHEN $is_missing_trader THEN [1] ELSE [] END |
    MERGE (m:MissingTrader {gstin: $gstin})
    SET m.total_itc_generated = $aggregate_turnover,
        m.tax_liability_paid = $tax_paid_ytd,
        m.liability_gap = $aggregate_turnover - $tax_paid_ytd
    MERGE (g)-[:IS_MISSING_TRADER]->(m)
)
RETURN g.gstin AS gstin
"""

_UPSERT_ITC_CLAIM = """
MATCH (g:GSTEntity {gstin: $gstin})
MERGE (i:ITCClaim {gstin: $gstin, period: $period})
SET i.claimed_amount = $claimed_amount,
    i.eligible_amount = $eligible_amount,
    i.source_gstin = $source_gstin
MERGE (g)-[:HAS_ITC_CLAIM]->(i)
RETURN i.gstin AS gstin
"""

_UPSERT_CLAIMS_ITC_FROM = """
MATCH (src:GSTEntity {gstin: $from_gstin})
MATCH (dst:GSTEntity {gstin: $to_gstin})
MERGE (src)-[r:CLAIMS_ITC_FROM {period: $period}]->(dst)
SET r.amount = $amount,
    r.invoice_count = $invoice_count,
    r.risk_flag = $risk_flag
RETURN id(r) AS id
"""


async def upsert_loan_repayment(driver: AsyncDriver, repayment: RawLoanRepayment) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_LOAN_REPAYMENT,
            loan_id=repayment.loan_id,
            cin=repayment.cin,
            repayment_date=repayment.repayment_date.isoformat(),
            amount=repayment.amount,
            source_account_ifsc=repayment.source_account_ifsc,
            days_to_repayment=repayment.days_to_repayment,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert LoanRepayment for {repayment.loan_id}")
    return record["loan_id"]


async def upsert_funded_repayment_of(driver: AsyncDriver, edge: RawFundedRepaymentOf) -> int:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_FUNDED_REPAYMENT_OF,
            funding_loan_id=edge.funding_loan_id,
            funded_repayment_loan_id=edge.funded_repayment_loan_id,
            days_between=edge.days_between,
            amount_overlap_pct=edge.amount_overlap_pct,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(
            f"Failed to upsert FUNDED_REPAYMENT_OF "
            f"{edge.funding_loan_id} -> {edge.funded_repayment_loan_id}"
        )
    return int(record["id"])


async def upsert_carousel_gst_entity(driver: AsyncDriver, entity: RawCarouselGSTEntity) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_CAROUSEL_GST_ENTITY,
            gstin=entity.gstin,
            name=entity.name,
            pan=entity.pan,
            registration_date=entity.registration_date.isoformat(),
            is_cancelled=entity.is_cancelled,
            taxpayer_type=entity.taxpayer_type,
            aggregate_turnover=entity.aggregate_turnover,
            tax_paid_ytd=entity.tax_paid_ytd,
            cancellation_date=entity.cancellation_date.isoformat() if entity.cancellation_date else None,
            is_missing_trader=entity.is_missing_trader,
            cin=entity.cin,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert carousel GSTEntity {entity.gstin}")
    return record["gstin"]


async def upsert_itc_claim(driver: AsyncDriver, claim: RawITCClaim) -> str:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_ITC_CLAIM,
            gstin=claim.gstin,
            period=claim.period,
            claimed_amount=claim.claimed_amount,
            eligible_amount=claim.eligible_amount,
            source_gstin=claim.source_gstin,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to upsert ITCClaim {claim.gstin}/{claim.period}")
    return record["gstin"]


async def upsert_claims_itc_from(driver: AsyncDriver, edge: RawClaimsITCFrom) -> int:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_CLAIMS_ITC_FROM,
            from_gstin=edge.from_gstin,
            to_gstin=edge.to_gstin,
            period=edge.period,
            amount=edge.amount,
            invoice_count=edge.invoice_count,
            risk_flag=edge.risk_flag,
        )
        record = await result.single()
    if record is None:
        raise RuntimeError(
            f"Failed to upsert CLAIMS_ITC_FROM {edge.from_gstin} -> {edge.to_gstin}"
        )
    return int(record["id"])


# --- Day 8 additions: FraudSignal + TRIGGERED_BY (PRD Â§6 evidence provenance)

_UPSERT_FRAUD_SIGNAL = """
MERGE (s:FraudSignal {signal_id: $signal_id})
SET s.signal_type = $signal_type,
    s.severity = $severity,
    s.score_contribution = $score_contribution,
    s.evidence_string = $evidence_string,
    s.module_name = $module_name,
    s.cin = $cin,
    s.created_at = datetime($created_at)
WITH s
MATCH (c:Company {cin: $cin})
// Direct Company -> FraudSignal edge so provenance traversal is one hop
// even for module 9 (NCLT) signals where no fiscal year context exists.
MERGE (c)-[hsc:HAS_FRAUD_SIGNAL]->(s)
SET hsc.module = $module_name, hsc.severity = $severity
WITH s, c
// When a fiscal year IS available (modules M1, M2, M3, M5-M8), also
// edge through the FinancialStatement so the per-FY drill-down works.
OPTIONAL MATCH (c)-[:HAS_FINANCIALS {year: $year}]->(fs:FinancialStatement)
FOREACH (_ IN CASE WHEN fs IS NULL THEN [] ELSE [1] END |
    MERGE (fs)-[r:HAS_FRAUD_SIGNAL]->(s)
    SET r.module = $module_name, r.severity = $severity
)
RETURN s.signal_id AS signal_id
"""


_LINK_TRIGGERED_BY_FS = """
MATCH (s:FraudSignal {signal_id: $signal_id})
MATCH (e:FinancialStatement {cin: $cin, year: $year})
MERGE (s)-[:TRIGGERED_BY]->(e)
RETURN id(e) AS id
"""

_LINK_TRIGGERED_BY_LOAN = """
MATCH (s:FraudSignal {signal_id: $signal_id})
MATCH (e:LoanDisbursement {loan_id: $loan_id})
MERGE (s)-[:TRIGGERED_BY]->(e)
RETURN id(e) AS id
"""

_LINK_TRIGGERED_BY_GST = """
MATCH (s:FraudSignal {signal_id: $signal_id})
MATCH (e:GSTEntity {gstin: $gstin})
MERGE (s)-[:TRIGGERED_BY]->(e)
RETURN id(e) AS id
"""

_LINK_TRIGGERED_BY_COMPANY = """
MATCH (s:FraudSignal {signal_id: $signal_id})
MATCH (e:Company {cin: $cin})
MERGE (s)-[:TRIGGERED_BY]->(e)
RETURN id(e) AS id
"""

_LINK_TRIGGERED_BY_DIRECTOR = """
MATCH (s:FraudSignal {signal_id: $signal_id})
MATCH (e:Director {din: $din})
MERGE (s)-[:TRIGGERED_BY]->(e)
RETURN id(e) AS id
"""


async def upsert_fraud_signal(
    driver: AsyncDriver, cin: str, year: int | None, signal: FraudSignal,
) -> str:
    """Persist a FraudSignal + HAS_FRAUD_SIGNAL link + every TRIGGERED_BY edge
    declared in `signal.triggered_by`. Idempotent on signal_id."""
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            _UPSERT_FRAUD_SIGNAL,
            signal_id=signal.signal_id,
            signal_type=signal.signal_type,
            severity=signal.severity.value,
            score_contribution=float(signal.score_contribution),
            evidence_string=signal.evidence_string,
            module_name=signal.module_name,
            created_at=signal.created_at.isoformat(),
            cin=cin,
            year=year,
        )
        record = await result.single()

        # TRIGGERED_BY edges â€” one Cypher per source type
        for ref in signal.triggered_by:
            label = ref.get("label")
            if label == "FinancialStatement":
                await session.run(
                    _LINK_TRIGGERED_BY_FS,
                    signal_id=signal.signal_id,
                    cin=ref["cin"],
                    year=ref["year"],
                )
            elif label == "LoanDisbursement":
                await session.run(
                    _LINK_TRIGGERED_BY_LOAN,
                    signal_id=signal.signal_id,
                    loan_id=ref["loan_id"],
                )
            elif label == "GSTEntity":
                await session.run(
                    _LINK_TRIGGERED_BY_GST,
                    signal_id=signal.signal_id,
                    gstin=ref["gstin"],
                )
            elif label == "Company":
                await session.run(
                    _LINK_TRIGGERED_BY_COMPANY,
                    signal_id=signal.signal_id,
                    cin=ref["cin"],
                )
            elif label == "Director":
                await session.run(
                    _LINK_TRIGGERED_BY_DIRECTOR,
                    signal_id=signal.signal_id,
                    din=ref["din"],
                )

    if record is None:
        raise RuntimeError(f"Failed to upsert FraudSignal {signal.signal_id}")
    return record["signal_id"]


def _year_from_signal(signal: FraudSignal) -> int | None:
    """Pull a fiscal year off the signal's `triggered_by` refs if any
    point at a FinancialStatement â€” needed for the FS-mediated edge
    in `_UPSERT_FRAUD_SIGNAL`. Returns None when no year is available
    (M9 NCLT, M10 hypergraph, M11 anomaly, etc.) which is fine â€” the
    FOREACH in the Cypher will then skip the FS edge."""
    for ref in signal.triggered_by:
        if ref.get("label") == "FinancialStatement" and "year" in ref:
            return int(ref["year"])
    return None


async def persist_fraud_signals(
    driver: AsyncDriver, cin: str, signals: list[FraudSignal],
) -> int:
    """Batch-persist every FraudSignal raised for one CIN inside a single
    Neo4j session, returning the count successfully written.

    Used by the scorer after `apply_override` so the /provenance endpoint
    can later traverse the live graph (Stream 3.4) instead of re-running
    the whole scorer. Idempotent â€” the underlying MERGE is keyed by
    `signal_id`, so calling this twice on the same RiskReport is a no-op.

    Never raises: any Neo4j-side failure is logged + the in-memory
    `report.evidence_chain` remains the source of truth for the request.
    """
    if not signals:
        return 0
    settings = get_settings()
    count = 0
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            for signal in signals:
                year = _year_from_signal(signal)
                await session.run(
                    _UPSERT_FRAUD_SIGNAL,
                    signal_id=signal.signal_id,
                    signal_type=signal.signal_type,
                    severity=signal.severity.value,
                    score_contribution=float(signal.score_contribution),
                    evidence_string=signal.evidence_string,
                    module_name=signal.module_name,
                    created_at=signal.created_at.isoformat(),
                    cin=cin,
                    year=year,
                )
                for ref in signal.triggered_by:
                    label = ref.get("label")
                    try:
                        if label == "FinancialStatement":
                            await session.run(
                                _LINK_TRIGGERED_BY_FS,
                                signal_id=signal.signal_id,
                                cin=ref.get("cin", cin),
                                year=ref["year"],
                            )
                        elif label == "LoanDisbursement":
                            await session.run(
                                _LINK_TRIGGERED_BY_LOAN,
                                signal_id=signal.signal_id,
                                loan_id=ref["loan_id"],
                            )
                        elif label == "GSTEntity":
                            await session.run(
                                _LINK_TRIGGERED_BY_GST,
                                signal_id=signal.signal_id,
                                gstin=ref["gstin"],
                            )
                        elif label == "Company":
                            await session.run(
                                _LINK_TRIGGERED_BY_COMPANY,
                                signal_id=signal.signal_id,
                                cin=ref.get("cin", cin),
                            )
                        elif label == "Director":
                            await session.run(
                                _LINK_TRIGGERED_BY_DIRECTOR,
                                signal_id=signal.signal_id,
                                din=ref["din"],
                            )
                    except KeyError as missing:
                        logger.warning(
                            "persist_fraud_signals: skipping malformed TRIGGERED_BY "
                            "ref %r on %s (%s)", ref, signal.signal_id, missing,
                        )
                count += 1
    except Exception as exc:  # noqa: BLE001 â€” must never crash the request path
        logger.warning(
            "persist_fraud_signals: failed after %d/%d for cin=%s (%s)",
            count, len(signals), cin, exc,
        )
    return count


# --- Provenance read (Stream 3.4) ------------------------------------------
# Reads back what `persist_fraud_signals` wrote so /provenance can stop
# re-running the scorer just to render the evidence chain.

_READ_PROVENANCE_FOR_CIN = """
MATCH (s:FraudSignal {cin: $cin})
OPTIONAL MATCH (s)-[:TRIGGERED_BY]->(e)
RETURN s.signal_id        AS signal_id,
       s.signal_type      AS signal_type,
       s.severity         AS severity,
       s.score_contribution AS score_contribution,
       s.evidence_string  AS evidence_string,
       s.module_name      AS module_name,
       collect(
         CASE WHEN e IS NULL THEN NULL
              ELSE {label: labels(e)[0], props: properties(e)}
         END
       ) AS refs
ORDER BY s.created_at
"""


async def read_provenance_for_cin(driver: AsyncDriver, cin: str) -> dict | None:
    """Return the `{signals, triggered_by}` provenance payload directly
    from Neo4j for `cin`. None on driver/Cypher failure â€” callers fall
    back to the in-memory evidence chain.

    Shape mirrors the in-memory builder in `analyse.py` so the route can
    swap transparently."""
    settings = get_settings()
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            result = await session.run(_READ_PROVENANCE_FOR_CIN, cin=cin)
            records = [r.data() async for r in result]
    except Exception as exc:  # noqa: BLE001 â€” defensive
        logger.warning("read_provenance_for_cin failed for %s (%s)", cin, exc)
        return None

    if not records:
        return None

    signals: list[dict[str, Any]] = []
    triggered_by: list[dict[str, Any]] = []
    for rec in records:
        signals.append({
            "signal_id": rec["signal_id"],
            "signal_type": rec["signal_type"],
            "severity": rec["severity"],
            "module_name": rec["module_name"],
            "score_contribution": rec["score_contribution"],
            "evidence_string": rec["evidence_string"],
        })
        for ref in rec.get("refs") or []:
            if not ref:
                continue
            triggered_by.append({
                "signal_id": rec["signal_id"],
                "label": ref.get("label"),
                "ref": ref.get("props") or {},
            })
    return {
        "cin": cin,
        "signal_count": len(signals),
        "signals": signals,
        "triggered_by": triggered_by,
    }

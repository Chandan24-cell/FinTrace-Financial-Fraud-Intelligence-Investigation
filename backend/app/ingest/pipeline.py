"""Ingestion pipeline: source -> validate -> write -> DQE -> DataConfidence.

PRD §10 Day 3 acceptance: '10 companies fetched, validated, written to Neo4j with
DataConfidence % set.' This is the orchestrator that makes that statement true.

The pipeline is structured so the Neo4j writer is injected — the pure validation
and DataConfidence logic can be exercised by tests without a live database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from pydantic import ValidationError

from backend.app.ingest.data_confidence import compute_data_confidence
from backend.app.ingest.schemas import CompanyBundle
from backend.app.ingest.sources import CompanySource
from backend.app.ingest.validation import DQErrorType, DataQualityError

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    cin: str
    ok: bool
    data_confidence: int | None = None
    errors: list[DataQualityError] = field(default_factory=list)
    error_message: str | None = None


# Type aliases for the injected writers — keeps the pipeline testable.
WriteBundle = Callable[[CompanyBundle], Awaitable[None]]
WriteDQE = Callable[[DataQualityError], Awaitable[None]]
SetConfidence = Callable[[str, int], Awaitable[None]]


async def ingest_one(
    cin: str,
    source: CompanySource,
    write_bundle: WriteBundle,
    write_dqe: WriteDQE,
    set_confidence: SetConfidence,
) -> IngestResult:
    """Fetch a single CIN, validate, write to Neo4j, compute DataConfidence."""
    errors: list[DataQualityError] = []

    # --- Fetch + validate ----------------------------------------------------
    try:
        bundle = await source.fetch_bundle(cin)
    except ValidationError as exc:
        dqe = DataQualityError.now(
            cin=cin,
            error_type=DQErrorType.SCHEMA_VALIDATION,
            field=";".join(".".join(str(p) for p in e["loc"]) for e in exc.errors()),
            actual_value=str(exc.errors()[:3]),
        )
        await write_dqe(dqe)
        return IngestResult(cin=cin, ok=False, errors=[dqe], error_message="Schema validation failed")
    except Exception as exc:  # noqa: BLE001 — surface any source error as DQE
        dqe = DataQualityError.now(
            cin=cin,
            error_type=DQErrorType.SCHEMA_VALIDATION,
            field="source.fetch_bundle",
            actual_value=type(exc).__name__,
        )
        await write_dqe(dqe)
        return IngestResult(cin=cin, ok=False, errors=[dqe], error_message=str(exc))

    if bundle is None:
        dqe = DataQualityError.now(
            cin=cin,
            error_type=DQErrorType.MISSING_REQUIRED_FIELD,
            field="company",
            expected_value=f"non-null bundle for {cin}",
            actual_value="None",
        )
        await write_dqe(dqe)
        return IngestResult(cin=cin, ok=False, errors=[dqe], error_message="Source returned no bundle")

    # --- Semantic validation (warnings — not blockers) ----------------------
    if bundle.company.employee_count_reported is None:
        dqe = DataQualityError.now(
            cin=cin,
            error_type=DQErrorType.MISSING_REQUIRED_FIELD,
            field="employee_count_reported",
            expected_value="non-null integer",
            actual_value="None",
        )
        errors.append(dqe)
        await write_dqe(dqe)

    if not bundle.financials:
        dqe = DataQualityError.now(
            cin=cin,
            error_type=DQErrorType.MISSING_REQUIRED_FIELD,
            field="financials",
            expected_value=">=1 FinancialStatement",
            actual_value="0",
        )
        errors.append(dqe)
        await write_dqe(dqe)

    # --- Write to graph -----------------------------------------------------
    try:
        await write_bundle(bundle)
    except Exception as exc:  # noqa: BLE001
        dqe = DataQualityError.now(
            cin=cin,
            error_type=DQErrorType.REFERENTIAL_INTEGRITY,
            field="write_bundle",
            actual_value=type(exc).__name__,
        )
        errors.append(dqe)
        await write_dqe(dqe)
        return IngestResult(
            cin=cin, ok=False, errors=errors, error_message=f"Write failed: {exc}"
        )

    # --- DataConfidence -----------------------------------------------------
    dc = compute_data_confidence(bundle)
    await set_confidence(cin, dc)

    return IngestResult(cin=cin, ok=True, data_confidence=dc, errors=errors)


async def ingest_many(
    cins: list[str],
    source: CompanySource,
    write_bundle: WriteBundle,
    write_dqe: WriteDQE,
    set_confidence: SetConfidence,
) -> list[IngestResult]:
    """Ingest a batch sequentially. PRD §10 Day 3 calls for 10 — sequential is fine.

    Day 7's 200-company and Day 17's 1000-company runs can swap this for
    asyncio.gather() with a semaphore once the real MCA21 client is rate-limited.
    """
    results: list[IngestResult] = []
    for cin in cins:
        result = await ingest_one(cin, source, write_bundle, write_dqe, set_confidence)
        results.append(result)
        logger.info(
            "Ingested %s — ok=%s confidence=%s errors=%d",
            cin, result.ok, result.data_confidence, len(result.errors),
        )
    return results

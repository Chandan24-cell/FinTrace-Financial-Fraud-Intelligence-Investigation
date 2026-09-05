"""Pipeline unit tests using FixtureSource and in-memory writers.

Validates:
  - Happy path: fixture flows through to DataConfidence set
  - Validation error: source raises -> DQE written, IngestResult.ok=False
  - Missing data: warnings emitted as DQEs but pipeline continues
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.ingest.pipeline import ingest_many, ingest_one
from backend.app.ingest.schemas import CompanyBundle
from backend.app.ingest.sources import FixtureSource
from backend.app.ingest.validation import DataQualityError


class _InMemoryWriters:
    def __init__(self) -> None:
        self.bundles: list[CompanyBundle] = []
        self.dqes: list[DataQualityError] = []
        self.confidences: dict[str, int] = {}

    async def write_bundle(self, b: CompanyBundle) -> None:
        self.bundles.append(b)

    async def write_dqe(self, d: DataQualityError) -> None:
        self.dqes.append(d)

    async def set_confidence(self, cin: str, score: int) -> None:
        self.confidences[cin] = score


@pytest.fixture
def writers() -> _InMemoryWriters:
    return _InMemoryWriters()


@pytest.fixture
def source() -> FixtureSource:
    return FixtureSource()


@pytest.mark.asyncio
async def test_happy_path_ilfs(writers: _InMemoryWriters, source: FixtureSource) -> None:
    """IL&FS fixture has 2 years + GST + bank -> DataConfidence should be 92."""
    cin = "U45201MH2005PTC155294"
    result = await ingest_one(
        cin, source, writers.write_bundle, writers.write_dqe, writers.set_confidence
    )
    assert result.ok is True
    assert result.data_confidence == 92
    assert len(writers.bundles) == 1
    assert writers.bundles[0].company.cin == cin
    assert writers.confidences[cin] == 92


@pytest.mark.asyncio
async def test_no_financials_fixture_flags_dqe(
    writers: _InMemoryWriters, source: FixtureSource
) -> None:
    """UVW Construction has no financials -> DQE emitted, DataConfidence 25%."""
    cin = "U45203MH2020PTC315678"
    result = await ingest_one(
        cin, source, writers.write_bundle, writers.write_dqe, writers.set_confidence
    )
    assert result.ok is True  # warnings don't fail the ingest
    assert result.data_confidence == 25
    assert any(d.field == "financials" for d in writers.dqes)


@pytest.mark.asyncio
async def test_missing_employee_count_emits_dqe(
    writers: _InMemoryWriters, source: FixtureSource
) -> None:
    """EFG Trading has no employee_count_reported -> DQE emitted."""
    cin = "U46190MH2019PTC295432"
    result = await ingest_one(
        cin, source, writers.write_bundle, writers.write_dqe, writers.set_confidence
    )
    assert result.ok is True
    assert any(d.field == "employee_count_reported" for d in writers.dqes)


@pytest.mark.asyncio
async def test_source_returns_none_emits_dqe(writers: _InMemoryWriters) -> None:
    class _EmptySource:
        name = "empty"

        async def fetch_bundle(self, cin: str):
            return None

        async def list_available_cins(self):
            return []

    result = await ingest_one(
        "U00000XX0000XXX000000",
        _EmptySource(),  # type: ignore[arg-type]
        writers.write_bundle, writers.write_dqe, writers.set_confidence,
    )
    assert result.ok is False
    assert len(writers.dqes) == 1
    assert writers.dqes[0].field == "company"


@pytest.mark.asyncio
async def test_source_validation_error_emits_dqe(writers: _InMemoryWriters) -> None:
    class _BadSource:
        name = "bad"

        async def fetch_bundle(self, cin: str):
            from pydantic_core import PydanticCustomError
            raise ValidationError.from_exception_data(
                "CompanyBundle",
                [{"type": "missing", "loc": ("company", "cin"), "input": {}}],
            )

        async def list_available_cins(self):
            return []

    result = await ingest_one(
        "U00000XX0000XXX000000",
        _BadSource(),  # type: ignore[arg-type]
        writers.write_bundle, writers.write_dqe, writers.set_confidence,
    )
    assert result.ok is False
    assert writers.dqes[0].error_type in {"SCHEMA_VALIDATION", "schema_validation"}


@pytest.mark.asyncio
async def test_ingest_many_ten_fixtures(writers: _InMemoryWriters, source: FixtureSource) -> None:
    """PRD Day-3 acceptance shape: 10 fixtures ingest cleanly."""
    cins = (await source.list_available_cins())[:10]
    assert len(cins) == 10

    results = await ingest_many(
        cins, source, writers.write_bundle, writers.write_dqe, writers.set_confidence
    )
    assert len(results) == 10
    ok_count = sum(1 for r in results if r.ok)
    # We expect all 10 to flow through. Some emit warning-level DQEs but still succeed.
    assert ok_count == 10
    assert len(writers.confidences) == 10

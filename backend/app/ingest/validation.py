"""Validation error model. PRD §3 DataQualityError node:
{cin, year, error_type, field, expected_value, actual_value, timestamp}

These are persisted to Neo4j (replacing the PostgreSQL audit log we removed).
The pipeline does NOT abort on validation failure — it records the error and
continues. DataConfidence is then docked accordingly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class DQErrorType(str, Enum):
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_FORMAT = "INVALID_FORMAT"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    REFERENTIAL_INTEGRITY = "REFERENTIAL_INTEGRITY"
    DUPLICATE_RECORD = "DUPLICATE_RECORD"
    CROSS_SOURCE_MISMATCH = "CROSS_SOURCE_MISMATCH"


class DataQualityError(BaseModel):
    """Mirrors the DataQualityError node in PRD §3."""

    model_config = ConfigDict(use_enum_values=True)

    cin: str
    year: int | None = None
    error_type: DQErrorType
    field: str
    expected_value: str | None = None
    actual_value: str | None = None
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        cin: str,
        error_type: DQErrorType,
        field: str,
        year: int | None = None,
        expected_value: Any = None,
        actual_value: Any = None,
    ) -> "DataQualityError":
        return cls(
            cin=cin,
            year=year,
            error_type=error_type,
            field=field,
            expected_value=None if expected_value is None else str(expected_value),
            actual_value=None if actual_value is None else str(actual_value),
            timestamp=datetime.now(timezone.utc),
        )

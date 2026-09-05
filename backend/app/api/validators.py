"""Path-parameter validators shared across routes (PRD §10 Day 24).

Single source of truth for the CIN regex pattern. Every public route uses
the `CIN_PATH` annotation below so that a malformed CIN never reaches the
handler — FastAPI returns 422 with a stable error shape instead.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

# PRD §3 — CIN format: letter + 5-digit NIC + 2-letter state + 4-digit year
# + 3-letter company-type + 6-digit serial. Verbatim regex used by the
# ingestion schema validators too (backend/app/ingest/schemas.py).
CIN_REGEX = r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$"

# FastAPI ≥0.111 uses Pydantic 2's `pattern=` for path params. Older
# versions accepted `regex=`. We pin to `pattern=` since pyproject.toml
# pins FastAPI 0.111.x (CLAUDE.md §11.1).
CIN_PATH = Annotated[
    str,
    Path(
        pattern=CIN_REGEX,
        description="Corporate Identification Number — PRD §3 21-char format",
        examples=["U45201MH2005PTC155294"],
    ),
]

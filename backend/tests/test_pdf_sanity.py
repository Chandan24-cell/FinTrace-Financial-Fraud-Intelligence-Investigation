"""PDF parser sanity checks — Stream 5.3.

Exercises `_validate_extracted_fields` directly with synthetic
PDFForensics / field dicts. Real PDF parsing is already covered by
existing fixtures in test_pdf_parser*.py; this file targets *just*
the new validation surface so a regression on the sanity rules is
detected as a fast unit failure instead of an end-to-end PDF run.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from backend.app.parse.pdf_parser import PDFForensics, _validate_extracted_fields


def _forensics(
    *,
    created: datetime | None = None,
    modified: datetime | None = None,
) -> PDFForensics:
    gap_days = (
        abs((modified - created).days) if (created and modified) else None
    )
    return PDFForensics(
        pdf_creation_software="Test",
        created_at=created,
        modified_at=modified,
        creation_mod_gap_days=gap_days,
        page_count=1,
        distinct_fonts=2,
        dpi_inconsistency=False,
    )


_DUMMY_PATH = Path("synthetic.pdf")


# ---------------------------------------------------------------------------
# Hard failure: total_assets <= 0.
# ---------------------------------------------------------------------------

def test_zero_total_assets_raises():
    with pytest.raises(ValueError, match="total_assets"):
        _validate_extracted_fields(
            {"total_assets": 0.0}, _forensics(), _DUMMY_PATH,
        )


def test_negative_total_assets_raises():
    with pytest.raises(ValueError, match="total_assets"):
        _validate_extracted_fields(
            {"total_assets": -1.0}, _forensics(), _DUMMY_PATH,
        )


def test_missing_total_assets_is_allowed():
    """An unset total_assets is a parse miss, not a violation —
    upstream validators decide whether to reject the bundle."""
    _validate_extracted_fields({}, _forensics(), _DUMMY_PATH)  # no raise
    _validate_extracted_fields(
        {"revenue": 1000.0}, _forensics(), _DUMMY_PATH,
    )  # no raise


# ---------------------------------------------------------------------------
# Hard failure: PDF metadata time travel.
# ---------------------------------------------------------------------------

def test_creation_after_modification_raises():
    created = datetime(2024, 6, 1)
    modified = datetime(2024, 1, 1)
    with pytest.raises(ValueError, match="creation date"):
        _validate_extracted_fields(
            {"total_assets": 1.0}, _forensics(created=created, modified=modified),
            _DUMMY_PATH,
        )


def test_creation_one_day_after_modification_is_tolerated():
    """Some PDF writers stamp creation slightly after modification by a
    few hours due to timezone-or-clock-skew; tolerate up to 1 day."""
    created = datetime(2024, 1, 2, 0, 0)
    modified = datetime(2024, 1, 1, 23, 59)
    _validate_extracted_fields(
        {"total_assets": 1.0}, _forensics(created=created, modified=modified),
        _DUMMY_PATH,
    )  # no raise


def test_only_creation_or_only_modification_is_allowed():
    """A PDF with only one timestamp can't be checked — allow extraction
    to continue."""
    _validate_extracted_fields(
        {"total_assets": 1.0}, _forensics(created=datetime(2024, 1, 1)),
        _DUMMY_PATH,
    )
    _validate_extracted_fields(
        {"total_assets": 1.0}, _forensics(modified=datetime(2024, 1, 1)),
        _DUMMY_PATH,
    )


# ---------------------------------------------------------------------------
# Soft warning: PAT > Revenue.
# ---------------------------------------------------------------------------

def test_pat_greater_than_revenue_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="backend.app.parse.pdf_parser"):
        _validate_extracted_fields(
            {"total_assets": 1.0, "revenue": 100.0, "pat": 200.0},
            _forensics(), _DUMMY_PATH,
        )
    msg = " ".join(r.message for r in caplog.records)
    assert "pat" in msg.lower()
    assert "revenue" in msg.lower()


def test_pat_less_than_or_equal_revenue_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="backend.app.parse.pdf_parser"):
        _validate_extracted_fields(
            {"total_assets": 1.0, "revenue": 100.0, "pat": 50.0},
            _forensics(), _DUMMY_PATH,
        )
    assert "pat" not in " ".join(r.message for r in caplog.records).lower()


# ---------------------------------------------------------------------------
# Soft warning: PAT > PBT.
# ---------------------------------------------------------------------------

def test_pat_significantly_above_pbt_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="backend.app.parse.pdf_parser"):
        _validate_extracted_fields(
            {"total_assets": 1.0, "pbt": 100.0, "pat": 120.0},
            _forensics(), _DUMMY_PATH,
        )
    msg = " ".join(r.message for r in caplog.records).lower()
    assert "pbt" in msg
    assert "negative tax" in msg


def test_pat_within_rounding_of_pbt_is_silent(caplog):
    """₹1 of slack — auditors round to nearest rupee."""
    with caplog.at_level(logging.WARNING, logger="backend.app.parse.pdf_parser"):
        _validate_extracted_fields(
            {"total_assets": 1.0, "pbt": 100.0, "pat": 100.5},
            _forensics(), _DUMMY_PATH,
        )
    # Should not produce the negative-tax warning.
    msg_text = " ".join(r.message for r in caplog.records).lower()
    assert "negative tax" not in msg_text


# ---------------------------------------------------------------------------
# Combined / no-fields baseline.
# ---------------------------------------------------------------------------

def test_empty_fields_dict_passes_silently():
    _validate_extracted_fields({}, _forensics(), _DUMMY_PATH)  # no raise, no log


def test_valid_full_set_passes():
    _validate_extracted_fields(
        {
            "total_assets": 5_000_000.0,
            "revenue": 1_200_000.0,
            "pbt": 150_000.0,
            "pat": 110_000.0,
        },
        _forensics(
            created=datetime(2024, 1, 1),
            modified=datetime(2024, 1, 15),
        ),
        _DUMMY_PATH,
    )

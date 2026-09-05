"""Tests for the pdfplumber-based AOC-4 parser (Day 4)."""

from pathlib import Path

import pytest

from backend.app.parse.pdf_parser import PDFForensics, parse_financial_pdf


def _make_pdf(path: Path, body: list[str]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 10)
    y = 800
    for line in body:
        c.drawString(40, y, line)
        y -= 16
    c.showPage()
    c.save()


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    out = tmp_path / "test_aoc4.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2022",
        "Form AOC-4",
        "Revenue from operations  : 1,240,000",
        "Cost of materials consumed: 891,000",
        "Employee benefits expense : 87,000",
        "Finance costs            : 142,000",
        "Depreciation and amortisation: 58,000",
        "Profit before tax        : 8,000",
        "Profit after tax         : 5,600",
        "Total assets             : 3,140,000",
        "Property, Plant and Equipment: 1,820,000",
        "Capital work-in-progress : 312,000",
        "Cash and cash equivalents: 41,000",
        "Trade receivables        : 487,000",
        "Long-term borrowings     : 1,740,000",
        "Short-term borrowings    : 412,000",
        "Opinion",
        "In our opinion, the financial statements give a true and fair view.",
        "Basis for Opinion",
        "For S Mehta and Associates  Chartered Accountants",
    ])
    return out


def test_parses_cin_and_year(synthetic_pdf: Path) -> None:
    stmt, _ = parse_financial_pdf(synthetic_pdf)
    assert stmt.cin == "U27101MH2010PTC215432"
    assert stmt.year == 2022


def test_parses_pl_fields(synthetic_pdf: Path) -> None:
    stmt, _ = parse_financial_pdf(synthetic_pdf)
    assert stmt.revenue == 1_240_000.0
    assert stmt.cost_of_materials == 891_000.0
    assert stmt.employee_cost == 87_000.0
    assert stmt.finance_costs == 142_000.0
    assert stmt.depreciation == 58_000.0
    assert stmt.pbt == 8_000.0
    assert stmt.pat == 5_600.0


def test_parses_balance_sheet_fields(synthetic_pdf: Path) -> None:
    stmt, _ = parse_financial_pdf(synthetic_pdf)
    assert stmt.total_assets == 3_140_000.0
    assert stmt.fixed_assets == 1_820_000.0
    assert stmt.cwip == 312_000.0
    assert stmt.cash_and_equivalents == 41_000.0
    assert stmt.receivables == 487_000.0
    assert stmt.long_term_borrowings == 1_740_000.0
    assert stmt.short_term_borrowings == 412_000.0


def test_extracts_auditor_name(synthetic_pdf: Path) -> None:
    stmt, _ = parse_financial_pdf(synthetic_pdf)
    assert "S Mehta and Associates" in stmt.auditor_name


def test_extracts_opinion_text(synthetic_pdf: Path) -> None:
    stmt, _ = parse_financial_pdf(synthetic_pdf)
    assert "true and fair view" in stmt.audit_opinion_text.lower()


def test_overrides_short_circuit_detection(tmp_path: Path) -> None:
    out = tmp_path / "no_anchors.pdf"
    _make_pdf(out, ["Revenue from operations  : 100"])
    stmt, _ = parse_financial_pdf(
        out, cin_override="U45201MH2005PTC155294", year_override=2017
    )
    assert stmt.cin == "U45201MH2005PTC155294"
    assert stmt.year == 2017
    assert stmt.revenue == 100.0


def test_forensics_metadata_populated(synthetic_pdf: Path) -> None:
    _, forensics = parse_financial_pdf(synthetic_pdf)
    assert isinstance(forensics, PDFForensics)
    assert forensics.page_count == 1
    assert forensics.distinct_fonts >= 1


def test_missing_anchors_raise(tmp_path: Path) -> None:
    out = tmp_path / "blank.pdf"
    _make_pdf(out, ["This document has nothing useful"])
    with pytest.raises(ValueError, match="Could not locate CIN"):
        parse_financial_pdf(out)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_financial_pdf(tmp_path / "nope.pdf")

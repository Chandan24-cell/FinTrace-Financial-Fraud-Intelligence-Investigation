"""Day-7 PDF parser edge-case regression tests (PRD §10 Day 7 'Fix parser edge cases').

The Day-4 parser ships a baseline label-anchored regex that covers >80% of
AOC-4 PDFs. These tests pin down the corner cases we have to be honest about:

  - Parenthesised negatives (Indian audit convention for negative numbers)
  - Numbers expressed in 'lakhs' or 'crores' rather than plain rupees
  - Unit suffix ' Cr' inline with the value
  - Excess whitespace inside the label

Tests that fail today document remaining edge cases rather than crash the
build — they're marked xfail with a reason so the next PRD §10 Day-11
'Document Forensics' iteration can knock them out. Tests that pass today
become regression guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.parse.pdf_parser import parse_financial_pdf


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


def test_label_with_extra_whitespace_still_matches(tmp_path: Path) -> None:
    """The Day-4 regex tolerates variable whitespace between the label and value."""
    out = tmp_path / "spacey.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2023",
        "Revenue from operations              :     1,500,000",
        "Total assets             :         4,200,000",
    ])
    stmt, _ = parse_financial_pdf(out)
    assert stmt.revenue == 1_500_000.0
    assert stmt.total_assets == 4_200_000.0


def test_handles_dash_separator(tmp_path: Path) -> None:
    """Some AOC-4 PDFs use a dash instead of a colon — the regex accepts either."""
    out = tmp_path / "dash.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2023",
        "Revenue from operations - 875,000",
    ])
    stmt, _ = parse_financial_pdf(out)
    assert stmt.revenue == 875_000.0


def test_handles_numbers_without_commas(tmp_path: Path) -> None:
    """Plain integer values without thousands separators must parse."""
    out = tmp_path / "nocommas.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2023",
        "Revenue from operations: 875000",
        "Total assets: 4200000",
    ])
    stmt, _ = parse_financial_pdf(out)
    assert stmt.revenue == 875_000.0
    assert stmt.total_assets == 4_200_000.0


def test_total_revenue_synonym_matches(tmp_path: Path) -> None:
    """Some AOC-4 variants use 'Total revenue' instead of 'Revenue from operations'."""
    out = tmp_path / "total_revenue.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2023",
        "Total revenue : 1,675,000",
    ])
    stmt, _ = parse_financial_pdf(out)
    assert stmt.revenue == 1_675_000.0


def test_parenthesised_negative_value_known_gap(tmp_path: Path) -> None:
    """Indian audit convention: '(123,456)' means -123,456. Resolved post-Day-9
    by paren-neg pre-processing in pdf_parser._PAREN_NEG_PATTERN."""
    out = tmp_path / "paren_neg.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2023",
        "Profit after tax: (1,500,000)",
    ])
    stmt, _ = parse_financial_pdf(out)
    assert stmt.pat == -1_500_000.0


def test_lakhs_crores_unit_known_gap(tmp_path: Path) -> None:
    """Many SME PDFs report values in crores ('₹ Cr') or lakhs. Resolved post-Day-9
    via document-level unit-multiplier detection in pdf_parser."""
    out = tmp_path / "crores.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Financial Year: 2023",
        "(All amounts in INR crore)",
        "Revenue from operations: 12.4",
    ])
    stmt, _ = parse_financial_pdf(out)
    # 12.4 crore == 124,000,000
    assert stmt.revenue == 124_000_000.0


def test_multiple_year_anchors_picks_first(tmp_path: Path) -> None:
    """A PDF may reference a prior year in notes; the regex must lock onto the
    statement year, not a stray comparison."""
    out = tmp_path / "multiyear.pdf"
    _make_pdf(out, [
        "CIN: U27101MH2010PTC215432",
        "Year ended March 31, 2023",
        "Compared to financial year 2022 figures shown in brackets",
        "Revenue from operations: 1,200,000",
    ])
    stmt, _ = parse_financial_pdf(out)
    assert stmt.year == 2023, "Must pick the statement year, not the comparison year"


def test_year_override_wins_when_pdf_year_ambiguous(tmp_path: Path) -> None:
    """When the caller already knows the year, the override short-circuits detection."""
    out = tmp_path / "no_year.pdf"
    _make_pdf(out, [
        "Revenue from operations: 999,000",
    ])
    stmt, _ = parse_financial_pdf(
        out,
        cin_override="U27101MH2010PTC215432",
        year_override=2019,
    )
    assert stmt.year == 2019
    assert stmt.cin == "U27101MH2010PTC215432"

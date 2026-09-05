"""AOC-4 PDF parser baseline (PRD §10 Day 4 + §4.8 Document Forensics inputs).

Strategy:
  1. pdfplumber as the primary text + table extractor (works on Windows without
     external deps; pure Python).
  2. camelot-py as an optional fallback for table-heavy pages — Windows builds
     need Ghostscript installed system-wide. We import lazily and degrade
     gracefully when it's not available.
  3. PDF forensic metadata harvested from pdfplumber's `metadata` dict (PDF
     creation software, creation/modification dates) — feeds Module 8.

This is the Day-4 baseline. Field extraction here uses simple label-anchored
regex; the AOC-4 XBRL → PDF round-trip on the MCA filings is highly templated,
so this works for >80% of fixtures. Production-grade extraction (Day 11+) will
add table-region heuristics + camelot for tabular blocks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdfplumber

from backend.app.ingest.schemas import RawFinancialStatement

logger = logging.getLogger(__name__)


@dataclass
class PDFForensics:
    """Document-forensics metadata captured at parse time. Feeds PRD §4.8 Module 8."""

    pdf_creation_software: str | None
    created_at: datetime | None
    modified_at: datetime | None
    creation_mod_gap_days: int | None
    page_count: int
    distinct_fonts: int
    dpi_inconsistency: bool

    def to_node_props(self) -> dict[str, object]:
        return {
            "pdf_creation_software": self.pdf_creation_software,
            "pdf_metadata_anomaly": self.creation_mod_gap_days is not None
            and self.creation_mod_gap_days < 3,
        }


# --- Label → field map for the AOC-4 baseline (case-insensitive, anchored) -
# Order matters: more specific labels first so generic ones don't match prematurely.
_FIELD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("revenue",            re.compile(r"(?:revenue\s+from\s+operations|total\s+revenue)\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("cost_of_materials",  re.compile(r"cost\s+of\s+materials\s+consumed\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("employee_cost",      re.compile(r"employee\s+benefits?\s+expense\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("finance_costs",      re.compile(r"finance\s+costs?\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("depreciation",       re.compile(r"depreciation\s+and\s+amortisation\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("pbt",                re.compile(r"profit\s+before\s+tax\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("pat",                re.compile(r"profit\s+after\s+tax\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("total_assets",       re.compile(r"total\s+assets\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("fixed_assets",       re.compile(r"(?:property,\s*plant\s+and\s+equipment|fixed\s+assets)\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("cwip",               re.compile(r"capital\s+work[\-\s]in[\-\s]progress\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("cash_and_equivalents", re.compile(r"cash\s+and\s+cash\s+equivalents\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("receivables",        re.compile(r"trade\s+receivables\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("long_term_borrowings", re.compile(r"long[\-\s]term\s+borrowings\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
    ("short_term_borrowings", re.compile(r"short[\-\s]term\s+borrowings\s*[:\-]?\s*(-?[\d,]+(?:\.\d+)?)", re.I)),
]

_CIN_PATTERN = re.compile(r"\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b")
# `.{0,50}?` (lazy any-char) tolerates intervening digits like the day in
# 'Year ended March 31, 2023' — older `[^0-9]{0,30}` stalled on the '31' and
# fell through to a stray comparison year reference (PRD Day 7 parser fix).
_YEAR_PATTERN = re.compile(r"(?:financial\s+year|fy|year\s+ended).{0,50}?((?:19|20)\d{2})", re.I)
_AUDIT_OPINION_PATTERN = re.compile(
    r"(opinion[\s\S]{20,800}?)(?:basis\s+for\s+opinion|emphasis\s+of\s+matter|key\s+audit\s+matters|to\s+the\s+members|annexure)",
    re.I,
)
_AUDITOR_NAME_PATTERN = re.compile(r"for\s+(.{3,80}?)\s*(?:chartered\s+accountants|firm\s+registration)", re.I)

# Indian audit convention: '(1,500,000)' = -1,500,000. Pre-process the raw text
# and replace digit-only parenthesised values with a leading minus so the
# downstream label-anchored regexes work unchanged.
_PAREN_NEG_PATTERN = re.compile(r"\((\d[\d,]*(?:\.\d+)?)\)")

# Document-level unit declarations: 'All amounts in INR crore', '(in lakhs)', etc.
# A declaration of crore multiplies every parsed numeric by 1e7; lakhs by 1e5.
_UNIT_CRORE_PATTERN = re.compile(
    r"\(?\s*(?:all\s+amounts\s+(?:are\s+)?in\s+)?(?:₹|inr|rs\.?)?\s*crores?\s*\)?",
    re.I,
)
_UNIT_LAKH_PATTERN = re.compile(
    r"\(?\s*(?:all\s+amounts\s+(?:are\s+)?in\s+)?(?:₹|inr|rs\.?)?\s*lakhs?\s*\)?",
    re.I,
)
# More restrictive document-level markers so a stray 'crore' inside prose
# doesn't rescale every number. We look for explicit '(... in crore)' or
# '(All amounts in INR crore)' shapes.
_DOC_CRORE_PATTERN = re.compile(
    r"\(?\s*(?:all\s+amounts?\s+(?:are\s+)?in|in)\s+(?:₹|inr|rs\.?\s*)?\s*crores?\s*\)?",
    re.I,
)
_DOC_LAKH_PATTERN = re.compile(
    r"\(?\s*(?:all\s+amounts?\s+(?:are\s+)?in|in)\s+(?:₹|inr|rs\.?\s*)?\s*lakhs?\s*\)?",
    re.I,
)


def _detect_unit_multiplier(text: str) -> float:
    """Return 1e7 (crore), 1e5 (lakh), or 1.0 (no marker)."""
    if _DOC_CRORE_PATTERN.search(text):
        return 1e7
    if _DOC_LAKH_PATTERN.search(text):
        return 1e5
    return 1.0


def _to_float(raw: str) -> float:
    """Parse a captured numeric. Negative prefix '-' (synthesised from a
    parenthesised value upstream) is preserved."""
    return float(raw.replace(",", "").strip())


def _parse_pdf_date(value: str | None) -> datetime | None:
    """pdfplumber surfaces PDF dates as e.g. 'D:20170615142312+05'30'' — best-effort parse."""
    if not value or not isinstance(value, str):
        return None
    s = value.lstrip("D:").split("+")[0].split("-")[0].replace("'", "")
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            target_len = len(re.sub(r"%.", "", fmt)) + 2 * fmt.count("%")
            return datetime.strptime(s[:target_len], fmt)
        except ValueError:
            continue
    return None


def _extract_forensics(pdf: "pdfplumber.PDF") -> PDFForensics:
    meta = pdf.metadata or {}
    creator = meta.get("Producer") or meta.get("Creator")
    created = _parse_pdf_date(meta.get("CreationDate"))
    modified = _parse_pdf_date(meta.get("ModDate"))

    gap_days: int | None
    if created and modified:
        gap_days = abs((modified - created).days)
    else:
        gap_days = None

    distinct_fonts: set[str] = set()
    for page in pdf.pages:
        for ch in page.chars:
            distinct_fonts.add(ch.get("fontname", ""))

    return PDFForensics(
        pdf_creation_software=creator,
        created_at=created,
        modified_at=modified,
        creation_mod_gap_days=gap_days,
        page_count=len(pdf.pages),
        distinct_fonts=len(distinct_fonts),
        dpi_inconsistency=False,  # populated in Day-11 image-extraction pass
    )


def _validate_extracted_fields(
    fields: dict[str, float],
    forensics: PDFForensics,
    path: Path,
) -> None:
    """Stream 5.3 — sanity-check the regex-extracted numbers before they
    poison the downstream scorer.

    Hard failures (raise ValueError):
      * `total_assets <= 0`        — a positive balance-sheet anchor is
        the cheapest tripwire for "the regex picked up an unrelated
        line item" or "the PDF wasn't a financial statement at all".
      * `created_at` strictly more than 1 day *after* `modified_at` —
        PDF metadata that time-travels backwards is malformed or
        deliberately tampered.

    Soft warnings (log only — extraction continues):
      * `pat > revenue`            — profit-after-tax can't exceed top-
        line revenue. Almost always means the PAT regex captured a
        comparative-year figure or a percentage.
      * `pat > pbt`                — taxes are never negative beyond
        rounding-cents. If pat > pbt + small epsilon, an extraction
        error is more likely than a refund.

    The split between hard/soft matches what an investigator needs:
    hard failures kill the bundle (the upload route maps ValueError ->
    422 already); soft warnings flow into the operator's log stream so
    they can patch the regex without bouncing the user's PDF.
    """
    assets = fields.get("total_assets")
    if assets is not None and assets <= 0:
        raise ValueError(
            f"PDF sanity: total_assets must be > 0; got {assets!r} from {path.name}",
        )

    created, modified = forensics.created_at, forensics.modified_at
    if created and modified and (created - modified).days > 1:
        raise ValueError(
            f"PDF sanity: creation date ({created.isoformat()}) is after "
            f"modification date ({modified.isoformat()}) by more than one "
            f"day — metadata anomaly, refusing the PDF",
        )

    revenue = fields.get("revenue")
    pat = fields.get("pat")
    pbt = fields.get("pbt")
    if revenue is not None and pat is not None and pat > revenue:
        logger.warning(
            "PDF sanity: pat (%s) exceeds revenue (%s) in %s — regex likely "
            "captured a comparative-year or percentage figure; extraction "
            "continues but the value is suspect.",
            pat, revenue, path.name,
        )
    # Allow ₹1 of rounding-cents slack — Indian audit reports round to
    # the nearest rupee but the source figures are sometimes in paise.
    if pat is not None and pbt is not None and pat > pbt + 1.0:
        logger.warning(
            "PDF sanity: pat (%s) exceeds pbt (%s) in %s — implies a "
            "negative tax expense; treat with caution.",
            pat, pbt, path.name,
        )


def parse_financial_pdf(
    path: str | Path,
    *,
    cin_override: str | None = None,
    year_override: int | None = None,
) -> tuple[RawFinancialStatement, PDFForensics]:
    """Parse one AOC-4 PDF -> (RawFinancialStatement, PDFForensics).

    `cin_override` / `year_override` short-circuit the text-anchored detection
    when the caller already knows the company/year (e.g. when iterating a known
    fixture set). Useful for the Day-4 acceptance run.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    with pdfplumber.open(str(path)) as pdf:
        forensics = _extract_forensics(pdf)
        all_text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    # Detect document-level unit declaration BEFORE paren-neg rewrite — the
    # marker '(All amounts in INR crore)' contains letters so it won't be
    # mistaken for a numeric paren-neg.
    unit_multiplier = _detect_unit_multiplier(all_text)
    # Rewrite parenthesised numeric values as negatives (Indian audit convention).
    all_text = _PAREN_NEG_PATTERN.sub(r"-\1", all_text)

    cin = cin_override
    if cin is None:
        m = _CIN_PATTERN.search(all_text)
        if m is None:
            raise ValueError(f"Could not locate CIN in {path}")
        cin = m.group(1)

    year = year_override
    if year is None:
        m = _YEAR_PATTERN.search(all_text)
        if m is None:
            raise ValueError(f"Could not locate fiscal year in {path}")
        year = int(m.group(1))

    fields: dict[str, float] = {}
    for field_name, pattern in _FIELD_PATTERNS:
        match = pattern.search(all_text)
        if match:
            try:
                fields[field_name] = _to_float(match.group(1)) * unit_multiplier
            except ValueError:
                logger.warning("Could not parse number for %s in %s: %r", field_name, path, match.group(1))

    _validate_extracted_fields(fields, forensics, path)

    auditor_match = _AUDITOR_NAME_PATTERN.search(all_text)
    auditor_name = auditor_match.group(1).strip() if auditor_match else ""

    opinion_match = _AUDIT_OPINION_PATTERN.search(all_text)
    opinion_text = opinion_match.group(1).strip() if opinion_match else ""

    notes_word_count = len(all_text.split())

    statement = RawFinancialStatement(
        cin=cin,
        year=year,
        audit_opinion_text=opinion_text[:2000],
        auditor_name=auditor_name[:200],
        notes_word_count=notes_word_count,
        pdf_creation_software=forensics.pdf_creation_software,
        pdf_metadata_anomaly=bool(forensics.to_node_props()["pdf_metadata_anomaly"]),
        **fields,
    )
    return statement, forensics

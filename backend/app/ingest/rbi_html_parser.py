"""RBI Wilful Defaulter HTML table parser (PRD §10 Phase B free-source).

RBI publishes the web version of the wilful-defaulter / suit-filed lists
at rbi.org.in (`Database on Indian Economy → Defaulters`). The booklet /
CD compilation is priced; the web list is free. We parse the HTML tables
directly with the stdlib `html.parser` — no new dependencies, identical
strategy to the NCLT cause-list parser (see `nclt.py:parse_cause_list_html`).

Tolerant of column-order drift: we locate the CIN cell via the canonical
CIN regex rather than positional indexing, so a layout reshuffle on
rbi.org.in does not silently corrupt the dataset. Date parsing accepts
DD-MM-YYYY, DD/MM/YYYY, "DD Mon YYYY", and ISO. Amounts strip Indian
comma grouping ("1,25,50,000.00" → 1255000.0).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from html.parser import HTMLParser

from backend.app.ingest.wilful_defaulter import RawWilfulDefaulter

logger = logging.getLogger(__name__)


_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_DIN_RE = re.compile(r"^\d{8}$")
_AMOUNT_CLEAN_RE = re.compile(r"[^\d.]")
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d %b %Y",
    "%d %B %Y",
)


class _RBITableExtractor(HTMLParser):
    """Stream-parse <table>...</table> blocks into list-of-rows.

    Identical mechanics to NCLT's _CauseListTableExtractor; kept as a
    separate class so a future RBI-specific quirk (e.g. nested <p> tags
    inside <td>) can be handled here without touching NCLT.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._row_buf: list[str] = []
        self._table_buf: list[list[str]] = []
        self.tables: list[list[list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._table_buf = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row_buf = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._row_buf.append("".join(self._cell_buf).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row_buf:
                self._table_buf.append(self._row_buf)
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._table_buf:
                self.tables.append(self._table_buf)
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)


def _parse_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> float:
    """Strip currency symbols + thousands separators. RBI publishes ₹ in lakh
    *and* crore in the same table depending on era — we trust the number
    the page emits and let the downstream module normalise (M9 only cares
    that a defaulter row exists; absolute amount is informational)."""
    if not raw:
        return 0.0
    cleaned = _AMOUNT_CLEAN_RE.sub("", raw)
    if not cleaned or cleaned == ".":
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _find_cell(row: list[str], pattern: re.Pattern[str]) -> str | None:
    for cell in row:
        if pattern.match(cell.strip()):
            return cell.strip()
    return None


def parse_rbi_wilful_html(
    html: str,
    *,
    source: str = "RBI",
    default_declared_date: date | None = None,
) -> list[RawWilfulDefaulter]:
    """Parse one RBI wilful-defaulter HTML page into RawWilfulDefaulter rows.

    Args:
      html: Raw HTML as served by rbi.org.in.
      source: Marker copied onto every emitted record. Use "RBI" for the
        non-suit-filed list, "SUIT_FILED" for the suit-filed list, "CIBIL"
        for CIBIL's public defaulter list.
      default_declared_date: Fallback when the row has no parseable date —
        typically the quarter-end the operator pulled the list on. None
        means: skip rows with no date.

    Returns:
      Validated RawWilfulDefaulter records. Rows missing CIN, bank name,
      or amount are skipped (logged at warning).
    """
    parser = _RBITableExtractor()
    parser.feed(html)
    parser.close()

    out: list[RawWilfulDefaulter] = []
    for table in parser.tables:
        if not table or len(table) < 2:
            continue
        header = [c.strip().lower() for c in table[0]]
        idx_bank = next(
            (i for i, h in enumerate(header) if "bank" in h or "lender" in h),
            -1,
        )
        idx_amount = next(
            (i for i, h in enumerate(header) if "amount" in h or "outstanding" in h),
            -1,
        )
        idx_date = next(
            (i for i, h in enumerate(header) if "date" in h or "declared" in h),
            -1,
        )

        for row in table[1:]:
            if not row:
                continue
            cin = _find_cell(row, _CIN_RE)
            if not cin:
                continue
            din = _find_cell(row, _DIN_RE)

            bank = row[idx_bank].strip() if 0 <= idx_bank < len(row) else ""
            amount = (
                _parse_amount(row[idx_amount]) if 0 <= idx_amount < len(row) else 0.0
            )
            declared_str = row[idx_date] if 0 <= idx_date < len(row) else ""
            declared = _parse_date(declared_str) or default_declared_date

            if not bank or declared is None:
                logger.warning(
                    "RBI row skipped (missing bank/date): cin=%s row=%r", cin, row,
                )
                continue

            try:
                out.append(RawWilfulDefaulter(
                    cin=cin,
                    din=din,
                    bank_name=bank,
                    amount=max(amount, 0.0),
                    declared_date=declared,
                    source=source,
                ))
            except Exception as exc:  # pragma: no cover — Pydantic error path
                logger.warning("RBI row failed validation: %s — %r", exc, row)

    return out

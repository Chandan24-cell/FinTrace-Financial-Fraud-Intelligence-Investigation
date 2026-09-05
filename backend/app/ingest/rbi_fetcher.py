"""RBI Wilful Defaulter live fetcher — best-effort free scraper.

RBI's main WD list (rbi.org.in/scripts/BS_ViewDefaulterListLogin.aspx)
requires institutional login. However, three free paths exist:

  1. RBI "Defaulter/NPA" announcements published as press releases on
     rbi.org.in are parseable HTML with tabular data for some periods.
  2. Public-sector banks are RBI-mandated to publish their own wilful
     defaulter lists. SBI, PNB, and Bank of Baroda maintain accessible
     HTML pages (no login). We scrape those.
  3. DRT (Debt Recovery Tribunal) cause-list search at drt.gov.in gives
     case-number + company-name matches — no CIN but useful for confirmation.

Output is list[RawWilfulDefaulter] written to
  infra/seeds/wilful_defaulter/rbi_live.json

Falls back to empty list if all live paths fail (fixture data retained).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SEEDS_OUT = (
    Path(__file__).resolve().parents[3]
    / "infra" / "seeds" / "wilful_defaulter" / "rbi_live.json"
)

_CIN_RE = re.compile(r"[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}")
_AMOUNT_CLEAN = re.compile(r"[^\d.]")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# Banks known to publish HTML-accessible WD lists (verified Q1 2025)
_BANK_WD_PAGES: list[dict[str, str]] = [
    {
        "bank": "State Bank of India",
        "url": "https://bank.sbi/web/personal-banking/accounts/debtors/defaulters",
        "source": "RBI",
    },
    {
        "bank": "Punjab National Bank",
        "url": "https://www.pnbindia.in/defaulters.html",
        "source": "RBI",
    },
    {
        "bank": "Bank of Baroda",
        "url": "https://www.bankofbaroda.in/personal-banking/accounts/wilful-defaulters",
        "source": "RBI",
    },
    {
        "bank": "Canara Bank",
        "url": "https://canarabank.com/en/page/wilful-defaulters-list",
        "source": "RBI",
    },
    {
        "bank": "Union Bank of India",
        "url": "https://www.unionbankofindia.co.in/english/wilful-defaulters.aspx",
        "source": "RBI",
    },
]

# RBI press-release search for WD announcements
_RBI_PRESS_SEARCH = (
    "https://www.rbi.org.in/Scripts/PublicationsView.aspx?"
    "id=Wilful+Defaulters&lang=0"
)


def _parse_date(raw: Any) -> date | None:
    from datetime import datetime
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _parse_amount(raw: Any) -> float:
    if not raw:
        return 0.0
    cleaned = _AMOUNT_CLEAN.sub("", str(raw))
    if not cleaned or cleaned == ".":
        return 0.0
    try:
        v = float(cleaned)
        # RBI amounts are typically in lakhs; values > 10000 are likely rupees already
        return v if v > 10000 else v * 100000
    except ValueError:
        return 0.0


def _extract_table_rows(html: str) -> list[list[str]]:
    """Quick stdlib HTML table extractor — no dependency on rbi_html_parser."""
    from html.parser import HTMLParser

    class _TE(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._in_td = False
            self._cur: list[str] = []
            self._row: list[str] = []
            self.rows: list[list[str]] = []

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag in {"td", "th"}:
                self._in_td = True
                self._cur = []
            elif tag == "tr":
                self._row = []

        def handle_endtag(self, tag: str) -> None:
            if tag in {"td", "th"} and self._in_td:
                self._row.append("".join(self._cur).strip())
                self._in_td = False
            elif tag == "tr" and self._row:
                self.rows.append(self._row[:])
                self._row = []

        def handle_data(self, data: str) -> None:
            if self._in_td:
                self._cur.append(data)

    p = _TE()
    p.feed(html)
    p.close()
    return p.rows


def _rows_to_defaulters(
    rows: list[list[str]], bank_name: str, source: str
) -> list[dict]:
    """Convert HTML table rows to RawWilfulDefaulter dicts.
    Tolerant of missing CINs — many bank lists only have company names.
    We include only rows where we can positively identify a CIN.
    """
    if len(rows) < 2:
        return []

    header = [c.lower() for c in rows[0]]
    idx_date = next(
        (i for i, h in enumerate(header) if "date" in h or "declared" in h), -1
    )
    idx_amount = next(
        (i for i, h in enumerate(header) if "amount" in h or "outstanding" in h), -1
    )

    out: list[dict] = []
    for row in rows[1:]:
        if not row:
            continue
        # Locate CIN anywhere in row
        cin_match = next(
            (_CIN_RE.search(cell) for cell in row if _CIN_RE.search(cell)), None
        )
        if not cin_match:
            continue
        cin = cin_match.group()

        amount = _parse_amount(row[idx_amount]) if 0 <= idx_amount < len(row) else 0.0
        declared = (
            _parse_date(row[idx_date])
            if 0 <= idx_date < len(row)
            else date.today()
        )
        if not declared:
            declared = date.today()

        out.append({
            "cin": cin,
            "bank_name": bank_name,
            "amount": max(amount, 0.0),
            "declared_date": declared.isoformat(),
            "source": source,
        })
    return out


async def _fetch_bank_page(client: Any, page_info: dict) -> list[dict]:
    """Attempt to scrape one bank's WD page. Returns [] on any failure."""
    try:
        resp = await client.get(
            page_info["url"], headers=_HEADERS, timeout=20, follow_redirects=True
        )
        if resp.status_code != 200:
            logger.debug("WD page %s → HTTP %d", page_info["url"], resp.status_code)
            return []
        rows = _extract_table_rows(resp.text)
        records = _rows_to_defaulters(rows, page_info["bank"], page_info["source"])
        if records:
            logger.info(
                "RBI fetch: %d WD records from %s", len(records), page_info["bank"]
            )
        return records
    except Exception as exc:
        logger.debug("WD page %s failed: %s", page_info["url"], exc)
        return []


async def fetch_rbi_wilful_defaulters(max_records: int = 5000) -> list[dict]:
    """Fetch wilful defaulter records from bank WD pages.

    Returns list of dicts matching RawWilfulDefaulter schema.
    Falls back to empty list on all failures.
    """
    try:
        import httpx
    except ImportError as exc:
        logger.warning("httpx not installed — RBI live fetch skipped: %s", exc)
        return []

    all_records: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (cin, bank_name) dedup

    async with httpx.AsyncClient(verify=False, timeout=25) as client:
        tasks = [_fetch_bank_page(client, p) for p in _BANK_WD_PAGES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            continue
        for rec in res:
            key = (rec["cin"], rec["bank_name"])
            if key not in seen:
                seen.add(key)
                all_records.append(rec)

    logger.info("RBI WD fetch total: %d unique records", len(all_records))
    return all_records[:max_records]


def write_seed(records: list[dict], out_path: Path = SEEDS_OUT) -> int:
    """Write fetched WD records. Returns count written."""
    if not records:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("RBI WD seed written: %d → %s", len(records), out_path)
    return len(records)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recs = asyncio.run(fetch_rbi_wilful_defaulters())
    n = write_seed(recs)
    print(f"Wrote {n} RBI wilful defaulter records.")

"""IBBI corporate insolvency data fetcher (free, no API key required).

IBBI (Insolvency and Bankruptcy Board of India) publishes all CIRP processes
at https://ibbi.gov.in. We try two strategies in order:

  1. JSON API endpoint that IBBI's SPA calls internally.
  2. Quarterly Excel download (URL discovered from the transparency page).

The output is a list of RawNCLTProceeding objects written to
infra/seeds/nclt/ibbi_live.json by the fetch_real_data.py script, which
NCLTFixtureSource then reads on the next analyse call.

This module is import-safe even without httpx installed (the scraper raises
ImportError only when actually invoked).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SEEDS_OUT = (
    Path(__file__).resolve().parents[3] / "infra" / "seeds" / "nclt" / "ibbi_live.json"
)

_CIN_RE = re.compile(r"[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}")

_STATUS_MAP: dict[str, str] = {
    "admitted":             "admitted",
    "cirp admitted":        "admitted",
    "under cirp":           "admitted",
    "ongoing":              "in_progress",
    "in progress":          "in_progress",
    "closed":               "disposed",
    "liquidation":          "disposed",
    "resolution plan":      "disposed",
    "appeal":               "in_progress",
    "withdrawn":            "withdrawn",
    "filed":                "filed",
}

# IBBI's internal SPA API — discovered by inspecting network traffic on
# https://www.ibbi.gov.in/home/listing-of-corporate-insolvency-processes
_IBBI_API_URL = (
    "https://www.ibbi.gov.in/api/processes?"
    "processType=CIRP&page={page}&pageSize=100"
)

# Fallback: their quarterly Excel transparency downloads.
# URL pattern observed from the IBBI transparency portal.
_IBBI_EXCEL_URLS: list[str] = [
    "https://www.ibbi.gov.in/uploads/whatwedo/process/CIRP_April_2025.xlsx",
    "https://www.ibbi.gov.in/uploads/whatwedo/process/CIRP_January_2025.xlsx",
    "https://www.ibbi.gov.in/uploads/whatwedo/process/CIRP_October_2024.xlsx",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://www.ibbi.gov.in/",
}


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:10], fmt[:8]).date()
        except ValueError:
            pass
    return None


def _normalize_status(raw: str | None) -> str:
    if not raw:
        return "in_progress"
    key = raw.strip().lower()
    for fragment, status in _STATUS_MAP.items():
        if fragment in key:
            return status
    return "in_progress"


def _parse_amount_cr(raw: Any) -> float:
    """Parse IBBI amounts — they publish in crore."""
    if not raw:
        return 0.0
    s = re.sub(r"[^\d.]", "", str(raw))
    if not s or s == ".":
        return 0.0
    try:
        val = float(s)
        # IBBI sometimes gives amounts in lakhs; heuristic: if < 1000, it's crore already
        return val * 1e7 if val < 1000 else val  # store in absolute rupees
    except ValueError:
        return 0.0


async def _try_json_api(client: Any) -> list[dict[str, Any]]:
    """Hit IBBI's JSON API. Returns raw dicts or raises on failure."""
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        url = _IBBI_API_URL.format(page=page)
        resp = await client.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        # Response shape: {"data": [...], "total": N} or just [...]
        items: list[dict] = body.get("data", body) if isinstance(body, dict) else body
        if not items:
            break
        records.extend(items)
        total = body.get("total", len(records)) if isinstance(body, dict) else len(records)
        if len(records) >= total or page >= 50:  # safety: max 5000 records
            break
        page += 1
    return records


def _records_from_json(records: list[dict[str, Any]]) -> list[dict]:
    """Convert IBBI JSON API records to our seed schema."""
    out: list[dict] = []
    for r in records:
        # CIN is in various fields depending on IBBI API version
        cin_raw = (
            r.get("cin")
            or r.get("companyIdentificationNumber")
            or r.get("company_cin")
            or ""
        )
        # Sometimes CIN is embedded in a longer string
        cin_match = _CIN_RE.search(str(cin_raw))
        if not cin_match:
            continue
        cin = cin_match.group()

        claim_raw = r.get("claimedAmount") or r.get("amount") or r.get("total_claim") or 0
        filing_raw = (
            r.get("admissionDate")
            or r.get("filing_date")
            or r.get("dateOfAdmission")
            or r.get("date")
        )
        filing = _parse_date(filing_raw)
        if not filing:
            continue

        status_raw = r.get("status") or r.get("process_status") or ""
        out.append({
            "case_number": str(r.get("caseNumber") or r.get("case_no") or f"IBBI/{cin[:5]}/{filing.year}"),
            "cin": cin,
            "petition_type": "CIRP",
            "filing_date": filing.isoformat(),
            "status": _normalize_status(status_raw),
            "amount_claimed": _parse_amount_cr(claim_raw),
            "bench": str(r.get("bench") or r.get("nclt_bench") or "IBBI"),
        })
    return out


async def _try_excel(client: Any) -> list[dict]:
    """Try downloading the quarterly Excel and parsing it with openpyxl."""
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        logger.debug("openpyxl not installed — skipping Excel path")
        return []

    for url in _IBBI_EXCEL_URLS:
        try:
            resp = await client.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
            if resp.status_code == 200:
                wb = openpyxl.load_workbook(BytesIO(resp.content), read_only=True, data_only=True)
                ws = wb.active
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue
                header = [str(c or "").strip().lower() for c in rows[0]]

                def col(names: list[str]) -> int:
                    for name in names:
                        for i, h in enumerate(header):
                            if name in h:
                                return i
                    return -1

                i_cin     = col(["cin", "company identification"])
                i_status  = col(["status", "process status"])
                i_date    = col(["admission date", "filing date", "date"])
                i_amount  = col(["claimed", "amount"])
                i_bench   = col(["bench", "nclt"])
                i_case    = col(["case no", "case number"])

                out: list[dict] = []
                for row in rows[1:]:
                    if not row:
                        continue
                    cin_raw = str(row[i_cin] or "") if i_cin >= 0 else ""
                    cin_m = _CIN_RE.search(cin_raw)
                    if not cin_m:
                        continue
                    cin = cin_m.group()
                    filing = _parse_date(row[i_date] if i_date >= 0 else None)
                    if not filing:
                        continue
                    out.append({
                        "case_number": str(row[i_case] or f"IBBI/{cin[:5]}/{filing.year}") if i_case >= 0 else f"IBBI/{cin[:5]}/{filing.year}",
                        "cin": cin,
                        "petition_type": "CIRP",
                        "filing_date": filing.isoformat(),
                        "status": _normalize_status(str(row[i_status] or "") if i_status >= 0 else ""),
                        "amount_claimed": _parse_amount_cr(row[i_amount] if i_amount >= 0 else 0),
                        "bench": str(row[i_bench] or "IBBI") if i_bench >= 0 else "IBBI",
                    })
                wb.close()
                if out:
                    logger.info("IBBI Excel: parsed %d CIRP records from %s", len(out), url)
                    return out
        except Exception as exc:
            logger.debug("IBBI Excel %s failed: %s", url, exc)
    return []


async def fetch_ibbi_proceedings(max_records: int = 2000) -> list[dict]:
    """Fetch live CIRP proceedings from IBBI.

    Returns a list of dicts matching the NCLTProceeding seed schema.
    Falls back to empty list on all failures (caller retains seed data).
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:
        logger.warning("httpx not installed — IBBI live fetch skipped: %s", exc)
        return []

    async with httpx.AsyncClient(verify=True, timeout=30) as client:
        # Strategy 1: JSON API
        try:
            records = await _try_json_api(client)
            parsed = _records_from_json(records)
            if parsed:
                logger.info("IBBI JSON API: fetched %d proceedings", len(parsed))
                return parsed[:max_records]
        except Exception as exc:
            logger.debug("IBBI JSON API failed (%s) — falling back to Excel", exc)

        # Strategy 2: Quarterly Excel download
        excel_records = await _try_excel(client)
        if excel_records:
            return excel_records[:max_records]

    logger.warning("IBBI: all fetch strategies failed — returning empty (seeds retained)")
    return []


def write_seed(records: list[dict], out_path: Path = SEEDS_OUT) -> int:
    """Write fetched records to the NCLT seed directory. Returns count written."""
    if not records:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("IBBI seed written: %d records → %s", len(records), out_path)
    return len(records)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    records = asyncio.run(fetch_ibbi_proceedings())
    n = write_seed(records)
    print(f"Wrote {n} IBBI proceedings.")

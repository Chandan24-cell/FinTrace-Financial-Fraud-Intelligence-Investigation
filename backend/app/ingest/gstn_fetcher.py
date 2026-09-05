"""GSTN public registration-status fetcher (no API key required).

GSTN (GST Network) exposes a public taxpayer search portal at
  https://services.gst.gov.in/services/searchtp

The JSON endpoint behind the portal works without authentication for basic
lookups — it returns registration status, business name, registration date,
cancellation date, and taxpayer type. This is the publicly documented
"Verify GSTIN" service that any taxpayer can use.

We use this to enrich Company nodes that have a known GSTIN:
  - is the GSTIN active or cancelled?
  - when was it cancelled (useful for missing-trader detection)?
  - what is the declared aggregate turnover band?

Without a GSP (GST Suvidha Provider) licence we cannot access GSTR-1/2A/3B
transaction-level data. This module only reads what the public portal shows.

Lookups are throttled to ≤ 3/second to avoid rate-limit blocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SEEDS_OUT = (
    Path(__file__).resolve().parents[3] / "infra" / "seeds" / "gst" / "gstn_live.json"
)

# GSTN public search endpoint (reverse-engineered from portal; no auth required)
_GSTN_SEARCH_URL = "https://services.gst.gov.in/services/api/search/gstin"

_GSTIN_RE = re.compile(
    r"^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SentinelG/1.0; +https://github.com)",
    "Accept": "application/json",
    "Referer": "https://services.gst.gov.in/services/searchtp",
}

_TURNOVER_BANDS: dict[str, tuple[float, float]] = {
    # GSTN API returns slab code → approximate range in crore
    "T1": (0, 0.40),
    "T2": (0.40, 1.50),
    "T3": (1.50, 5.0),
    "T4": (5.0, 25.0),
    "T5": (25.0, 100.0),
    "T6": (100.0, 500.0),
    "T7": (500.0, float("inf")),
}


def _parse_gstn_date(raw: Any) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _turnover_midpoint_cr(band_code: str | None) -> float | None:
    if not band_code:
        return None
    lo, hi = _TURNOVER_BANDS.get(str(band_code).upper(), (None, None))
    if lo is None:
        return None
    if hi == float("inf"):
        return lo * 1.5
    return (lo + hi) / 2.0


def _parse_response(gstin: str, body: dict) -> dict | None:
    """Normalise GSTN API response to our schema."""
    if body.get("sts") and str(body["sts"]).upper() == "INVALID":
        logger.debug("GSTN: %s invalid", gstin)
        return None

    taxpayer = body if "lgnm" in body else body.get("taxpayerInfo", body)

    reg_date = _parse_gstn_date(
        taxpayer.get("rgdt") or taxpayer.get("registrationDate")
    )
    cancel_date = _parse_gstn_date(
        taxpayer.get("cxdt") or taxpayer.get("cancellationDate")
    )
    status_raw = str(
        taxpayer.get("sts") or taxpayer.get("status", "")
    ).upper()
    is_cancelled = "CANCEL" in status_raw or cancel_date is not None

    taxpayer_type = str(
        taxpayer.get("dty") or taxpayer.get("taxpayerType", "Regular")
    ).lower()

    turnover_band = taxpayer.get("adhrVrfctn") or taxpayer.get("turnoverBand")
    aggregate_turnover_cr = _turnover_midpoint_cr(turnover_band)

    return {
        "gstin": gstin,
        "legal_name": str(taxpayer.get("lgnm") or taxpayer.get("legalName", "")).strip(),
        "trade_name": str(taxpayer.get("tradeNam") or taxpayer.get("tradeName", "")).strip(),
        "state_jurisdiction": str(taxpayer.get("stj") or "").strip(),
        "registration_date": reg_date.isoformat() if reg_date else None,
        "cancellation_date": cancel_date.isoformat() if cancel_date else None,
        "is_cancelled": is_cancelled,
        "taxpayer_type": taxpayer_type,
        "aggregate_turnover_cr": aggregate_turnover_cr,
    }


async def _lookup_single(client: Any, gstin: str) -> dict | None:
    """Fetch one GSTIN status. Returns None on failure."""
    if not _GSTIN_RE.match(gstin):
        logger.debug("GSTN: skipping invalid format %s", gstin)
        return None
    try:
        resp = await client.get(
            _GSTN_SEARCH_URL,
            params={"gstin": gstin},
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            body = resp.json()
            return _parse_response(gstin, body)
        logger.debug("GSTN %s → HTTP %d", gstin, resp.status_code)
    except Exception as exc:
        logger.debug("GSTN lookup %s failed: %s", gstin, exc)
    return None


async def fetch_gstn_status(
    gstins: list[str],
    *,
    requests_per_second: float = 2.5,
    max_records: int = 10000,
) -> list[dict]:
    """Fetch registration status for a list of GSTINs.

    Throttled to ≤ 3/s. Returns a list of status dicts (missing ones omitted).
    """
    try:
        import httpx
    except ImportError as exc:
        logger.warning("httpx not installed — GSTN lookup skipped: %s", exc)
        return []

    delay = 1.0 / requests_per_second
    results: list[dict] = []

    async with httpx.AsyncClient(verify=True, timeout=20) as client:
        for i, gstin in enumerate(gstins[:max_records]):
            rec = await _lookup_single(client, gstin)
            if rec:
                results.append(rec)
            if i < len(gstins) - 1:
                await asyncio.sleep(delay)

    logger.info("GSTN: fetched %d/%d records", len(results), min(len(gstins), max_records))
    return results


def write_seed(records: list[dict], out_path: Path = SEEDS_OUT) -> int:
    """Write GSTN records to seed file. Returns count written."""
    if not records:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("GSTN seed written: %d records → %s", len(records), out_path)
    return len(records)


def read_seed_gstins(seed_dir: Path | None = None) -> list[str]:
    """Collect GSTINs from existing GST seed files to refresh."""
    root = seed_dir or (
        Path(__file__).resolve().parents[3] / "infra" / "seeds" / "gst"
    )
    gstins: list[str] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("gst_entities", [])
            for item in items:
                g = item.get("gstin", "")
                if _GSTIN_RE.match(g):
                    gstins.append(g)
        except Exception:
            pass
    return list(dict.fromkeys(gstins))  # deduplicate, preserve order


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    gstins_to_check = sys.argv[1:] or read_seed_gstins()
    if not gstins_to_check:
        print("No GSTINs found. Pass GSTINs as arguments or ensure seed files exist.")
        sys.exit(0)
    records = asyncio.run(fetch_gstn_status(gstins_to_check))
    n = write_seed(records)
    print(f"Wrote {n} GSTN status records.")

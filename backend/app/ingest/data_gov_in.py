"""data.gov.in bulk MCA master-data CSV source (PRD §10 Phase C).

The Open Government Data India platform (data.gov.in) publishes annual
snapshots of every registered Indian company as CSV. License is CC-BY
style — verified-free, no paid API. Catalog page:
  https://www.data.gov.in/catalog/company-master-data

This source fills the one gap MCA21 + the MCA Public Portal scraper
cannot fill — **bulk CIN enumeration**. Neither of those exposes a "list
all India CINs" endpoint, so `CompositeCompanySource.list_available_cins()`
historically returned only the demo fixture set. With the annual snapshot
in `data/raw/data_gov_in/*.csv` the composite now enumerates the full
universe of registered Indian companies.

Per-CIN bundle: the snapshot only carries master-data columns (CIN, name,
NIC, state, registration date, ROC). Directors / charges / financials are
**not** present — those still come from MCA21 V3 (paid) or the MCA Public
Portal Playwright scraper (free, Phase A). When asked for a bundle this
source returns a company-only bundle as a best-effort minimum.

Test path: pass `csv_texts` directly to the constructor — CI never reads
from disk. Production path: leave `csv_texts=None`; the source reads
every `.csv` under `cache_dir` on first call and memoises.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from backend.app.ingest.schemas import CompanyBundle, RawCompany

logger = logging.getLogger(__name__)


_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d %b %Y",
    "%d-%b-%Y",
)


# MCA exports state as a full name; RawCompany requires a 2-letter code.
# Mapping covers every Indian state + UT. Extend as the snapshot reveals
# more variants (e.g. "Delhi" vs "NCT of Delhi").
_STATE_CODE: dict[str, str] = {
    "andhra pradesh": "AP",
    "arunachal pradesh": "AR",
    "assam": "AS",
    "bihar": "BR",
    "chhattisgarh": "CT",
    "goa": "GA",
    "gujarat": "GJ",
    "haryana": "HR",
    "himachal pradesh": "HP",
    "jharkhand": "JH",
    "karnataka": "KA",
    "kerala": "KL",
    "madhya pradesh": "MP",
    "maharashtra": "MH",
    "manipur": "MN",
    "meghalaya": "ML",
    "mizoram": "MZ",
    "nagaland": "NL",
    "odisha": "OR",
    "orissa": "OR",
    "punjab": "PB",
    "rajasthan": "RJ",
    "sikkim": "SK",
    "tamil nadu": "TN",
    "telangana": "TG",
    "tripura": "TR",
    "uttar pradesh": "UP",
    "uttarakhand": "UK",
    "west bengal": "WB",
    # UTs
    "andaman and nicobar islands": "AN",
    "chandigarh": "CH",
    "dadra and nagar haveli and daman and diu": "DN",
    "delhi": "DL",
    "nct of delhi": "DL",
    "jammu and kashmir": "JK",
    "ladakh": "LA",
    "lakshadweep": "LD",
    "puducherry": "PY",
}


# Column-header aliases. MCA exports rename columns subtly between
# snapshots ("Date of Registration" vs "DateOfRegistration"). We lowercase
# + strip + collapse non-alphanumerics, then look up here.
_COL_ALIASES: dict[str, str] = {
    "cin": "cin",
    "corporateidentificationnumber": "cin",
    "corporate_identification_number": "cin",
    "companyname": "name",
    "company_name": "name",
    "name": "name",
    "dateofregistration": "incorporation_date",
    "date_of_registration": "incorporation_date",
    "incorporationdate": "incorporation_date",
    "registereddate": "incorporation_date",
    # TN snapshot from data.gov.in publishes the column as
    # `CompanyRegistrationdate_date` (note the doubled token + underscore).
    # Normalising drops non-alphanumerics so it lands here.
    "companyregistrationdatedate": "incorporation_date",
    "industrialclass": "nic_code",
    "industrial_class": "nic_code",
    "principalbusinessactivityasperclass": "nic_code",
    "niccode": "nic_code",
    "nic_code": "nic_code",
    "registeredstate": "state",
    "registered_state": "state",
    "state": "state",
    # TN snapshot column header (`CompanyStateCode` — but the value is the
    # full state name like "tamil nadu", not a 2-letter code).
    # `_state_code()` handles both shapes.
    "companystatecode": "state",
    "registeredofficeaddress": "registered_address",
    "registered_office_address": "registered_address",
    "address": "registered_address",
}

_NORMALIZE_KEY_RE = re.compile(r"[^a-z0-9]+")


def _normalize_header(raw: str) -> str:
    return _NORMALIZE_KEY_RE.sub("", (raw or "").strip().lower())


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


def _parse_nic(raw: str) -> int | None:
    s = (raw or "").strip().lstrip("0")
    if not s:
        return None
    # NIC codes are sometimes "27101 Manufacture of motors" — keep leading int.
    m = re.match(r"^(\d{1,5})", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _state_code(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    code = _STATE_CODE.get(s.lower())
    return code


class DataGovInBulkSource:
    """data.gov.in MCA Company Master Data — bulk-universe CompanySource.

    name == 'data_gov_in_bulk'.
    """

    name = "data_gov_in_bulk"

    def __init__(
        self,
        *,
        csv_texts: list[str] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        """
        Args:
          csv_texts: In-memory CSV strings. Test path — when provided, the
            source skips the disk-cache scan entirely.
          cache_dir: Directory containing one-or-more `.csv` snapshots.
            Defaults to `./data/raw/data_gov_in`. Created lazily on first
            production refresh; absent dir is treated as 'empty universe'
            (composite falls through to fixture).
        """
        self._csv_texts = csv_texts
        self._cache_dir = cache_dir or (
            Path(__file__).resolve().parents[3] / "data" / "raw" / "data_gov_in"
        )
        self._companies: dict[str, RawCompany] | None = None

    def _iter_csv_blobs(self) -> list[str]:
        if self._csv_texts is not None:
            return self._csv_texts
        if not self._cache_dir.exists():
            return []
        return [p.read_text(encoding="utf-8") for p in sorted(self._cache_dir.glob("*.csv"))]

    def _build_index(self) -> dict[str, RawCompany]:
        index: dict[str, RawCompany] = {}
        for blob in self._iter_csv_blobs():
            reader = csv.DictReader(io.StringIO(blob))
            if not reader.fieldnames:
                continue
            header_map = {
                fn: _COL_ALIASES.get(_normalize_header(fn))
                for fn in reader.fieldnames
            }
            for raw_row in reader:
                row: dict[str, str] = {}
                for src, dest in header_map.items():
                    if dest is None:
                        continue
                    val = (raw_row.get(src) or "").strip()
                    if val:
                        row[dest] = val
                cin = row.get("cin", "")
                if not _CIN_RE.match(cin):
                    continue

                incorporation = _parse_date(row.get("incorporation_date", ""))
                nic = _parse_nic(row.get("nic_code", ""))
                state = _state_code(row.get("state", ""))
                # incorporation_date + state are essential — if either is
                # missing the row is unusable for any downstream module.
                if incorporation is None or state is None:
                    logger.warning(
                        "data.gov.in row skipped (incomplete master): cin=%s "
                        "incorporation=%r state=%r",
                        cin, row.get("incorporation_date"), row.get("state"),
                    )
                    continue
                # NIC fallback: TN snapshot writes 'NA' for every row (real
                # industry data lives in `CompanyIndustrialClassification`
                # text column). Sentinel 99999 = "Other / Unclassified" so
                # the RawCompany schema accepts the row but M5
                # peer-deviation cleanly skips it (no BSE benchmark for
                # 99999).
                if nic is None:
                    nic = 99999

                try:
                    company = RawCompany(
                        cin=cin,
                        name=row.get("name", cin),
                        incorporation_date=incorporation,
                        nic_code=nic,
                        state=state,
                        registered_address=row.get("registered_address") or None,
                    )
                except ValidationError as exc:
                    logger.warning("data.gov.in row failed validation %s: %s", cin, exc)
                    continue

                index[cin] = company
        return index

    def _ensure_index(self) -> dict[str, RawCompany]:
        if self._companies is None:
            self._companies = self._build_index()
        return self._companies

    async def fetch_bundle(self, cin: str) -> CompanyBundle | None:
        idx = self._ensure_index()
        company = idx.get(cin)
        if company is None:
            return None
        # Master-data only — directors/financials/charges come from MCA21
        # or the MCA public scraper. Composite folds those on top.
        return CompanyBundle(company=company)

    async def list_available_cins(self) -> list[str]:
        return sorted(self._ensure_index().keys())

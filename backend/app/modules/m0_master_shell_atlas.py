"""M0 — Master-data Shell Atlas.

Pre-computes shell-like company clusters directly from the data.gov.in
MCA master-data CSV, without needing financials / directors / charges.
Three orthogonal signals, all derivable from columns present in the
bulk snapshot:

  ADDRESS_CLUSTER         — N companies sharing one registered office.
                            Real businesses rarely co-locate at scale.
  MASS_INCORPORATION      — N companies registered the same day in the
                            same state. Coordinated shell registration.
  PAPER_SHELL             — Paid-up capital < 10% of authorised AND
                            incorporation older than 2 years. Suggests
                            a parked shell.

Why this exists: the TN seed loads ONLY :Company nodes into Neo4j with
zero relationships, so M1-M11 all skip for any random TN CIN and the
dossier is empty. M0 reads the CSV directly, finds shell-like patterns
in memory, and exposes:

  * `/shells` endpoint     — judges browse the ranked clusters
  * cin_to_clusters index  — the scorer adds a `MASTER_DATA_SHELL_CLUSTER`
                             FraudSignal when an analysed CIN is in any
                             flagged cluster (so /analyse on a TN CIN
                             actually returns evidence)

Memory budget on Lightsail's 4 GB box: a single pass over the 250k-row
CSV with three Counter / defaultdict aggregations runs in ~200 MB peak.
Atlas built once at startup; subsequent requests hit dict lookups.

Honesty note: the CSV does not always carry AuthorizedCapital /
PaidupCapital — some snapshots redact them. When both columns are
missing the PAPER_SHELL signal is skipped (no false positives from
zero-division).
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ShellSignalType = Literal["ADDRESS_CLUSTER", "MASS_INCORPORATION", "PAPER_SHELL"]


# ---------------------------------------------------------------------------
# CSV column aliases — kept tight, parallel to data_gov_in.py's set so the
# normaliser handles the same snapshot variants. We need a slightly different
# field projection than the RawCompany extractor (capital fields, raw address
# string) so this module owns its own column map.
# ---------------------------------------------------------------------------

_NORM_KEY_RE = re.compile(r"[^a-z0-9]+")
_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_WHITESPACE_RE = re.compile(r"\s+")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%Y")

_ATLAS_COL_ALIASES: dict[str, str] = {
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
    "companyregistrationdatedate": "incorporation_date",
    "registeredstate": "state",
    "registered_state": "state",
    "state": "state",
    "companystatecode": "state",
    "registeredofficeaddress": "address",
    "registered_office_address": "address",
    "address": "address",
    "authorizedcapital": "auth_capital",
    "authorisedcapital": "auth_capital",
    "authorized_capital": "auth_capital",
    "authorised_capital": "auth_capital",
    "paidupcapital": "paid_capital",
    "paid_up_capital": "paid_capital",
    "paidup_capital": "paid_capital",
}

# Cluster size thresholds. Tuned for TN bulk (191k+ rows): too low →
# everything looks suspicious; too high → only a handful of clusters fire.
_MIN_ADDRESS_CLUSTER = 5
_MIN_MASS_INCORP = 8
_PAPER_SHELL_PAID_RATIO = 0.10  # paid_up < 10% of authorised
_PAPER_SHELL_MIN_AGE_DAYS = 730  # > 2 years old
_TOP_PER_SIGNAL = 100  # what /shells returns by default per signal type


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyRow:
    """Lightweight projection of one CSV row — only the fields the atlas
    needs. Constructed in a hot loop so we keep it minimal."""

    cin: str
    name: str
    state: str | None
    address_norm: str | None
    incorporation_date: date | None
    auth_capital: float | None
    paid_capital: float | None


@dataclass
class ShellCluster:
    """One ranked cluster surfaced by M0."""

    cluster_id: str            # e.g. "ADDRESS-TN-0001"
    signal_type: ShellSignalType
    size: int
    state: str | None
    anchor_value: str           # the shared address, the (state, date), or "paid<10%"
    sample_cins: list[str] = field(default_factory=list)  # first 10 members
    sample_names: list[str] = field(default_factory=list)
    evidence_string: str = ""
    # Verifiable provenance — points at the CSV row this came from.
    source: str = "data.gov.in MCA Master Data (CC-BY) — M0 cluster pipeline"


@dataclass
class ShellAtlas:
    """In-memory state for the master-data shell atlas."""

    clusters: dict[str, ShellCluster] = field(default_factory=dict)
    cin_to_clusters: dict[str, list[str]] = field(default_factory=dict)
    by_signal: dict[ShellSignalType, list[str]] = field(
        default_factory=lambda: {
            "ADDRESS_CLUSTER": [],
            "MASS_INCORPORATION": [],
            "PAPER_SHELL": [],
        }
    )
    # Audit fields surfaced via /shells inventory header.
    rows_scanned: int = 0
    csv_path: str | None = None
    built_at: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_header(raw: str) -> str:
    return _NORM_KEY_RE.sub("", (raw or "").strip().lower())


def _norm_address(raw: str) -> str | None:
    """Address fingerprint — lowercase, collapse whitespace, strip
    leading/trailing punctuation. Coarse but effective: 'A-12, MG ROAD'
    and 'a 12 mg road' collide on the same key."""
    if not raw:
        return None
    s = re.sub(r"[^a-z0-9\s,]+", "", raw.lower())
    s = _WHITESPACE_RE.sub(" ", s).strip()
    if len(s) < 8:
        return None
    return s


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


def _parse_capital(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", "")
    if not s or s.upper() in {"NA", "NULL", "-"}:
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


# Truncate normalised address to a readable banner length.
def _trim(s: str, n: int = 80) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# CSV iteration
# ---------------------------------------------------------------------------


def _iter_rows(csv_paths: list[Path]) -> list[CompanyRow]:
    """Single pass over every TN-bulk-shaped CSV under csv_paths. Returns
    the lightweight CompanyRow projection. Missing-but-essential rows
    (no CIN match) are silently skipped — they get counted against
    rows_scanned but produce no atlas entry."""
    out: list[CompanyRow] = []
    for path in csv_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("M0: could not read %s (%s)", path, exc)
            continue
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            continue
        header_map = {
            fn: _ATLAS_COL_ALIASES.get(_norm_header(fn)) for fn in reader.fieldnames
        }
        for raw_row in reader:
            mapped: dict[str, str] = {}
            for src, dest in header_map.items():
                if dest is None:
                    continue
                val = (raw_row.get(src) or "").strip()
                if val:
                    mapped[dest] = val
            cin = mapped.get("cin", "")
            if not _CIN_RE.match(cin):
                continue
            out.append(
                CompanyRow(
                    cin=cin,
                    name=mapped.get("name", cin),
                    state=(mapped.get("state") or None),
                    address_norm=_norm_address(mapped.get("address", "")),
                    incorporation_date=_parse_date(mapped.get("incorporation_date", "")),
                    auth_capital=_parse_capital(mapped.get("auth_capital", "")),
                    paid_capital=_parse_capital(mapped.get("paid_capital", "")),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Cluster computation
# ---------------------------------------------------------------------------


def _compute_address_clusters(
    rows: list[CompanyRow],
) -> list[tuple[ShellCluster, list[str]]]:
    """Return (display cluster, full member CIN list) tuples. The display
    cluster carries only the top-10 sample for UI; the full member list
    feeds the scorer index so every member triggers a hit."""
    by_addr: defaultdict[str, list[CompanyRow]] = defaultdict(list)
    for r in rows:
        if r.address_norm is None:
            continue
        by_addr[r.address_norm].append(r)
    out: list[tuple[ShellCluster, list[str]]] = []
    idx = 0
    for addr, members in sorted(by_addr.items(), key=lambda kv: -len(kv[1])):
        if len(members) < _MIN_ADDRESS_CLUSTER:
            break
        idx += 1
        state = members[0].state
        cid = f"ADDR-{(state or 'XX').upper()[:2]}-{idx:04d}"
        cluster = ShellCluster(
            cluster_id=cid,
            signal_type="ADDRESS_CLUSTER",
            size=len(members),
            state=state,
            anchor_value=_trim(addr, 120),
            sample_cins=[m.cin for m in members[:10]],
            sample_names=[m.name for m in members[:10]],
            evidence_string=(
                f"{len(members)} companies share the same registered "
                f"office address ('{_trim(addr, 60)}'). Real businesses "
                f"rarely co-locate at this scale — a known shell-firm tell."
            ),
        )
        out.append((cluster, [m.cin for m in members]))
        if len(out) >= _TOP_PER_SIGNAL:
            break
    return out


def _compute_mass_incorporation(
    rows: list[CompanyRow],
) -> list[tuple[ShellCluster, list[str]]]:
    by_batch: defaultdict[tuple[str, date], list[CompanyRow]] = defaultdict(list)
    for r in rows:
        if r.state is None or r.incorporation_date is None:
            continue
        by_batch[(r.state, r.incorporation_date)].append(r)
    out: list[tuple[ShellCluster, list[str]]] = []
    idx = 0
    for (state, day), members in sorted(by_batch.items(), key=lambda kv: -len(kv[1])):
        if len(members) < _MIN_MASS_INCORP:
            break
        idx += 1
        cid = f"MASS-{state[:8].upper()}-{day.isoformat()}-{idx:04d}"
        cluster = ShellCluster(
            cluster_id=cid,
            signal_type="MASS_INCORPORATION",
            size=len(members),
            state=state,
            anchor_value=f"{state} · {day.isoformat()}",
            sample_cins=[m.cin for m in members[:10]],
            sample_names=[m.name for m in members[:10]],
            evidence_string=(
                f"{len(members)} companies registered in {state} on the "
                f"same day ({day.isoformat()}). Coordinated batch "
                f"registrations are a documented shell-network pattern "
                f"(DGGI / SFIO enforcement actions, 2020-2024)."
            ),
        )
        out.append((cluster, [m.cin for m in members]))
        if len(out) >= _TOP_PER_SIGNAL:
            break
    return out


def _compute_paper_shells(
    rows: list[CompanyRow], today: date,
) -> list[tuple[ShellCluster, list[str]]]:
    # "Paper shell" is a per-CIN finding, not a multi-member cluster — but
    # we expose it through the same ShellCluster shape so the frontend can
    # render it in the same list. One ShellCluster per qualifying CIN with
    # size=1; useful when /analyse is called on a TN CIN that matches.
    out: list[tuple[ShellCluster, list[str]]] = []
    for r in rows:
        if r.auth_capital is None or r.paid_capital is None or r.incorporation_date is None:
            continue
        if r.auth_capital <= 0:
            continue
        if r.paid_capital >= r.auth_capital * _PAPER_SHELL_PAID_RATIO:
            continue
        age = (today - r.incorporation_date).days
        if age < _PAPER_SHELL_MIN_AGE_DAYS:
            continue
        ratio = (r.paid_capital / r.auth_capital) * 100.0
        cluster = ShellCluster(
            cluster_id=f"PAPER-{r.cin}",
            signal_type="PAPER_SHELL",
            size=1,
            state=r.state,
            anchor_value=f"paid {ratio:.1f}% of authorised, age {age // 365} years",
            sample_cins=[r.cin],
            sample_names=[r.name],
            evidence_string=(
                f"Paid-up capital is only {ratio:.1f}% of authorised "
                f"(₹{r.paid_capital:,.0f} of ₹{r.auth_capital:,.0f}). "
                f"Company is {age // 365} years old. Pattern: parked "
                f"shell — authorised on paper, never operationally funded."
            ),
        )
        out.append((cluster, [r.cin]))
        if len(out) >= _TOP_PER_SIGNAL * 5:
            # Cap paper shells separately — they're per-CIN so the universe
            # is larger; 500 deep cap keeps the index small.
            break
    return out


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_atlas(csv_dir: Path | None = None, *, today: date | None = None) -> ShellAtlas:
    """Scan the data.gov.in cache dir, compute the three cluster signals,
    return a populated ShellAtlas.

    Defensive: missing dir → empty atlas (no exception). Missing capital
    columns → only PAPER_SHELL skipped, the other two still fire.
    """
    csv_dir = csv_dir or (
        Path(__file__).resolve().parents[3] / "data" / "raw" / "data_gov_in"
    )
    today = today or date.today()
    atlas = ShellAtlas(csv_path=str(csv_dir))
    if not csv_dir.exists():
        logger.info("M0 atlas: no CSV dir at %s — empty atlas", csv_dir)
        return atlas

    csv_paths = sorted(csv_dir.glob("*.csv"))
    if not csv_paths:
        logger.info("M0 atlas: no CSVs under %s", csv_dir)
        return atlas

    rows = _iter_rows(csv_paths)
    atlas.rows_scanned = len(rows)
    logger.info("M0 atlas: scanned %d rows from %d CSV(s)", len(rows), len(csv_paths))

    address_clusters = _compute_address_clusters(rows)
    mass_clusters = _compute_mass_incorporation(rows)
    paper_clusters = _compute_paper_shells(rows, today)

    for cluster, full_members in address_clusters + mass_clusters + paper_clusters:
        atlas.clusters[cluster.cluster_id] = cluster
        atlas.by_signal[cluster.signal_type].append(cluster.cluster_id)
        # Index *every* member, not just the display sample — otherwise
        # /analyse on a random TN CIN that's the 17th company at a shared
        # address would miss the flag.
        for cin in full_members:
            atlas.cin_to_clusters.setdefault(cin, []).append(cluster.cluster_id)

    atlas.built_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    logger.info(
        "M0 atlas built: %d address / %d mass-incorp / %d paper clusters, "
        "%d unique CINs flagged",
        len(address_clusters), len(mass_clusters), len(paper_clusters),
        len(atlas.cin_to_clusters),
    )
    return atlas


# ---------------------------------------------------------------------------
# Module-level singleton — loaded once at startup
# ---------------------------------------------------------------------------


_atlas: ShellAtlas | None = None


def get_atlas() -> ShellAtlas:
    """Lazy accessor. Build on first call so dev / CI environments without
    the bulk CSV don't pay the startup cost."""
    global _atlas
    if _atlas is None:
        _atlas = build_atlas()
    return _atlas


def reset_for_tests() -> None:
    global _atlas
    _atlas = None


def status() -> dict:
    """Snapshot for /health/ml or /sources surfacing."""
    a = _atlas
    if a is None:
        return {"built": False}
    return {
        "built": True,
        "rows_scanned": a.rows_scanned,
        "csv_path": a.csv_path,
        "built_at": a.built_at,
        "address_clusters": len(a.by_signal["ADDRESS_CLUSTER"]),
        "mass_incorporation_clusters": len(a.by_signal["MASS_INCORPORATION"]),
        "paper_shells": len(a.by_signal["PAPER_SHELL"]),
        "flagged_cins": len(a.cin_to_clusters),
    }

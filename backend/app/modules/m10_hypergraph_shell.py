"""Module 10 — Hypergraph Shell Network Detection (PRD §4.10).

A shell network hides behind one shared attribute being referenced by many
otherwise-unrelated companies. Pairwise graph edges miss this because the
shared attribute (an address, a CA firm DIN, a bank branch) is the *invisible*
hub — not an edge endpoint.

We model each shared attribute as a `SharedAttribute` intermediate node and
flag any cluster whose membership exceeds the PRD §4.10 thresholds:

  | Attribute               | Threshold      | Severity / +score |
  |-------------------------|----------------|-------------------|
  | registered_address      | > 5 companies   | CRITICAL / +40    |
  | auditor_din (district)  | > 50 companies  | HIGH / +25        |
  | bank_branch_ifsc        | > 10 related    | HIGH / +25        |
  | contact_phone           | > 1 company     | HIGH / +25        |

Every CIN in a flagged cluster receives one FraudSignal carrying the cluster
fingerprint, the attribute value (truncated for addresses), and the sibling
CIN list. PRD §4.2 specific-numbers rule applies: we always cite the cluster
size + at least 5 sibling CINs.

Day-12 acceptance (PRD §10) — landed as part of Tier-1 module suite. The full
SCC-driven ring detection lives in Module 4 graph patterns.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from backend.app.ingest.schemas import RawCharge, RawCompany
from backend.app.modules.base import (
    FraudSignal,
    ModuleResult,
    Severity,
    clamp_score,
)

MODULE_NAME = "m10_hypergraph_shell"

# PRD §4.10 thresholds — verbatim.
ADDRESS_CLUSTER_THRESHOLD = 5
AUDITOR_DISTRICT_THRESHOLD = 50
IFSC_RELATED_THRESHOLD = 10
PHONE_DUPLICATE_THRESHOLD = 1


@dataclass(frozen=True)
class CompanyAttribute:
    """One (cin, attribute_name, attribute_value) row — the hypergraph edge."""

    cin: str
    name: str
    value: str
    district: str | None = None  # state code if we're using state as district proxy


@dataclass
class HypergraphInputs:
    """All companies + their charges. Module groups internally."""

    companies: list[RawCompany]
    charges: list[RawCharge]


# --- Attribute extractors ---------------------------------------------------
def _extract_addresses(companies: Iterable[RawCompany]) -> list[CompanyAttribute]:
    out: list[CompanyAttribute] = []
    for c in companies:
        if c.registered_address:
            out.append(CompanyAttribute(
                cin=c.cin, name=c.name, value=c.registered_address.strip(),
                district=c.state,
            ))
    return out


def _extract_auditors(companies: Iterable[RawCompany]) -> list[CompanyAttribute]:
    out: list[CompanyAttribute] = []
    for c in companies:
        if c.auditor_din:
            out.append(CompanyAttribute(
                cin=c.cin, name=c.name, value=c.auditor_din,
                district=c.state,  # state used as district proxy in fixture mode
            ))
    return out


def _extract_phones(companies: Iterable[RawCompany]) -> list[CompanyAttribute]:
    out: list[CompanyAttribute] = []
    for c in companies:
        if c.contact_phone:
            out.append(CompanyAttribute(
                cin=c.cin, name=c.name, value=c.contact_phone.strip(),
                district=c.state,
            ))
    return out


def _extract_ifsc(
    companies: Iterable[RawCompany], charges: Iterable[RawCharge],
) -> list[CompanyAttribute]:
    cin_to_name = {c.cin: c.name for c in companies}
    cin_to_state = {c.cin: c.state for c in companies}
    out: list[CompanyAttribute] = []
    for ch in charges:
        if ch.bank_branch_ifsc:
            out.append(CompanyAttribute(
                cin=ch.cin,
                name=cin_to_name.get(ch.cin, ch.cin),
                value=ch.bank_branch_ifsc,
                district=cin_to_state.get(ch.cin),
            ))
    return out


# --- Cluster grouping --------------------------------------------------------
def _group_by_value(rows: list[CompanyAttribute]) -> dict[str, list[CompanyAttribute]]:
    clusters: dict[str, list[CompanyAttribute]] = defaultdict(list)
    for row in rows:
        clusters[row.value].append(row)
    return clusters


def _group_by_value_within_district(
    rows: list[CompanyAttribute],
) -> dict[tuple[str, str | None], list[CompanyAttribute]]:
    clusters: dict[tuple[str, str | None], list[CompanyAttribute]] = defaultdict(list)
    for row in rows:
        clusters[(row.value, row.district)].append(row)
    return clusters


# --- Signal builders ---------------------------------------------------------
def _truncate(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _format_siblings(members: list[CompanyAttribute], anchor_cin: str) -> str:
    others = [m.cin for m in members if m.cin != anchor_cin]
    shown = others[:5]
    suffix = f" (+{len(others) - len(shown)} more)" if len(others) > len(shown) else ""
    return ", ".join(shown) + suffix if shown else "(no siblings)"


def _address_signal(anchor: CompanyAttribute, cluster: list[CompanyAttribute]) -> FraudSignal:
    return FraudSignal(
        signal_type="HYPERGRAPH_SHARED_ADDRESS",
        severity=Severity.CRITICAL,
        score_contribution=40.0,
        evidence_string=(
            f"Registered address '{_truncate(anchor.value)}' is shared by "
            f"{len(cluster)} companies — above the {ADDRESS_CLUSTER_THRESHOLD}-company "
            f"PRD §4.10 floor. Sibling CINs: {_format_siblings(cluster, anchor.cin)}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "SharedAttribute", "kind": "registered_address", "value": anchor.value},
            *[{"label": "Company", "cin": m.cin} for m in cluster[:6]],
        ],
    )


def _auditor_signal(anchor: CompanyAttribute, cluster: list[CompanyAttribute]) -> FraudSignal:
    return FraudSignal(
        signal_type="HYPERGRAPH_SHARED_AUDITOR_DIN",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Auditor DIN {anchor.value} signs filings for {len(cluster)} "
            f"companies in district '{anchor.district or 'unknown'}' — above the "
            f"{AUDITOR_DISTRICT_THRESHOLD}-company PRD §4.10 floor. "
            f"Sibling CINs: {_format_siblings(cluster, anchor.cin)}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "SharedAttribute", "kind": "auditor_din", "value": anchor.value},
            *[{"label": "Company", "cin": m.cin} for m in cluster[:6]],
        ],
    )


def _phone_signal(anchor: CompanyAttribute, cluster: list[CompanyAttribute]) -> FraudSignal:
    return FraudSignal(
        signal_type="HYPERGRAPH_SHARED_PHONE",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Contact phone '{anchor.value}' is shared by {len(cluster)} "
            f"unrelated companies. Sibling CINs: {_format_siblings(cluster, anchor.cin)}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "SharedAttribute", "kind": "contact_phone", "value": anchor.value},
            *[{"label": "Company", "cin": m.cin} for m in cluster[:6]],
        ],
    )


def _ifsc_signal(anchor: CompanyAttribute, cluster: list[CompanyAttribute]) -> FraudSignal:
    return FraudSignal(
        signal_type="HYPERGRAPH_SHARED_IFSC",
        severity=Severity.HIGH,
        score_contribution=25.0,
        evidence_string=(
            f"Bank branch IFSC '{anchor.value}' anchors charges for "
            f"{len(cluster)} companies — above the {IFSC_RELATED_THRESHOLD}-company "
            f"PRD §4.10 floor. Sibling CINs: {_format_siblings(cluster, anchor.cin)}."
        ),
        module_name=MODULE_NAME,
        triggered_by=[
            {"label": "SharedAttribute", "kind": "bank_branch_ifsc", "value": anchor.value},
            *[{"label": "Company", "cin": m.cin} for m in cluster[:6]],
        ],
    )


def _build_cluster_signals(inputs: HypergraphInputs) -> dict[str, list[FraudSignal]]:
    """Compute every signal for every flagged CIN. Returns {cin -> signals}."""
    out: dict[str, list[FraudSignal]] = defaultdict(list)

    # 1. Shared registered_address
    for value, cluster in _group_by_value(_extract_addresses(inputs.companies)).items():
        if len(cluster) > ADDRESS_CLUSTER_THRESHOLD:
            for member in cluster:
                out[member.cin].append(_address_signal(member, cluster))

    # 2. Shared auditor_din within the same district/state
    auditor_clusters = _group_by_value_within_district(_extract_auditors(inputs.companies))
    for (_value, _district), cluster in auditor_clusters.items():
        if len(cluster) > AUDITOR_DISTRICT_THRESHOLD:
            for member in cluster:
                out[member.cin].append(_auditor_signal(member, cluster))

    # 3. Shared contact_phone
    for value, cluster in _group_by_value(_extract_phones(inputs.companies)).items():
        if len(cluster) > PHONE_DUPLICATE_THRESHOLD:
            for member in cluster:
                out[member.cin].append(_phone_signal(member, cluster))

    # 4. Shared bank_branch_ifsc on charges
    for value, cluster in _group_by_value(_extract_ifsc(inputs.companies, inputs.charges)).items():
        # Cluster size is unique CINs, not raw charge rows.
        unique_cins = {m.cin: m for m in cluster}
        if len(unique_cins) > IFSC_RELATED_THRESHOLD:
            for member in unique_cins.values():
                out[member.cin].append(_ifsc_signal(member, list(unique_cins.values())))

    return out


def run(inputs: HypergraphInputs) -> dict[str, ModuleResult]:
    """Batch-mode: returns {cin -> ModuleResult} only for CINs that fired."""
    cluster_signals = _build_cluster_signals(inputs)
    name_by_cin = {c.cin: c.name for c in inputs.companies}

    results: dict[str, ModuleResult] = {}
    for cin, signals in cluster_signals.items():
        if not signals:
            continue
        results[cin] = ModuleResult(
            module_name=MODULE_NAME,
            cin=cin,
            year=None,
            score=clamp_score(sum(s.score_contribution for s in signals)),
            signals=signals,
        )
    # Annotate (no-op now — name_by_cin is here for future logging hooks).
    _ = name_by_cin
    return results

"""Tests for Module 10 — Hypergraph Shell Network Detection (PRD §4.10 + §10 Day 12)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.ingest.schemas import RawCharge, RawCompany
from backend.app.modules import m10_hypergraph_shell as m10
from backend.app.modules.base import Severity
from backend.app.modules.m10_hypergraph_shell import HypergraphInputs


def _company(serial: int, *, address: str | None = None, auditor_din: str | None = None,
             phone: str | None = None, state: str = "MH") -> RawCompany:
    return RawCompany(
        cin=f"U27101{state}2020PTC{900000 + serial:06d}",
        name=f"Shell Co {serial}",
        incorporation_date=date(2020, 1, 1),
        nic_code=27101,
        state=state,
        registered_address=address,
        auditor_din=auditor_din,
        contact_phone=phone,
    )


def _charge(serial: int, ifsc: str) -> RawCharge:
    return RawCharge(
        cin=f"U27101MH2020PTC{900000 + serial:06d}",
        charge_id=f"SHL-CHG-{serial:04d}",
        lender_name="Test Bank",
        bank_branch_ifsc=ifsc,
        amount=10_000_000.0,
        creation_date=date(2021, 1, 1),
        charge_type="hypothecation",
    )


def test_shared_address_above_threshold_fires_critical() -> None:
    cluster_addr = "401, Acme Tower, Andheri East, Mumbai"
    companies = [_company(i, address=cluster_addr) for i in range(6)]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    assert len(results) == 6
    for cin, res in results.items():
        sig = next(s for s in res.signals if s.signal_type == "HYPERGRAPH_SHARED_ADDRESS")
        assert sig.severity is Severity.CRITICAL
        assert "6 companies" in sig.evidence_string


def test_shared_address_at_threshold_does_not_fire() -> None:
    cluster_addr = "401, Acme Tower"
    companies = [_company(i, address=cluster_addr) for i in range(m10.ADDRESS_CLUSTER_THRESHOLD)]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    assert results == {}


def test_shared_phone_fires_high() -> None:
    companies = [
        _company(1, phone="+91-22-1234-5678"),
        _company(2, phone="+91-22-1234-5678"),
    ]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    assert len(results) == 2
    for cin, res in results.items():
        sig = next(s for s in res.signals if s.signal_type == "HYPERGRAPH_SHARED_PHONE")
        assert sig.severity is Severity.HIGH


def test_unique_phones_do_not_fire() -> None:
    companies = [_company(i, phone=f"+91-22-1234-{i:04d}") for i in range(3)]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    assert results == {}


def test_shared_auditor_in_same_district_fires() -> None:
    # 51 companies in MH share auditor DIN 00112233 -> exceeds 50-threshold
    companies = [_company(i, auditor_din="00112233", state="MH")
                 for i in range(m10.AUDITOR_DISTRICT_THRESHOLD + 1)]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    assert results
    any_res = next(iter(results.values()))
    sig = next(s for s in any_res.signals if s.signal_type == "HYPERGRAPH_SHARED_AUDITOR_DIN")
    assert sig.severity is Severity.HIGH


def test_shared_auditor_split_across_districts_does_not_aggregate() -> None:
    # 30 MH + 30 KA — neither crosses the 50-threshold within the same district.
    companies = (
        [_company(i, auditor_din="00112233", state="MH") for i in range(30)]
        + [_company(100 + i, auditor_din="00112233", state="KA") for i in range(30)]
    )
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    auditor_sigs = [
        s for r in results.values() for s in r.signals
        if s.signal_type == "HYPERGRAPH_SHARED_AUDITOR_DIN"
    ]
    assert auditor_sigs == []


def test_shared_ifsc_above_threshold_fires_high() -> None:
    companies = [_company(i) for i in range(11)]
    charges = [_charge(i, ifsc="HDFC0000412") for i in range(11)]
    results = m10.run(HypergraphInputs(companies=companies, charges=charges))
    assert len(results) == 11
    any_res = next(iter(results.values()))
    sig = next(s for s in any_res.signals if s.signal_type == "HYPERGRAPH_SHARED_IFSC")
    assert sig.severity is Severity.HIGH


def test_evidence_string_cites_siblings_and_count() -> None:
    addr = "shared-address"
    companies = [_company(i, address=addr) for i in range(7)]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    res = next(iter(results.values()))
    sig = next(s for s in res.signals if s.signal_type == "HYPERGRAPH_SHARED_ADDRESS")
    assert "7 companies" in sig.evidence_string
    assert "Sibling CINs:" in sig.evidence_string


def test_thresholds_match_prd() -> None:
    assert m10.ADDRESS_CLUSTER_THRESHOLD == 5
    assert m10.AUDITOR_DISTRICT_THRESHOLD == 50
    assert m10.IFSC_RELATED_THRESHOLD == 10
    assert m10.PHONE_DUPLICATE_THRESHOLD == 1


def test_clean_companies_produce_no_results() -> None:
    companies = [_company(i, address=f"unique addr {i}", phone=f"+91-{i:09d}") for i in range(8)]
    results = m10.run(HypergraphInputs(companies=companies, charges=[]))
    assert results == {}

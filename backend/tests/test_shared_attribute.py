"""Unit tests for SharedAttribute materialisation logic (Day 4)."""

from backend.app.graph.shared_attribute import (
    _COMPANY_PROPERTY_RULES,
    _MATERIALISE_FROM_CHARGES,
    _MATERIALISE_FROM_COMPANY,
)


def test_rule_list_covers_address_and_auditor() -> None:
    types = {entry[0] for entry in _COMPANY_PROPERTY_RULES}
    assert "registered_address" in types
    assert "auditor_din" in types


def test_company_property_rules_map_to_real_company_props() -> None:
    valid_company_props = {
        "registered_address", "auditor_din", "contact_phone", "gstin",
    }
    for _, prop in _COMPANY_PROPERTY_RULES:
        assert prop in valid_company_props, f"Unexpected company property {prop!r}"


def test_company_cypher_uses_correct_labels_and_relationships() -> None:
    cypher = _MATERIALISE_FROM_COMPANY
    assert "SharedAttribute" in cypher
    assert "SHARES_ATTRIBUTE" in cypher
    assert "MATCH (c:Company)" in cypher
    assert "$attribute_type" in cypher
    assert "$min_count" in cypher


def test_charges_cypher_uses_loan_disbursement() -> None:
    cypher = _MATERIALISE_FROM_CHARGES
    assert "LoanDisbursement" in cypher
    assert "bank_branch_ifsc" in cypher
    assert "RECEIVED_LOAN" in cypher


def test_company_cypher_writes_company_count_property() -> None:
    assert "a.company_count = size(companies)" in _MATERIALISE_FROM_COMPANY


def test_charges_cypher_writes_company_count_property() -> None:
    assert "a.company_count = size(companies)" in _MATERIALISE_FROM_CHARGES

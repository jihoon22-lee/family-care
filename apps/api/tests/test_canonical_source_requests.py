"""Only digest-bound certificate evidence drives enrollment inventory reads."""

from typing import Any

from familycare_api.insurance_reconciliation.canonical_repository import (
    _certificate_pages,
    _digest,
)


def test_certificate_inventory_ignores_other_source_roles_and_invalid_pages() -> None:
    source: dict[str, Any] = {
        "certificate_review": {
            "evidence_locations": [
                {"document_alias": "Synthetic Policy", "physical_page": 1},
                {"document_alias": "Synthetic Policy", "physical_page": 3},
                {"document_alias": "Synthetic Policy", "physical_page": 1},
                {"document_alias": "Synthetic Policy", "physical_page": True},
                {"document_alias": "Synthetic Policy", "physical_page": 501},
                {"document_alias": "Synthetic Policy", "physical_page": "2"},
                None,
            ]
        },
        "terms_review": {
            "evidence_locations": [{"document_alias": "Synthetic Terms", "physical_page": 200}]
        },
    }
    coverage = {"source_record_json": source, "source_record_digest_sha256": _digest(source)}
    assert _certificate_pages([coverage]) == {"Synthetic Policy": {1, 3}}
    coverage["source_record_digest_sha256"] = "0" * 64
    assert _certificate_pages([coverage]) == {}


def test_missing_certificate_locations_do_not_trigger_full_household_inventory() -> None:
    for source in ({}, {"certificate_review": None}, {"certificate_review": {}}):
        assert (
            _certificate_pages(
                [{"source_record_json": source, "source_record_digest_sha256": _digest(source)}]
            )
            == {}
        )

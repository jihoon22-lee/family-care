"""Refinement preserves earlier source facts instead of choosing convenient values."""

from copy import deepcopy
from typing import Any

import pytest
from familycare_api.insurance_documents.metadata_refinement import metadata_refines


def _proof(**fields: str) -> dict[str, Any]:
    return {
        "role": "terms",
        "page_start": 1,
        "page_end": 1,
        "facts": [{"field": name, "value": value} for name, value in fields.items()],
        "conflicting_fields": [],
        "unresolved_fields": [],
    }


def test_additional_source_metadata_is_a_strict_refinement() -> None:
    before = _proof(insurer="Sample Assurance", product_code="SAMPLE-A")
    after = _proof(
        insurer="Sample Assurance", product_code="SAMPLE-A", product_name="Sample Policy"
    )
    assert metadata_refines(before, after)
    assert not metadata_refines(before, deepcopy(before))


@pytest.mark.parametrize("change", ["missing", "different", "conflicting", "unresolved"])
def test_more_fields_cannot_hide_loss_of_an_earlier_resolved_value(change: str) -> None:
    before = _proof(insurer="Sample Assurance", product_code="SAMPLE-A")
    after = _proof(
        insurer="Sample Assurance", product_code="SAMPLE-A", product_name="Sample Policy"
    )
    if change == "missing":
        after["facts"] = [fact for fact in after["facts"] if fact["field"] != "product_code"]
    elif change == "different":
        after["facts"][1]["value"] = "SAMPLE-B"
    else:
        after[change + "_fields"] = ["product_code"]
    assert not metadata_refines(before, after)


def test_new_uncertainty_does_not_block_unrelated_verified_enrichment() -> None:
    before = _proof(insurer="Sample Assurance")
    after = _proof(insurer="Sample Assurance", product_code="SAMPLE-A")
    after["unresolved_fields"] = ["edition_date"]
    assert metadata_refines(before, after)


def test_resolving_a_previously_unresolved_value_is_an_improvement() -> None:
    before = _proof(insurer="Sample Assurance", product_code="SAMPLE-A")
    before["unresolved_fields"] = ["product_code"]
    after = _proof(insurer="Sample Assurance", product_code="SAMPLE-A")
    assert metadata_refines(before, after)


@pytest.mark.parametrize(
    "role", ["terms", "product_explanation", "policy", "application", "amendment"]
)
def test_range_extension_cannot_merge_contract_documents_by_product_identity(role: str) -> None:
    before = _proof(product_code="SAMPLE-A")
    before["role"] = role
    after = deepcopy(before)
    after["page_end"] = 3
    assert metadata_refines(before, after) == (role in {"terms", "product_explanation"})


@pytest.mark.parametrize("change", ["role", "start", "end"])
def test_refinement_never_removes_the_original_role_or_page_scope(change: str) -> None:
    before = _proof(product_code="SAMPLE-A")
    before["page_end"] = 3
    after = _proof(product_code="SAMPLE-A", product_name="Sample Policy")
    after["page_end"] = 3
    if change == "role":
        after["role"] = "product_explanation"
    elif change == "start":
        after["page_start"] = 2
    else:
        after["page_end"] = 2
    assert not metadata_refines(before, after)

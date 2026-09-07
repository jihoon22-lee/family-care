"""Pure, synthetic evidence combinations for policy-to-edition applicability."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from familycare_api.clauses.terms_applicability import assess_terms_applicability

_EXPLICIT = frozenset({"terms_reference", "edition_reference"})


def _proof(role: str, **facts: str) -> dict[str, Any]:
    return {
        "role": role,
        "facts": [{"field": field, "value": value} for field, value in facts.items()],
        "conflicting_fields": [],
        "unresolved_fields": [],
    }


def _references() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _proof(
            "policy",
            insurer="Sample Assurance",
            terms_reference="SAMPLE-TERMS-A",
            edition_reference="SAMPLE-EDITION-A",
        ),
        _proof(
            "terms",
            insurer="Sample Assurance",
            terms_code="SAMPLE-TERMS-A",
            edition_code="SAMPLE-EDITION-A",
        ),
    )


def _period() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _proof(
            "policy",
            insurer="Sample Assurance",
            product_code="SAMPLE-PRODUCT-A",
            contract_date="2024-06-15",
        ),
        _proof(
            "terms",
            insurer="Sample Assurance",
            product_code="SAMPLE-PRODUCT-A",
            applicability_start="2024-01-01",
            applicability_end="2024-12-31",
        ),
    )


def _set(proof: dict[str, Any], field: str, value: str) -> None:
    proof["facts"] = [fact for fact in proof["facts"] if fact["field"] != field]
    proof["facts"].append({"field": field, "value": value})


def test_explicit_edition_reference_needs_no_product_or_contract_date() -> None:
    policy, terms = _references()
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "MATCH"
    assert assessment.matched_by == "EXPLICIT_EDITION_REFERENCE"
    assert set(assessment.evidence_fields) == {
        ("policy", "insurer"),
        ("terms", "insurer"),
        ("policy", "terms_reference"),
        ("policy", "edition_reference"),
        ("terms", "terms_code"),
        ("terms", "edition_code"),
    }


def test_display_normalization_preserves_code_identity() -> None:
    policy, terms = _references()
    _set(policy, "insurer", "  ＳＡＭＰＬＥ　ＡＳＳＵＲＡＮＣＥ  ")
    _set(policy, "terms_reference", "ｓａｍｐｌｅ-ｔｅｒｍｓ-ａ")
    _set(policy, "edition_reference", "sample-edition-a")
    assert (
        assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT).status
        == "MATCH"
    )


@pytest.mark.parametrize("contract_date", ["2024-01-01", "2024-02-29", "2024-12-31"])
def test_product_code_and_printed_period_include_both_boundaries(contract_date: str) -> None:
    policy, terms = _period()
    _set(policy, "contract_date", contract_date)
    assessment = assess_terms_applicability(policy, terms)
    assert assessment.status == "MATCH"
    assert assessment.matched_by == "PRODUCT_CODE_PRINTED_PERIOD"
    assert set(assessment.evidence_fields) == {
        ("policy", "insurer"),
        ("terms", "insurer"),
        ("policy", "product_code"),
        ("terms", "product_code"),
        ("policy", "contract_date"),
        ("terms", "applicability_start"),
        ("terms", "applicability_end"),
    }


def test_printed_start_does_not_require_an_optional_end() -> None:
    policy, terms = _period()
    terms["facts"] = [fact for fact in terms["facts"] if fact["field"] != "applicability_end"]
    _set(policy, "contract_date", "2025-01-01")
    assessment = assess_terms_applicability(policy, terms)
    assert assessment.status == "MATCH"
    assert ("terms", "applicability_end") not in assessment.evidence_fields


@pytest.mark.parametrize("references", [False, True])
def test_different_product_displays_do_not_override_matching_codes(references: bool) -> None:
    policy, terms = _references() if references else _period()
    _set(policy, "product_name", "Sample Certificate Display")
    _set(terms, "product_name", "Sample Terms Display")
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "MATCH"
    assert all(field != "product_name" for _, field in assessment.evidence_fields)


@pytest.mark.parametrize("flags", [frozenset(), frozenset({"terms_reference"})])
def test_general_reference_is_not_an_explicit_edition_selection(flags: frozenset[str]) -> None:
    policy, terms = _references()
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=flags)
    assert assessment.status == "UNKNOWN"
    assert assessment.matched_by is None


@pytest.mark.parametrize("multiple", [False, True])
def test_general_citations_do_not_gate_an_independent_printed_period(multiple: bool) -> None:
    policy, terms = _period()
    _set(policy, "terms_reference", "SAMPLE-OTHER-TERMS")
    _set(terms, "terms_code", "SAMPLE-TERMS-A")
    if multiple:
        policy["facts"].append({"field": "terms_reference", "value": "SAMPLE-SECOND-TERMS"})
        policy["unresolved_fields"] = ["terms_reference"]
    assessment = assess_terms_applicability(policy, terms)
    assert assessment.status == "MATCH"
    assert assessment.matched_by == "PRODUCT_CODE_PRINTED_PERIOD"
    assert ("policy", "terms_reference") not in assessment.evidence_fields


@pytest.mark.parametrize("field", ["terms_reference", "edition_reference"])
def test_multiple_applicable_references_require_scope_resolution_even_with_valid_dates(
    field: str,
) -> None:
    policy, terms = _period()
    referenced_policy, referenced_terms = _references()
    policy["facts"].extend(referenced_policy["facts"][1:])
    terms["facts"].extend(referenced_terms["facts"][1:])
    policy["facts"].append({"field": field, "value": "SAMPLE-OTHER"})
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "UNKNOWN"
    assert "MULTIPLE_APPLICATION_REFERENCES" in assessment.reason_codes


def test_repeated_normalized_reference_is_one_value() -> None:
    policy, terms = _references()
    policy["facts"].append({"field": "terms_reference", "value": " sample-terms-a "})
    assert (
        assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT).status
        == "MATCH"
    )


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("terms_reference", "TERMS_REFERENCE_MISMATCH"),
        ("edition_reference", "EDITION_REFERENCE_MISMATCH"),
    ],
)
def test_explicit_reference_contradiction_dominates_matching_product_and_dates(
    field: str, reason: str
) -> None:
    policy, terms = _period()
    referenced_policy, referenced_terms = _references()
    policy["facts"].extend(referenced_policy["facts"][1:])
    terms["facts"].extend(referenced_terms["facts"][1:])
    _set(policy, field, "SAMPLE-OTHER")
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "NO_MATCH"
    assert reason in assessment.reason_codes
    assert assessment.matched_by is None


@pytest.mark.parametrize(
    ("field", "reason"),
    [("insurer", "INSURER_MISMATCH"), ("product_code", "PRODUCT_CODE_MISMATCH")],
)
def test_resolved_identity_contradiction_dominates_exact_edition_reference(
    field: str, reason: str
) -> None:
    policy, terms = _references()
    if field == "product_code":
        _set(terms, field, "SAMPLE-PRODUCT-A")
    _set(policy, field, "SAMPLE-OTHER")
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "NO_MATCH"
    assert reason in assessment.reason_codes


def test_punctuation_is_not_removed_to_equate_different_codes() -> None:
    policy, terms = _period()
    _set(policy, "product_code", "SAMPLEPRODUCTA")
    assert assess_terms_applicability(policy, terms).status == "NO_MATCH"


@pytest.mark.parametrize("contract_date", ["2023-12-31", "2025-01-01"])
def test_printed_period_contradiction_dominates_exact_edition_reference(contract_date: str) -> None:
    policy, terms = _references()
    _set(policy, "contract_date", contract_date)
    _set(terms, "applicability_start", "2024-01-01")
    _set(terms, "applicability_end", "2024-12-31")
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "NO_MATCH"
    assert "CONTRACT_DATE_OUTSIDE_PRINTED_PERIOD" in assessment.reason_codes


@pytest.mark.parametrize("side", ["policy", "terms"])
@pytest.mark.parametrize("state", ["conflicting_fields", "unresolved_fields"])
def test_uncertain_identity_does_not_choose_a_convenient_scalar(side: str, state: str) -> None:
    policy, terms = _references()
    proof = policy if side == "policy" else terms
    proof[state] = ["insurer"]
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "UNKNOWN"
    assert "IDENTITY_METADATA_UNCERTAIN" in assessment.reason_codes


def test_two_unflagged_scalar_values_are_still_uncertain() -> None:
    policy, terms = _period()
    terms["facts"].append({"field": "product_code", "value": "SAMPLE-OTHER"})
    assert assess_terms_applicability(policy, terms).status == "UNKNOWN"


@pytest.mark.parametrize("state", ["conflicting_fields", "unresolved_fields"])
def test_uncertain_explicit_reference_blocks_the_independent_date_route(state: str) -> None:
    policy, terms = _period()
    _set(policy, "terms_reference", "SAMPLE-TERMS-A")
    policy[state] = ["terms_reference"]
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "UNKNOWN"


@pytest.mark.parametrize(
    ("side", "field"),
    [("policy", "contract_date"), ("terms", "applicability_start"), ("terms", "applicability_end")],
)
def test_uncertain_used_period_cannot_be_bypassed_by_a_reference(side: str, field: str) -> None:
    policy, terms = _references()
    _set(policy, "contract_date", "2024-06-15")
    _set(terms, "applicability_start", "2024-01-01")
    _set(terms, "applicability_end", "2024-12-31")
    (policy if side == "policy" else terms)["unresolved_fields"] = [field]
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "UNKNOWN"
    assert "PERIOD_METADATA_UNCERTAIN" in assessment.reason_codes


def test_unrelated_uncertainty_does_not_gate_either_route() -> None:
    for policy, terms in (_references(), _period()):
        policy["unresolved_fields"] = ["product_name", "edition_date"]
        terms["conflicting_fields"] = ["product_name", "edition_date"]
        assessment = assess_terms_applicability(
            policy, terms, explicit_application_fields=_EXPLICIT
        )
        assert assessment.status == "MATCH"


@pytest.mark.parametrize(
    ("side", "field", "value"),
    [
        ("policy", "contract_date", "2023-02-29"),
        ("policy", "contract_date", "2024-01-01T00:00:00"),
        ("terms", "applicability_start", "2024-02-30"),
        ("terms", "applicability_end", "2024-13-01"),
    ],
)
def test_invalid_calendar_values_are_unknown_not_negative_matches(
    side: str, field: str, value: str
) -> None:
    policy, terms = _period()
    _set(policy if side == "policy" else terms, field, value)
    assert assess_terms_applicability(policy, terms).status == "UNKNOWN"


def test_reversed_period_is_unknown_even_when_one_bound_would_exclude_the_contract() -> None:
    policy, terms = _period()
    _set(terms, "applicability_start", "2025-01-01")
    assessment = assess_terms_applicability(policy, terms)
    assert assessment.status == "UNKNOWN"
    assert "INVALID_PRINTED_PERIOD" in assessment.reason_codes


def test_edition_date_and_product_name_do_not_supply_missing_applicability_evidence() -> None:
    policy, terms = _period()
    terms["facts"] = [
        fact
        for fact in terms["facts"]
        if fact["field"] not in {"applicability_start", "applicability_end"}
    ]
    _set(terms, "edition_date", "2024-01-01")
    assert assess_terms_applicability(policy, terms).status == "UNKNOWN"


def test_product_name_similarity_does_not_replace_product_code_evidence() -> None:
    policy, terms = _period()
    for proof in (policy, terms):
        proof["facts"] = [fact for fact in proof["facts"] if fact["field"] != "product_code"]
        _set(proof, "product_name", "Sample Product")
    assert assess_terms_applicability(policy, terms).status == "UNKNOWN"


def test_contract_start_is_not_an_explicit_metadata_contract_date() -> None:
    policy, terms = _period()
    policy["facts"] = [fact for fact in policy["facts"] if fact["field"] != "contract_date"]
    _set(policy, "contract_start", "2024-06-15")
    assert assess_terms_applicability(policy, terms).status == "UNKNOWN"


@pytest.mark.parametrize("side", ["policy", "terms"])
def test_missing_insurer_does_not_allow_an_exact_reference_to_cross_insurer_scope(
    side: str,
) -> None:
    policy, terms = _references()
    source = policy if side == "policy" else terms
    source["facts"] = [fact for fact in source["facts"] if fact["field"] != "insurer"]
    assert (
        assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT).status
        == "UNKNOWN"
    )


def test_uncertain_product_code_cannot_be_ignored_by_an_exact_reference() -> None:
    policy, terms = _references()
    _set(policy, "product_code", "SAMPLE-PRODUCT-A")
    policy["unresolved_fields"] = ["product_code"]
    assert (
        assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT).status
        == "UNKNOWN"
    )


def test_invalid_period_cannot_be_replaced_by_an_exact_reference() -> None:
    policy, terms = _references()
    _set(policy, "contract_date", "2024-06-15")
    _set(terms, "applicability_start", "2025-01-01")
    _set(terms, "applicability_end", "2024-12-31")
    assessment = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    assert assessment.status == "UNKNOWN"
    assert "INVALID_PRINTED_PERIOD" in assessment.reason_codes


@pytest.mark.parametrize(
    ("policy_role", "terms_role"),
    [("terms", "terms"), ("application", "terms"), ("amendment", "terms"), ("policy", "policy")],
)
def test_only_certificate_to_terms_roles_are_supported(policy_role: str, terms_role: str) -> None:
    policy, terms = _period()
    policy["role"], terms["role"] = policy_role, terms_role
    assert assess_terms_applicability(policy, terms).status == "UNKNOWN"


def test_unrecognized_explicit_application_flag_cannot_supply_authority() -> None:
    policy, terms = _references()
    assessment = assess_terms_applicability(
        policy, terms, explicit_application_fields=frozenset({"product_name", *_EXPLICIT})
    )
    assert assessment.status == "UNKNOWN"


def test_assessment_is_frozen_deterministic_and_does_not_modify_input() -> None:
    policy, terms = _references()
    original = deepcopy((policy, terms))
    first = assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT)
    policy["facts"].reverse()
    terms["facts"].reverse()
    assert assess_terms_applicability(policy, terms, explicit_application_fields=_EXPLICIT) == first
    policy["facts"].reverse()
    terms["facts"].reverse()
    assert (policy, terms) == original
    with pytest.raises(FrozenInstanceError):
        first.status = "UNKNOWN"  # type: ignore[misc]

"""Bound private pages need one exact native enrolled name, not similar amounts."""

from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from familycare_api.insurance_reconciliation.canonical_match import (
    ProgramEnrollmentSource,
    VerifiedDocumentBinding,
    match_canonical_enrollment,
)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _private() -> dict[str, Any]:
    return {
        "name": "Sample Rider",
        "sum_assured_krw": 317,
        "certificate_review": {
            "name": "Sample Rider",
            "enrollment_decision": "MATCH",
            "component_class": "BENEFIT_COVERAGE",
            "evidence_inherited_from_rider_id": None,
            "evidence_locations": [
                {"document_alias": "Synthetic Policy", "line": 700, "physical_page": 2}
            ],
        },
    }


def _binding() -> VerifiedDocumentBinding:
    return VerifiedDocumentBinding(
        source_binding_id=_id(1),
        document_version_id=_id(2),
        content_sha256="a" * 64,
        page_count=3,
        document_kind="policy",
    )


def _candidate() -> ProgramEnrollmentSource:
    return ProgramEnrollmentSource(
        household_space_id=_id(3),
        family_member_id=_id(4),
        policy_contract_id=_id(5),
        rider_id=_id(6),
        original_rider_name="Sample Rider",
        document_version_id=_id(2),
        content_sha256="a" * 64,
        physical_page=2,
        publication_candidate_version_id=_id(7),
        evidence_id=_id(8),
        generation_id=_id(10),
        physical_locator={
            "schema_version": "enrollment-physical-v1",
            "content_sha256": "a" * 64,
            "physical_page": 2,
            "name_bbox": [10.125, 20.25, 73.625, 30.75],
        },
        source_refs=(
            {
                "evidence_id": str(_id(8)),
                "document_version_id": str(_id(2)),
                "extraction_id": str(_id(9)),
                "node_id": "b" * 64,
                "page": 2,
                "start": 0,
                "end": 40,
                "primary": True,
                "source_role": "policy",
            },
        ),
    )


def _match(
    private: dict[str, Any] | None = None,
    *,
    binding: VerifiedDocumentBinding | None = None,
    candidates: tuple[ProgramEnrollmentSource, ...] | None = None,
) -> Any:
    return match_canonical_enrollment(
        private if private is not None else _private(),
        {"Synthetic Policy": binding if binding is not None else _binding()},
        candidates if candidates is not None else (_candidate(),),
        expected_family_member_id=_id(4),
    )


def test_unique_native_enrollment_returns_detached_exact_identity_proposal() -> None:
    private, candidate = _private(), _candidate()
    result = _match(private, candidates=(candidate,))
    assert result is not None and result.policy_contract_id == _id(5) and result.rider_id == _id(6)
    assert result.family_member_id == _id(4)
    assert result.strategy == "UNIQUE_ENROLLED_NAME_ON_BOUND_PAGE"
    proof = result.proofs[0]
    assert proof.source_binding_id == _id(1)
    assert proof.publication_candidate_version_id == _id(7) and proof.evidence_id == _id(8)
    assert proof.generation_id == _id(10)
    assert proof.private_evidence_location == private["certificate_review"]["evidence_locations"][0]
    assert (
        proof.physical_locator == candidate.physical_locator
        and proof.source_refs == candidate.source_refs
    )
    private["certificate_review"]["evidence_locations"][0]["line"] = 1
    assert proof.private_evidence_location["line"] == 700


def test_line_is_retained_as_package_provenance_and_never_used_as_ir_order() -> None:
    private = _private()
    private["certificate_review"]["evidence_locations"][0]["line"] = 9000
    result = _match(private)
    assert result is not None and result.rider_id == _id(6)
    assert result.proofs[0].private_evidence_location["line"] == 9000


def test_worker_name_normalization_and_equal_bytes_across_document_versions() -> None:
    candidate = _candidate()
    candidate = replace(
        candidate,
        document_version_id=_id(22),
        original_rider_name="ＳＡＭＰＬＥ  Rider",
        source_refs=({**candidate.source_refs[0], "document_version_id": str(_id(22))},),
    )
    assert _match(candidates=(candidate,)) is not None


def test_amount_is_neither_required_nor_used_to_select_one_of_two_rows() -> None:
    private = _private()
    private["sum_assured_krw"] = 999999
    assert _match(private) is not None
    private.pop("sum_assured_krw")
    second = replace(
        _candidate(),
        rider_id=_id(66),
        physical_locator={
            **_candidate().physical_locator,
            "name_bbox": [10.125, 50.25, 73.625, 60.75],
        },
    )
    assert _match(private, candidates=(_candidate(), second)) is None


@pytest.mark.parametrize(
    "variant",
    [
        "unknown_enrollment",
        "nonbenefit",
        "inherited",
        "name_conflict",
        "no_location",
        "bad_line",
        "bad_page",
        "unknown_alias",
        "extra_location_key",
        "conflict",
    ],
)
def test_private_enrollment_requires_direct_supported_locations(variant: str) -> None:
    private = _private()
    review = private["certificate_review"]
    if variant == "unknown_enrollment":
        review["enrollment_decision"] = "UNKNOWN"
    elif variant == "nonbenefit":
        review["component_class"] = "NON_BENEFIT_CONTRACT_COMPONENT"
    elif variant == "inherited":
        review["evidence_inherited_from_rider_id"] = "synthetic-parent-rider"
    elif variant == "name_conflict":
        review["name"] = "Another Rider"
    elif variant == "no_location":
        review["evidence_locations"] = []
    elif variant == "bad_line":
        review["evidence_locations"][0]["line"] = 0
    elif variant == "bad_page":
        review["evidence_locations"][0]["physical_page"] = 4
    elif variant == "unknown_alias":
        review["evidence_locations"][0]["document_alias"] = "Another Policy"
    elif variant == "extra_location_key":
        review["evidence_locations"][0]["column"] = 3
    else:
        review["conflict"] = True
    assert _match(private) is None


@pytest.mark.parametrize(
    "variant",
    [
        "wrong_hash",
        "terms",
        "unverified",
        "ocr",
        "wrong_member",
        "wrong_locator_page",
        "wrong_locator_hash",
        "missing_box",
        "bad_box",
        "unbound_evidence",
        "wrong_ref_page",
        "partial_ref",
        "conflict",
    ],
)
def test_candidate_requires_verified_native_provenance(variant: str) -> None:
    candidate = _candidate()
    if variant == "wrong_hash":
        candidate = replace(candidate, content_sha256="c" * 64)
    elif variant == "terms":
        candidate = replace(
            candidate, source_refs=({**candidate.source_refs[0], "source_role": "terms"},)
        )
    elif variant == "unverified":
        candidate = replace(candidate, authority="USER_CONFIRMED")
    elif variant == "ocr":
        candidate = replace(candidate, source_layer="ocr")
    elif variant == "wrong_member":
        candidate = replace(candidate, family_member_id=_id(44))
    elif variant == "wrong_locator_page":
        candidate = replace(
            candidate, physical_locator={**candidate.physical_locator, "physical_page": 1}
        )
    elif variant == "wrong_locator_hash":
        candidate = replace(
            candidate, physical_locator={**candidate.physical_locator, "content_sha256": "c" * 64}
        )
    elif variant == "missing_box":
        candidate = replace(
            candidate, physical_locator={**candidate.physical_locator, "name_bbox": None}
        )
    elif variant == "bad_box":
        candidate = replace(
            candidate, physical_locator={**candidate.physical_locator, "name_bbox": [10, 20, 5, 30]}
        )
    elif variant == "unbound_evidence":
        candidate = replace(candidate, evidence_id=_id(88))
    elif variant == "wrong_ref_page":
        candidate = replace(candidate, source_refs=({**candidate.source_refs[0], "page": 1},))
    elif variant == "partial_ref":
        candidate = replace(candidate, source_refs=({**candidate.source_refs[0], "end": 0},))
    else:
        candidate = replace(candidate, conflict=True)
    assert _match(candidates=(candidate,)) is None


@pytest.mark.parametrize("variant", ["contract", "member", "household", "physical_row"])
def test_an_ambiguous_equal_name_cannot_be_filtered_into_a_match(variant: str) -> None:
    first = _candidate()
    if variant == "contract":
        second = replace(first, policy_contract_id=_id(55))
    elif variant == "member":
        second = replace(first, family_member_id=_id(44))
    elif variant == "household":
        second = replace(first, household_space_id=_id(33))
    else:
        second = replace(
            first,
            physical_locator={
                **first.physical_locator,
                "name_bbox": [10.125, 50.25, 73.625, 60.75],
            },
        )
    assert _match(candidates=(first, second)) is None


def test_repeated_publications_keep_all_proofs_without_making_another_identity() -> None:
    first = _candidate()
    second = replace(first, publication_candidate_version_id=_id(77))
    result = _match(candidates=(second, first))
    assert result is not None
    assert {proof.publication_candidate_version_id for proof in result.proofs} == {_id(7), _id(77)}
    assert _match(candidates=(first, second)) == result


def test_multiple_private_locations_must_resolve_to_one_rider() -> None:
    private = _private()
    location = deepcopy(private["certificate_review"]["evidence_locations"][0])
    location.update(line=800, physical_page=3)
    private["certificate_review"]["evidence_locations"].append(location)
    second = replace(
        _candidate(),
        physical_page=3,
        physical_locator={**_candidate().physical_locator, "physical_page": 3},
        source_refs=({**_candidate().source_refs[0], "page": 3},),
    )
    assert _match(private, candidates=(_candidate(), second)) is not None
    assert _match(private, candidates=(_candidate(), replace(second, rider_id=_id(66)))) is None


@pytest.mark.parametrize(
    "change",
    [{"document_kind": "terms"}, {"page_count": 0}, {"content_sha256": "bad"}, {"conflict": True}],
)
def test_binding_must_be_a_verified_policy_document(change: dict[str, Any]) -> None:
    assert _match(binding=replace(_binding(), **change)) is None

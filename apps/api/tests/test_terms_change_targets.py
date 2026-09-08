"""Exact synthetic target identities never use a current display name as authority."""

from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest
from familycare_api.clauses.terms_change_targets import (
    TermsChangeTargetRequest,
    VerifiedContractCandidate,
    VerifiedEditionCandidate,
    VerifiedRiderCandidate,
    resolve_terms_change_targets,
)

HOUSEHOLD, MEMBER, POLICY, RIDER, OLD, NEW = (UUID(int=value) for value in range(101, 107))
CONTRACT = VerifiedContractCandidate(POLICY, HOUSEHOLD, MEMBER, "a" * 64, "sample assurance")
RIDER_SOURCE = VerifiedRiderCandidate(RIDER, HOUSEHOLD, POLICY, MEMBER, ("original sample rider",))
OLD_SOURCE = VerifiedEditionCandidate(
    OLD, HOUSEHOLD, "sample assurance", "sample-terms", "edition-a"
)
NEW_SOURCE = VerifiedEditionCandidate(
    NEW, HOUSEHOLD, "sample assurance", "sample-terms", "edition-b"
)


def _request(**changes):
    values = dict(
        household_space_id=HOUSEHOLD,
        status="MATCH",
        family_member_id=MEMBER,
        contract_number_sha256="a" * 64,
        insurer_key="sample assurance",
        scope_kind="RIDER",
        rider_name_key="original sample rider",
        operation="REPLACE",
        previous_terms_code="sample-terms",
        previous_edition_code="edition-a",
        new_terms_code="sample-terms",
        new_edition_code="edition-b",
    )
    values.update(changes)
    return TermsChangeTargetRequest(**values)


def _resolve(
    request=None,
    *,
    contracts=(CONTRACT,),
    riders=(RIDER_SOURCE,),
    editions=(OLD_SOURCE, NEW_SOURCE),
):
    return resolve_terms_change_targets(request or _request(), contracts, riders, editions)


def test_exact_existing_contract_rider_and_editions_are_resolved() -> None:
    selected = _resolve()
    assert selected.status == "MATCH"
    assert selected.policy_contract_id == POLICY and selected.family_member_id == MEMBER
    assert selected.rider_id == RIDER and selected.scope_resolved
    assert selected.previous_edition_id == OLD and selected.new_edition_id == NEW
    assert selected.clause_id is None


def test_same_number_insurer_and_member_on_two_contract_ids_remains_ambiguous() -> None:
    selected = _resolve(contracts=(CONTRACT, replace(CONTRACT, policy_contract_id=UUID(int=999))))
    assert selected.status == "UNKNOWN"
    assert selected.policy_contract_id is None and selected.rider_id is None
    assert not selected.scope_resolved
    assert "CONTRACT_TARGET_AMBIGUOUS" in selected.reason_codes


@pytest.mark.parametrize(
    "changes",
    [
        {"household_space_id": UUID(int=999)},
        {"family_member_id": UUID(int=999)},
        {"contract_number_sha256": "b" * 64},
        {"insurer_key": "other assurance"},
    ],
)
def test_other_contract_identities_are_excluded(changes) -> None:
    selected = _resolve(contracts=(replace(CONTRACT, **changes),))
    assert selected.status == "UNKNOWN" and selected.policy_contract_id is None


def test_repeated_proofs_of_the_same_exact_ids_do_not_create_ambiguity() -> None:
    selected = _resolve(
        contracts=(CONTRACT, CONTRACT),
        riders=(RIDER_SOURCE, RIDER_SOURCE),
        editions=(OLD_SOURCE, NEW_SOURCE, NEW_SOURCE),
    )
    assert selected == _resolve()


def test_current_display_name_without_original_source_alias_is_insufficient() -> None:
    selected = _resolve(
        _request(rider_name_key="current display name"),
        riders=(replace(RIDER_SOURCE, source_alias_keys=()),),
    )
    assert selected.status == "UNKNOWN" and selected.rider_id is None
    assert selected.policy_contract_id == POLICY and not selected.scope_resolved


def test_two_original_alias_matches_are_ambiguous_instead_of_picking_a_rider() -> None:
    selected = _resolve(riders=(RIDER_SOURCE, replace(RIDER_SOURCE, rider_id=UUID(int=999))))
    assert selected.status == "UNKNOWN" and selected.rider_id is None
    assert "RIDER_TARGET_AMBIGUOUS" in selected.reason_codes


@pytest.mark.parametrize("field", ["household_space_id", "policy_contract_id", "family_member_id"])
def test_rider_alias_from_another_exact_scope_is_excluded(field) -> None:
    selected = _resolve(riders=(replace(RIDER_SOURCE, **{field: UUID(int=999)}),))
    assert selected.rider_id is None and not selected.scope_resolved


def test_alias_normalization_does_not_remove_code_punctuation_or_invent_synonyms() -> None:
    assert (
        _resolve(
            _request(
                insurer_key="ＳＡＭＰＬＥ　ASSURANCE", rider_name_key=" Original  Sample Rider "
            )
        ).status
        == "MATCH"
    )
    selected = _resolve(_request(new_edition_code="editionb"))
    assert selected.new_edition_id is None and selected.previous_edition_id == OLD
    assert selected.status == "UNKNOWN"


def test_ambiguous_edition_does_not_choose_a_newer_or_smaller_identifier() -> None:
    selected = _resolve(
        editions=(OLD_SOURCE, NEW_SOURCE, replace(NEW_SOURCE, terms_edition_id=UUID(int=999)))
    )
    assert selected.status == "UNKNOWN" and selected.new_edition_id is None
    assert selected.previous_edition_id == OLD
    assert "NEW_EDITION_TARGET_AMBIGUOUS" in selected.reason_codes


def test_missing_new_edition_preserves_the_old_id_for_partial_event_selection() -> None:
    selected = _resolve(_request(new_edition_code=None))
    assert selected.status == "UNKNOWN" and selected.new_edition_id is None
    assert selected.previous_edition_id == OLD and selected.scope_resolved


def test_addition_requires_no_previous_edition_target() -> None:
    selected = _resolve(
        _request(operation="ADD", previous_terms_code=None, previous_edition_code=None)
    )
    assert selected.status == "MATCH" and selected.previous_edition_id is None
    assert selected.new_edition_id == NEW


def test_clause_scope_is_not_silently_widened_to_rider_or_contract() -> None:
    selected = _resolve(_request(scope_kind="CLAUSE", clause_label_key="제7조"))
    assert selected.status == "UNKNOWN" and not selected.scope_resolved
    assert selected.policy_contract_id == POLICY and selected.rider_id == RIDER
    assert selected.clause_id is None and selected.new_edition_id == NEW
    assert "CLAUSE_TARGET_UNRESOLVED" in selected.reason_codes


def test_source_unknown_remains_unknown_even_when_ids_are_unique() -> None:
    selected = _resolve(_request(status="UNKNOWN", reason_codes=("EFFECTIVE_DATE_UNRESOLVED",)))
    assert selected.status == "UNKNOWN" and selected.policy_contract_id == POLICY
    assert selected.previous_edition_id == OLD and selected.new_edition_id == NEW
    assert "EFFECTIVE_DATE_UNRESOLVED" in selected.reason_codes


def test_source_no_match_does_not_bind_existing_contracts_or_editions() -> None:
    selected = _resolve(_request(status="NO_MATCH", reason_codes=("WRONG_MEMBER",)))
    assert selected.status == "NO_MATCH"
    assert selected.policy_contract_id is None and selected.rider_id is None
    assert selected.previous_edition_id is None and selected.new_edition_id is None
    assert not selected.scope_resolved


def test_target_inputs_and_results_are_immutable_and_do_not_print_private_keys() -> None:
    request = _request()
    selected = _resolve(request)
    with pytest.raises(FrozenInstanceError):
        selected.policy_contract_id = UUID(int=999)
    assert "sample assurance" not in repr(request)
    assert "original sample rider" not in repr(RIDER_SOURCE)
    assert str(POLICY) not in repr(selected)


@pytest.mark.parametrize(
    "changes",
    [
        {"household_space_id": UUID(int=999)},
        {"insurer_key": "other assurance"},
        {"terms_code": "other-terms"},
        {"edition_code": "edition-c"},
    ],
)
def test_edition_codes_require_the_exact_household_and_insurer(changes) -> None:
    selected = _resolve(editions=(OLD_SOURCE, replace(NEW_SOURCE, **changes)))
    assert selected.previous_edition_id == OLD and selected.new_edition_id is None
    assert selected.status == "UNKNOWN"


def test_contract_scope_does_not_require_or_invent_a_rider() -> None:
    selected = _resolve(_request(scope_kind="CONTRACT", rider_name_key=None))
    assert selected.status == "MATCH" and selected.scope_resolved
    assert selected.rider_id is None and selected.policy_contract_id == POLICY


@pytest.mark.parametrize("field", ["contract_number_sha256", "insurer_key", "family_member_id"])
def test_missing_contract_identity_is_partial_unknown_without_inventing_a_contract(field) -> None:
    selected = _resolve(_request(**{field: None}))
    assert selected.policy_contract_id is None and selected.status == "UNKNOWN"
    assert not selected.scope_resolved


def test_previous_edition_absence_does_not_discard_a_verified_new_edition() -> None:
    selected = _resolve(_request(previous_edition_code=None))
    assert selected.previous_edition_id is None and selected.new_edition_id == NEW
    assert selected.status == "UNKNOWN" and selected.scope_resolved


def test_missing_rider_and_contradictory_scope_never_become_contract_scope() -> None:
    selected = _resolve(_request(rider_name_key=None))
    assert not selected.scope_resolved and selected.scope_kind == "RIDER"
    assert selected.policy_contract_id == POLICY and selected.rider_id is None
    contradictory = _resolve(_request(scope_kind="CONTRACT"))
    assert contradictory.status == "UNKNOWN" and not contradictory.scope_resolved
    assert "CHANGE_SCOPE_CONFLICT" in contradictory.reason_codes


def test_known_clause_label_without_rider_still_needs_independent_clause_resolution() -> None:
    selected = _resolve(
        _request(scope_kind="CLAUSE", rider_name_key=None, clause_label_key="제7조")
    )
    assert selected.status == "UNKNOWN" and not selected.scope_resolved
    assert selected.policy_contract_id == POLICY and selected.rider_id is None
    assert selected.clause_id is None and selected.new_edition_id == NEW


def test_add_with_a_predecessor_and_self_replacement_are_not_verified_changes() -> None:
    assert _resolve(_request(operation="ADD")).status == "UNKNOWN"
    result = _resolve(_request(new_edition_code="edition-a"))
    assert result.status == "UNKNOWN"
    assert result.previous_edition_id == result.new_edition_id == OLD


def test_candidate_count_alias_count_and_reason_limits_fail_explicitly() -> None:
    from familycare_api.clauses.terms_change_targets import TermsChangeTargetError

    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(contracts=(CONTRACT,) * 513)
    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(riders=(replace(RIDER_SOURCE, source_alias_keys=("synthetic rider",) * 33),))
    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(_request(reason_codes=tuple(f"SYNTHETIC_REASON_{index}" for index in range(33))))
    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(riders=(replace(RIDER_SOURCE, source_alias_keys=("synthetic rider",) * 9),) * 512)


def test_malformed_inputs_are_rejected_without_echoing_identity_or_consuming_generators() -> None:
    from familycare_api.clauses.terms_change_targets import TermsChangeTargetError

    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(_request(contract_number_sha256="synthetic-invalid-digest"))
    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(contracts=iter((CONTRACT,)))
    with pytest.raises(TermsChangeTargetError, match="^TERMS_CHANGE_TARGET_INPUT_INVALID$"):
        _resolve(riders=(replace(RIDER_SOURCE, source_alias_keys=["synthetic rider"]),))


def test_candidate_input_order_does_not_change_the_unique_or_ambiguous_result() -> None:
    other = replace(CONTRACT, policy_contract_id=UUID(int=999))
    assert _resolve(contracts=(CONTRACT, other)) == _resolve(contracts=(other, CONTRACT))
    assert _resolve(editions=(OLD_SOURCE, NEW_SOURCE)) == _resolve(
        editions=(NEW_SOURCE, OLD_SOURCE)
    )

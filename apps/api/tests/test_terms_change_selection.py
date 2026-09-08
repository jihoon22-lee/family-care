"""Wholly synthetic event-date changes never establish enrollment or active status."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date
from uuid import UUID

import pytest
from familycare_api.clauses.terms_change_selection import (
    BaseTermsEdition,
    TermsChangeRelation,
    TermsSelectionScope,
    select_terms_for_event,
)

G, A, B, C, D = (UUID(int=value) for value in range(101, 106))
HOUSEHOLD, POLICY, MEMBER, RIDER_X, RIDER_Y, CLAUSE = (UUID(int=value) for value in range(201, 207))
SCOPE = TermsSelectionScope(HOUSEHOLD, POLICY, MEMBER, RIDER_X)
BASE = (BaseTermsEdition(G, "MATCH"), BaseTermsEdition(A, "MATCH"))
JUNE, JULY, AUGUST = date(2025, 6, 30), date(2025, 7, 1), date(2025, 8, 1)


def _change(**kwargs):
    values = dict(
        relation_id=UUID(int=301),
        scope=SCOPE,
        operation="REPLACE",
        previous_edition_id=A,
        new_edition_id=B,
        effective_from=JULY,
        effective_through=None,
        status="MATCH",
        reason_codes=("SOURCE_CHANGE_VERIFIED",),
        is_current=True,
    )
    values.update(kwargs)
    return TermsChangeRelation(**values)


def _select(*relations, event_date=JULY, scope=SCOPE, base=BASE):
    return select_terms_for_event(scope, event_date, base, relations)


def _statuses(selection):
    return {edition.edition_id: edition.status for edition in selection.editions}


def _matched(selection):
    return {edition.edition_id for edition in selection.editions if edition.status == "MATCH"}


def test_only_targeted_rider_replaces_its_terms_at_the_explicit_start() -> None:
    relation = _change()
    assert _matched(_select(relation, event_date=JUNE)) == {G, A}
    current = _select(relation)
    assert _matched(current) == {G, B}
    assert _statuses(current)[A] == "NO_MATCH"
    assert all(
        relation.relation_id in item.relation_ids
        for item in current.editions
        if item.edition_id in {A, B}
    )
    assert _matched(_select(relation, scope=replace(SCOPE, rider_id=RIDER_Y))) == {G, A}


@pytest.mark.parametrize("field", ["household_space_id", "policy_contract_id", "family_member_id"])
def test_other_exact_scopes_remain_unchanged(field: str) -> None:
    assert _matched(_select(_change(), scope=replace(SCOPE, **{field: UUID(int=999)}))) == {G, A}


def test_explicit_change_interval_has_inclusive_end_and_then_base_applies() -> None:
    relation = _change(effective_through=date(2025, 7, 31))
    assert _matched(_select(relation, event_date=date(2025, 7, 31))) == {G, B}
    assert _matched(_select(relation, event_date=AUGUST)) == {G, A}


def test_addition_keeps_independent_base_terms() -> None:
    relation = _change(operation="ADD", previous_edition_id=None)
    assert _matched(_select(relation)) == {G, A, B}
    assert _matched(_select(relation, event_date=JUNE)) == {G, A}


@pytest.mark.parametrize("changes", [{"effective_from": None}, {"status": "UNKNOWN"}])
def test_uncertain_change_affects_only_its_exact_editions(changes) -> None:
    selected = _statuses(_select(_change(**changes)))
    assert selected == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN"}


def test_same_scope_competing_replacements_do_not_choose_a_timestamp_winner() -> None:
    first = _change()
    second = _change(relation_id=UUID(int=302), new_edition_id=C)
    selected = _statuses(_select(first, second))
    assert selected == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN", C: "UNKNOWN"}
    assert _select(first, second) == _select(second, first)


def test_explicit_date_ordered_replacement_chain_is_followed() -> None:
    first = _change()
    second = _change(
        relation_id=UUID(int=302), previous_edition_id=B, new_edition_id=C, effective_from=AUGUST
    )
    assert _matched(_select(second, first)) == {G, B}
    selected = _select(second, first, event_date=AUGUST)
    assert _matched(selected) == {G, C}
    assert _statuses(selected)[B] == "NO_MATCH"


def test_specific_target_overrides_only_the_same_previous_edition_relation() -> None:
    broad = _change(scope=replace(SCOPE, rider_id=None))
    narrow = _change(relation_id=UUID(int=302), new_edition_id=C)
    unrelated = _change(
        relation_id=UUID(int=303),
        operation="ADD",
        previous_edition_id=None,
        new_edition_id=D,
        scope=replace(SCOPE, rider_id=None),
    )
    assert _matched(_select(broad, narrow, unrelated)) == {G, C, D}
    assert _matched(_select(broad, narrow, unrelated, scope=replace(SCOPE, rider_id=RIDER_Y))) == {
        G,
        B,
        D,
    }


def test_selection_and_inputs_are_immutable_and_safe_to_retain() -> None:
    relation = _change()
    before = deepcopy((BASE, relation))
    past = _select(relation, event_date=JUNE)
    snapshot = deepcopy(past)
    _select(relation)
    assert (BASE, relation) == before and past == snapshot
    with pytest.raises(FrozenInstanceError):
        past.event_date = JULY
    assert "SOURCE_CHANGE_VERIFIED" not in repr(relation)
    assert str(POLICY) not in repr(past)


def test_agreeing_replacement_evidence_keeps_both_relation_ids() -> None:
    first = _change()
    second = replace(first, relation_id=UUID(int=302))
    result = _select(first, second)
    assert _matched(result) == {G, B}
    assert set(result.applied_relation_ids) == {first.relation_id, second.relation_id}


def test_duplicate_identical_input_is_idempotent_but_conflicting_id_is_unknown() -> None:
    first = _change()
    assert _select(first, first) == _select(first)
    result = _select(first, replace(first, new_edition_id=C))
    assert _statuses(result) == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN", C: "UNKNOWN"}
    assert first.relation_id in result.uncertain_relation_ids


@pytest.mark.parametrize(
    "changes",
    [
        {"effective_through": JUNE},
        {"operation": "REMOVE"},
        {"previous_edition_id": A, "operation": "ADD"},
    ],
)
def test_invalid_change_is_isolated_to_its_known_targets(changes) -> None:
    assert _statuses(_select(_change(**changes))) == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN"}


def test_missing_predecessor_does_not_guess_which_base_edition_to_remove() -> None:
    assert _statuses(_select(_change(previous_edition_id=None))) == {
        G: "MATCH",
        A: "MATCH",
        B: "UNKNOWN",
    }


def test_currentness_and_decisive_rejection_are_not_new_selection_authority() -> None:
    assert _matched(_select(_change(is_current=False))) == {G, A}
    assert _matched(_select(_change(status="NO_MATCH"))) == {G, A}
    assert not _select(_change(is_current=False)).applied_relation_ids


def test_missing_event_date_affects_only_editions_in_changes() -> None:
    assert _statuses(_select(_change(), event_date=None)) == {
        G: "MATCH",
        A: "UNKNOWN",
        B: "UNKNOWN",
    }


def test_unknown_addition_does_not_remove_a_base_edition() -> None:
    assert _statuses(
        _select(_change(operation="ADD", previous_edition_id=None, status="UNKNOWN"))
    ) == {G: "MATCH", A: "MATCH", B: "UNKNOWN"}


def test_later_uncertainty_does_not_erase_a_proven_earlier_replacement() -> None:
    later = _change(
        relation_id=UUID(int=302),
        previous_edition_id=B,
        new_edition_id=C,
        effective_from=AUGUST,
        status="UNKNOWN",
    )
    assert _statuses(_select(_change(), later, event_date=AUGUST)) == {
        G: "MATCH",
        A: "NO_MATCH",
        B: "UNKNOWN",
        C: "UNKNOWN",
    }


def test_cyclic_changes_cannot_select_an_edition_or_loop() -> None:
    back = _change(relation_id=UUID(int=302), previous_edition_id=B, new_edition_id=A)
    result = _select(_change(), back)
    assert _statuses(result) == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN"}
    assert set(result.uncertain_relation_ids) == {_change().relation_id, back.relation_id}


def test_self_replacement_is_unresolved() -> None:
    assert _statuses(_select(_change(new_edition_id=A))) == {G: "MATCH", A: "UNKNOWN"}


def test_reverse_dated_chain_cannot_make_its_final_edition_applicable() -> None:
    late_parent = _change(effective_from=AUGUST)
    earlier_child = _change(relation_id=UUID(int=302), previous_edition_id=B, new_edition_id=C)
    result = _select(late_parent, earlier_child, event_date=AUGUST)
    assert _statuses(result)[C] == "UNKNOWN"
    assert _statuses(result)[G] == "MATCH"


def test_later_competing_change_still_requires_an_explicit_replacement_chain() -> None:
    competitor = _change(relation_id=UUID(int=302), new_edition_id=C, effective_from=AUGUST)
    assert _matched(_select(_change(), competitor)) == {G, B}
    assert _statuses(_select(_change(), competitor, event_date=AUGUST)) == {
        G: "MATCH",
        A: "UNKNOWN",
        B: "UNKNOWN",
        C: "UNKNOWN",
    }


def test_clause_specificity_only_applies_to_the_exact_requested_clause() -> None:
    general = _change(scope=replace(SCOPE, rider_id=None))
    rider = _change(relation_id=UUID(int=302), new_edition_id=C)
    clause = _change(
        relation_id=UUID(int=303), scope=replace(SCOPE, clause_id=CLAUSE), new_edition_id=D
    )
    assert _matched(_select(general, rider, clause, scope=replace(SCOPE, clause_id=CLAUSE))) == {
        G,
        D,
    }
    assert _matched(
        _select(general, rider, clause, scope=replace(SCOPE, clause_id=UUID(int=888)))
    ) == {G, C}
    assert _matched(_select(general, rider, clause, scope=replace(SCOPE, rider_id=None))) == {G, B}


def test_specificity_does_not_hide_conflict_in_an_unconnected_edition_chain() -> None:
    base = (*BASE, BaseTermsEdition(C, "MATCH"))
    unrelated = _change(
        relation_id=UUID(int=302),
        previous_edition_id=C,
        new_edition_id=D,
        scope=replace(SCOPE, rider_id=None),
    )
    conflicting = replace(unrelated, relation_id=UUID(int=303), new_edition_id=UUID(int=106))
    result = _select(_change(), unrelated, conflicting, base=base)
    assert _matched(result) == {G, B}
    assert all(_statuses(result)[edition] == "UNKNOWN" for edition in (C, D, UUID(int=106)))


def test_replacement_needs_a_supported_predecessor_in_base_or_earlier_chain() -> None:
    base = (BaseTermsEdition(G, "MATCH"), BaseTermsEdition(A, "UNKNOWN"))
    assert _statuses(_select(_change(), base=base)) == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN"}


def test_expired_parent_overlay_does_not_certify_its_successor() -> None:
    first = _change(effective_through=date(2025, 7, 31))
    second = _change(
        relation_id=UUID(int=302), previous_edition_id=B, new_edition_id=C, effective_from=AUGUST
    )
    selected = _statuses(_select(first, second, event_date=AUGUST))
    assert selected == {G: "MATCH", A: "MATCH", B: "UNKNOWN", C: "UNKNOWN"}


def test_conflicting_base_judgments_do_not_supply_a_replacement_predecessor() -> None:
    result = _select(_change(), base=(*BASE, BaseTermsEdition(A, "NO_MATCH")))
    assert _statuses(result) == {G: "MATCH", A: "UNKNOWN", B: "UNKNOWN"}


def test_inputs_are_bounded_before_graph_or_output_expansion() -> None:
    from familycare_api.clauses.terms_change_selection import TermsSelectionError

    with pytest.raises(TermsSelectionError, match="^TERMS_SELECTION_INPUT_INVALID$"):
        _select(*(_change(relation_id=UUID(int=1000 + index)) for index in range(4097)))
    with pytest.raises(TermsSelectionError, match="^TERMS_SELECTION_INPUT_INVALID$"):
        _select(
            base=tuple(BaseTermsEdition(UUID(int=1000 + index), "MATCH") for index in range(4097))
        )


def test_reason_output_budget_is_bounded_across_relations() -> None:
    from familycare_api.clauses.terms_change_selection import TermsSelectionError

    relations = tuple(
        _change(
            relation_id=UUID(int=400 + index),
            operation="ADD",
            previous_edition_id=None,
            new_edition_id=UUID(int=500 + index),
            reason_codes=tuple(f"SYNTHETIC_REASON_{index}_{number}" for number in range(32)),
        )
        for index in range(3)
    )
    with pytest.raises(TermsSelectionError, match="^TERMS_SELECTION_INPUT_INVALID$"):
        _select(*relations)


def test_non_sequence_input_is_rejected_without_consuming_an_iterator() -> None:
    from familycare_api.clauses.terms_change_selection import TermsSelectionError

    with pytest.raises(TermsSelectionError, match="^TERMS_SELECTION_INPUT_INVALID$"):
        select_terms_for_event(SCOPE, JULY, BASE, iter((_change(),)))


@pytest.mark.parametrize("status", ["UNKNOWN", "MATCH"])
def test_missing_new_edition_is_timed_and_only_withholds_its_known_predecessor(status) -> None:
    relation = _change(new_edition_id=None, status=status)
    before = _select(relation, event_date=JUNE)
    assert _statuses(before) == {G: "MATCH", A: "MATCH"}
    assert not before.uncertain_relation_ids and not before.scope_uncertainties
    selected = _select(relation)
    assert _statuses(selected) == {G: "MATCH", A: "UNKNOWN"}
    assert selected.uncertain_relation_ids == (relation.relation_id,)
    previous = next(item for item in selected.editions if item.edition_id == A)
    assert previous.relation_ids == (relation.relation_id,)
    assert "CHANGE_TARGET_UNRESOLVED" in previous.reason_codes
    assert selected.scope_uncertainties[0].relation_id == relation.relation_id
    assert "CHANGE_TARGET_UNRESOLVED" in selected.scope_uncertainties[0].reason_codes
    assert not selected.applied_relation_ids
    assert _statuses(before) == {G: "MATCH", A: "MATCH"}


@pytest.mark.parametrize("operation", ["ADD", "REPLACE"])
def test_both_missing_edition_ids_preserve_base_and_return_scope_uncertainty(operation) -> None:
    relation = _change(
        operation=operation, previous_edition_id=None, new_edition_id=None, status="UNKNOWN"
    )
    selected = _select(relation)
    assert _statuses(selected) == {G: "MATCH", A: "MATCH"}
    assert selected.uncertain_relation_ids == (relation.relation_id,)
    assert len(selected.scope_uncertainties) == 1
    assert selected.scope_uncertainties[0].relation_id == relation.relation_id
    assert "CHANGE_TARGET_UNRESOLVED" in selected.scope_uncertainties[0].reason_codes
    assert all(not edition.relation_ids for edition in selected.editions)
    assert not _select(relation, event_date=JUNE).scope_uncertainties


@pytest.mark.parametrize("previous", [None, A])
def test_unresolved_addition_does_not_withhold_any_existing_base(previous) -> None:
    selected = _select(_change(operation="ADD", previous_edition_id=previous, new_edition_id=None))
    assert _statuses(selected) == {G: "MATCH", A: "MATCH"}
    assert selected.scope_uncertainties


@pytest.mark.parametrize(
    "scope",
    [
        replace(SCOPE, rider_id=RIDER_Y),
        replace(SCOPE, policy_contract_id=UUID(int=801)),
        replace(SCOPE, family_member_id=UUID(int=802)),
        replace(SCOPE, household_space_id=UUID(int=803)),
    ],
)
def test_missing_target_scope_uncertainty_does_not_escape_its_exact_scope(scope) -> None:
    selected = _select(_change(new_edition_id=None), scope=scope)
    assert _statuses(selected) == {G: "MATCH", A: "MATCH"}
    assert not selected.uncertain_relation_ids and not selected.scope_uncertainties


@pytest.mark.parametrize("changes", [{"status": "NO_MATCH"}, {"is_current": False}])
def test_missing_targets_preserve_existing_no_match_and_currentness_rules(changes) -> None:
    selected = _select(_change(previous_edition_id=None, new_edition_id=None, **changes))
    assert _statuses(selected) == {G: "MATCH", A: "MATCH"}
    assert not selected.uncertain_relation_ids and not selected.scope_uncertainties


def test_partial_change_uses_the_same_inclusive_interval_end() -> None:
    relation = _change(new_edition_id=None, effective_through=date(2025, 7, 31))
    assert _statuses(_select(relation, event_date=date(2025, 7, 31)))[A] == "UNKNOWN"
    expired = _select(relation, event_date=AUGUST)
    assert _statuses(expired) == {G: "MATCH", A: "MATCH"}
    assert not expired.scope_uncertainties


def test_scope_uncertainty_is_immutable_and_deduplicates_identical_source_relations() -> None:
    relation = _change(previous_edition_id=None, new_edition_id=None)
    selected = _select(relation, relation)
    assert len(selected.scope_uncertainties) == 1
    with pytest.raises(FrozenInstanceError):
        selected.scope_uncertainties[0].relation_id = UUID(int=999)
    assert str(relation.relation_id) not in repr(selected.scope_uncertainties[0])

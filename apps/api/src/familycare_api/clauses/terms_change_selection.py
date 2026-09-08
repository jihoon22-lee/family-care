"""Compose source-verified terms changes for one exact scope and event date.

The caller owns source validation, currentness, party/rider enrollment and clause
ownership. MATCH here means edition applicability only, never contract activity,
enrollment or executable benefit authority. effective_through is the inclusive
end of the change overlay itself; outside it the independent base still applies.
"""

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

TermsStatus = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
_MAX_EDITIONS = 512
_MAX_RELATIONS = 128
_MAX_REASON_CODES = 64


@dataclass(frozen=True, slots=True, repr=False)
class TermsSelectionScope:
    household_space_id: UUID
    policy_contract_id: UUID
    family_member_id: UUID
    rider_id: UUID | None = None
    clause_id: UUID | None = None


@dataclass(frozen=True, slots=True, repr=False)
class BaseTermsEdition:
    edition_id: UUID
    status: TermsStatus
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class TermsChangeRelation:
    relation_id: UUID
    scope: TermsSelectionScope
    operation: Literal["ADD", "REPLACE"]
    previous_edition_id: UUID | None
    new_edition_id: UUID | None
    effective_from: date | None
    effective_through: date | None
    status: TermsStatus
    reason_codes: tuple[str, ...] = ()
    is_current: bool = True


@dataclass(frozen=True, slots=True, repr=False)
class SelectedTermsEdition:
    edition_id: UUID
    status: TermsStatus
    relation_ids: tuple[UUID, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class TermsScopeUncertainty:
    relation_id: UUID
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class TermsEventSelection:
    scope: TermsSelectionScope
    event_date: date | None
    editions: tuple[SelectedTermsEdition, ...]
    applied_relation_ids: tuple[UUID, ...]
    uncertain_relation_ids: tuple[UUID, ...]
    scope_uncertainties: tuple[TermsScopeUncertainty, ...] = ()


class TermsSelectionError(ValueError):
    def __init__(self) -> None:
        super().__init__("TERMS_SELECTION_INPUT_INVALID")


@dataclass(repr=False)
class _State:
    status: TermsStatus
    reasons: set[str] = field(default_factory=set)
    relations: set[UUID] = field(default_factory=set)


@dataclass(frozen=True, repr=False)
class _Change:
    relation: TermsChangeRelation
    issues: tuple[str, ...]


def _scope_valid(scope: TermsSelectionScope) -> bool:
    return (
        isinstance(scope, TermsSelectionScope)
        and all(
            isinstance(value, UUID)
            for value in (
                scope.household_space_id,
                scope.policy_contract_id,
                scope.family_member_id,
            )
        )
        and all(
            value is None or isinstance(value, UUID) for value in (scope.rider_id, scope.clause_id)
        )
    )


def _covers(relation: TermsSelectionScope, requested: TermsSelectionScope) -> bool:
    return (
        relation.household_space_id == requested.household_space_id
        and relation.policy_contract_id == requested.policy_contract_id
        and relation.family_member_id == requested.family_member_id
        and (relation.rider_id is None or relation.rider_id == requested.rider_id)
        and (relation.clause_id is None or relation.clause_id == requested.clause_id)
    )


def _specificity(scope: TermsSelectionScope) -> int:
    return 2 if scope.clause_id is not None else int(scope.rider_id is not None)


def _reasons_valid(reasons: tuple[str, ...]) -> bool:
    return (
        isinstance(reasons, tuple)
        and len(reasons) <= 32
        and all(
            isinstance(reason, str)
            and 1 <= len(reason) <= 80
            and reason.isascii()
            and all(
                character.isupper() or character.isdigit() or character == "_"
                for character in reason
            )
            for reason in reasons
        )
    )


def _validate_inputs(
    scope: TermsSelectionScope,
    event_date: date | None,
    base: Sequence[BaseTermsEdition],
    relations: Sequence[TermsChangeRelation],
) -> None:
    if (
        not _scope_valid(scope)
        or not isinstance(base, Sequence)
        or not isinstance(relations, Sequence)
        or (event_date is not None and type(event_date) is not date)
        or len(base) > _MAX_EDITIONS
        or len(relations) > _MAX_RELATIONS
    ):
        raise TermsSelectionError
    for edition in base:
        if (
            not isinstance(edition, BaseTermsEdition)
            or not isinstance(edition.edition_id, UUID)
            or edition.status not in {"MATCH", "NO_MATCH", "UNKNOWN"}
            or not _reasons_valid(edition.reason_codes)
        ):
            raise TermsSelectionError
    for relation in relations:
        if (
            not isinstance(relation, TermsChangeRelation)
            or not isinstance(relation.relation_id, UUID)
            or not _scope_valid(relation.scope)
            or (
                relation.new_edition_id is not None
                and not isinstance(relation.new_edition_id, UUID)
            )
            or (
                relation.previous_edition_id is not None
                and not isinstance(relation.previous_edition_id, UUID)
            )
            or relation.status not in {"MATCH", "NO_MATCH", "UNKNOWN"}
            or not _reasons_valid(relation.reason_codes)
            or type(relation.is_current) is not bool
        ):
            raise TermsSelectionError
    if (
        len(
            {reason for edition in base for reason in edition.reason_codes}
            | {reason for relation in relations for reason in relation.reason_codes}
        )
        > _MAX_REASON_CODES
    ):
        raise TermsSelectionError


def _changes(
    scope: TermsSelectionScope,
    event_date: date | None,
    relations: Sequence[TermsChangeRelation],
    states: dict[UUID, _State],
) -> list[_Change]:
    active = []
    by_id: dict[UUID, TermsChangeRelation] = {}
    duplicated: set[UUID] = set()
    for relation in relations:
        if (
            not relation.is_current
            or relation.status == "NO_MATCH"
            or not _covers(relation.scope, scope)
        ):
            continue
        if relation.relation_id in by_id:
            if by_id[relation.relation_id] == relation:
                continue
            duplicated.add(relation.relation_id)
        by_id[relation.relation_id] = relation
        if relation.new_edition_id is not None:
            states.setdefault(relation.new_edition_id, _State("NO_MATCH"))
        start, end = relation.effective_from, relation.effective_through
        invalid_period = (
            (start is not None and type(start) is not date)
            or (end is not None and type(end) is not date)
            or (type(start) is date and type(end) is date and end < start)
        )
        if (
            not invalid_period
            and event_date is not None
            and (
                (start is not None and event_date < start) or (end is not None and event_date > end)
            )
        ):
            continue
        issues = []
        if event_date is None:
            issues.append("EVENT_DATE_UNRESOLVED")
        if invalid_period:
            issues.append("CHANGE_PERIOD_INVALID")
        elif start is None:
            issues.append("CHANGE_START_UNRESOLVED")
        if relation.status == "UNKNOWN":
            issues.append("CHANGE_RELATION_UNRESOLVED")
        if relation.operation not in {"ADD", "REPLACE"}:
            issues.append("CHANGE_OPERATION_INVALID")
        elif (relation.operation == "ADD" and relation.previous_edition_id is not None) or (
            relation.operation == "REPLACE" and relation.previous_edition_id is None
        ):
            issues.append("CHANGE_TARGET_UNRESOLVED")
        if relation.new_edition_id is None:
            issues.append("CHANGE_TARGET_UNRESOLVED")
        elif relation.previous_edition_id == relation.new_edition_id:
            issues.append("CHANGE_CYCLE")
        active.append(_Change(relation, tuple(issues)))
    return [
        _Change(change.relation, (*change.issues, "CHANGE_RELATION_ID_CONFLICT"))
        if change.relation.relation_id in duplicated
        else change
        for change in active
    ]


def _scoped_changes(changes: list[_Change]) -> list[_Change]:
    """A narrow explicit replacement overrides only the same predecessor edition."""
    groups: dict[UUID, list[_Change]] = defaultdict(list)
    retained = []
    for change in changes:
        previous = change.relation.previous_edition_id
        if change.relation.operation == "REPLACE" and previous is not None:
            groups[previous].append(change)
        else:
            retained.append(change)
    for group in groups.values():
        maximum = max(_specificity(change.relation.scope) for change in group)
        specific = [change for change in group if _specificity(change.relation.scope) == maximum]
        # An uncertain narrow relation cannot silently certify a broad alternative.
        retained.extend(group if any(change.issues for change in specific) else specific)
    return sorted(retained, key=lambda change: change.relation.relation_id.int)


def _descendants(seeds: set[UUID], graph: dict[UUID, set[UUID]]) -> set[UUID]:
    reached = set(seeds)
    queue = deque(seeds)
    while queue:
        for target in graph.get(queue.popleft(), set()):
            if target not in reached:
                reached.add(target)
                queue.append(target)
    return reached


def _topology(graph: dict[UUID, set[UUID]]) -> tuple[list[UUID], set[UUID]]:
    indegree: dict[UUID, int] = defaultdict(int)
    for source, targets in graph.items():
        indegree.setdefault(source, 0)
        for target in targets:
            indegree[target] += 1
    queue = deque(
        sorted((node for node in indegree if indegree[node] == 0), key=lambda node: node.int)
    )
    ordered = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for target in sorted(graph.get(node, set()), key=lambda value: value.int):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return ordered, {node for node, degree in indegree.items() if degree}


def select_terms_for_event(
    scope: TermsSelectionScope,
    event_date: date | None,
    base_editions: Sequence[BaseTermsEdition],
    relations: Sequence[TermsChangeRelation],
) -> TermsEventSelection:
    """Apply bounded interval overlays to independently supplied base judgments.

    The result includes encountered edition IDs even when NO_MATCH, so callers
    must consume each tri-state instead of treating presence as applicability.
    General/rider/clause scope is compared by exact identifiers. This function
    cannot infer those identifiers or authenticate their source relationship.
    An unresolved new ID contributes scope uncertainty without inventing an
    edition. REPLACE may withhold its known predecessor; ADD never removes a
    base edition merely because its new target is unresolved.
    """
    _validate_inputs(scope, event_date, base_editions, relations)
    states: dict[UUID, _State] = {}
    for edition in base_editions:
        if edition.edition_id not in states:
            states[edition.edition_id] = _State(edition.status)
        elif states[edition.edition_id].status != edition.status:
            states[edition.edition_id].status = "UNKNOWN"
            states[edition.edition_id].reasons.add("BASE_EDITION_CONFLICT")
        states[edition.edition_id].reasons.update(edition.reason_codes)
    changes = _scoped_changes(_changes(scope, event_date, relations, states))
    graph: dict[UUID, set[UUID]] = defaultdict(set)
    outgoing: dict[UUID, list[_Change]] = defaultdict(list)
    for change in changes:
        relation = change.relation
        previous = relation.previous_edition_id
        if previous is not None and relation.new_edition_id is not None:
            graph[previous].add(relation.new_edition_id)
            outgoing[previous].append(change)
    mentioned = {
        identifier
        for change in changes
        for identifier in (change.relation.previous_edition_id, change.relation.new_edition_id)
        if identifier is not None
    }
    if len(set(states) | mentioned) > _MAX_EDITIONS:
        raise TermsSelectionError
    tainted: set[UUID] = set()
    applied: set[UUID] = set()
    uncertain: set[UUID] = set()
    scope_reasons: dict[UUID, set[str]] = defaultdict(set)

    def mark_unknown(seeds: set[UUID], reasons: set[str], ids: set[UUID]) -> None:
        affected = _descendants(seeds, graph)
        tainted.update(affected)
        for identifier in affected:
            state = states.setdefault(identifier, _State("UNKNOWN"))
            state.status = "UNKNOWN"
            state.reasons.update(reasons)
            state.relations.update(ids)
        uncertain.update(ids)

    for change in changes:
        relation = change.relation
        if change.issues:
            seeds = {relation.new_edition_id} if relation.new_edition_id is not None else set()
            if relation.previous_edition_id is not None and not (
                relation.operation == "ADD" and relation.new_edition_id is None
            ):
                seeds.add(relation.previous_edition_id)
            reasons = set(change.issues) | set(relation.reason_codes)
            mark_unknown(seeds, reasons, {relation.relation_id})
            if relation.new_edition_id is None:
                scope_reasons[relation.relation_id].update(reasons)
    for previous, group in outgoing.items():
        if len({change.relation.new_edition_id for change in group}) > 1:
            mark_unknown(
                {previous},
                {"COMPETING_TERMS_REPLACEMENTS"},
                {change.relation.relation_id for change in group},
            )
    ordered, cyclic = _topology(graph)
    if cyclic:
        ids = {
            change.relation.relation_id
            for change in changes
            if change.relation.previous_edition_id in cyclic
        }
        mark_unknown(cyclic, {"CHANGE_CYCLE"}, ids)
    for first in changes:
        if first.relation.new_edition_id is None:
            continue
        for second in outgoing.get(first.relation.new_edition_id, []):
            start, next_start = first.relation.effective_from, second.relation.effective_from
            if (
                not first.issues
                and not second.issues
                and start is not None
                and next_start is not None
                and next_start < start
            ):
                mark_unknown(
                    {first.relation.new_edition_id},
                    {"CHANGE_SEQUENCE_INVALID"},
                    {first.relation.relation_id, second.relation.relation_id},
                )
    for change in changes:
        relation = change.relation
        if relation.operation != "ADD" or change.issues or relation.new_edition_id is None:
            continue
        target = states[relation.new_edition_id]
        target.relations.add(relation.relation_id)
        target.reasons.update(relation.reason_codes)
        if relation.new_edition_id not in tainted:
            target.status = "MATCH"
            target.reasons.add("TERMS_CHANGE_ADDED")
            applied.add(relation.relation_id)
        else:
            uncertain.add(relation.relation_id)
    for previous in ordered:
        corroborating = [
            change.relation
            for change in outgoing.get(previous, [])
            if not change.issues and change.relation.operation == "REPLACE"
        ]
        if not corroborating:
            continue
        origin = states.setdefault(previous, _State("UNKNOWN"))
        ids = {relation.relation_id for relation in corroborating}
        reasons = {reason for relation in corroborating for reason in relation.reason_codes}
        targets = {
            relation.new_edition_id
            for relation in corroborating
            if relation.new_edition_id is not None
        }
        if previous in tainted or origin.status != "MATCH":
            mark_unknown(
                {previous, *targets},
                {"PREVIOUS_EDITION_UNRESOLVED", *reasons, *origin.reasons},
                ids | origin.relations,
            )
            continue
        # Equivalent source relations corroborate one transition. Never consume
        # the same predecessor twice, which would invent a second failed change.
        target_id = next(iter(targets))
        target = states[target_id]
        provenance = origin.relations | ids
        origin.status = "NO_MATCH"
        origin.reasons.update((*reasons, "TERMS_CHANGE_REPLACED"))
        origin.relations.update(ids)
        target.relations.update(provenance)
        target.reasons.update(reasons)
        if target_id not in tainted:
            target.status = "MATCH"
            target.reasons.add("TERMS_CHANGE_REPLACEMENT_APPLIES")
            applied.update(ids)
        else:
            uncertain.update(ids)
    return TermsEventSelection(
        scope,
        event_date,
        tuple(
            SelectedTermsEdition(
                identifier,
                state.status,
                tuple(sorted(state.relations, key=lambda value: value.int)),
                tuple(sorted(state.reasons)),
            )
            for identifier, state in sorted(states.items(), key=lambda item: item[0].int)
        ),
        tuple(sorted(applied - uncertain, key=lambda value: value.int)),
        tuple(sorted(uncertain, key=lambda value: value.int)),
        tuple(
            TermsScopeUncertainty(identifier, tuple(sorted(reasons)))
            for identifier, reasons in sorted(scope_reasons.items(), key=lambda item: item[0].int)
        ),
    )

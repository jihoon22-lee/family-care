"""Prove disjoint source classification cases within one scalar event assignment.

This relation grants neither additive payout authority nor exhaustiveness of the
listed cases. Activity names do not partition real care: admission and surgery
may coexist. Clinical comparisons require the same field, code system and edition.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Literal, cast
from uuid import UUID

from familycare_api.clauses.dsl import CompiledExpression, validate_rule_document
from familycare_api.guidance.calculation_source import _validated as _validated_calculation
from familycare_api.guidance.domain import GuidanceCoverageInput, GuidanceRuleInput
from familycare_api.guidance.event_facts import CodeScope
from familycare_api.guidance.models import GuidanceEvidence, GuidanceSemanticEvidence
from familycare_api.guidance.relevance import _bounded_document

MAX_SOURCE_CASES = 32
MAX_SOURCE_CASE_RULES = 256
MAX_SOURCE_CASE_NODES = 4096
MAX_SOURCE_CASE_VALUES = 256
_FIELDS = frozenset(
    {
        "MedicalEvent.classification",
        "MedicalEvent.diagnosis_code",
        "MedicalEvent.procedure_code",
        "MedicalEvent.pathology_code",
        "MedicalEvent.anatomical_site_code",
    }
)
type _Identity = tuple[str, str | None, str | None]
type _Constraints = dict[_Identity, frozenset[str]]


class _Unresolved(ValueError):
    pass


@dataclass(slots=True)
class _Budget:
    rules: int = MAX_SOURCE_CASE_RULES
    nodes: int = MAX_SOURCE_CASE_NODES

    def take(self, *, rule: bool = False) -> None:
        if rule:
            self.rules -= 1
        else:
            self.nodes -= 1
        if self.rules < 0 or self.nodes < 0:
            raise _Unresolved


def _rule_expression(
    rule: GuidanceRuleInput, case: GuidanceCoverageInput
) -> tuple[CompiledExpression, tuple[CodeScope, ...]]:
    if (
        not isinstance(rule, GuidanceRuleInput)
        or not isinstance(rule.publication_id, UUID)
        or rule.publication_id.int == 0
        or type(rule.rule_key) is not str
        or not 1 <= len(rule.rule_key) <= 256
        or not isinstance(rule.citations, tuple)
        or not 1 <= len(rule.citations) <= 64
        or case.calculation is None
        or rule.source_kind != case.calculation.source_kind
    ):
        raise _Unresolved
    keys = []
    for citation in rule.citations:
        if citation.lineage_valid is not True or type(citation.citation_key) is not str:
            raise _Unresolved
        evidence = citation.evidence
        if (
            not isinstance(evidence, GuidanceEvidence | GuidanceSemanticEvidence)
            or evidence.publication_id != rule.publication_id
            or evidence.source_sha256 is None
        ):
            raise _Unresolved
        type(evidence).model_validate(evidence.model_dump(warnings=False), strict=True)
        identifiers = (
            (
                evidence.citation_id,
                evidence.document_version_id,
                evidence.terms_edition_id,
                evidence.generation_id,
            )
            if isinstance(evidence, GuidanceSemanticEvidence)
            else (evidence.evidence_id,)
        )
        if any(identifier.int == 0 for identifier in identifiers):
            raise _Unresolved
        if rule.source_kind == "SEMANTIC_NODE":
            calculation = case.calculation
            if (
                not isinstance(evidence, GuidanceSemanticEvidence)
                or calculation is None
                or calculation.source_kind != "SEMANTIC_NODE"
                or calculation.publication_id != rule.publication_id
                or calculation.semantic_node_id != rule.semantic_node_id
                or evidence.root_node_id != rule.semantic_node_id
                or citation.citation_key != str(evidence.citation_id)
                or any(
                    not isinstance(c.evidence, GuidanceSemanticEvidence)
                    or c.evidence.manifest_sha256 != evidence.manifest_sha256
                    for c in calculation.citations
                )
            ):
                raise _Unresolved
        elif rule.source_kind == "PRIVATE_RULE_PUBLICATION":
            if evidence.kind != "TERMS_SECTION" or rule.semantic_node_id is not None:
                raise _Unresolved
        elif rule.source_kind == "OPERATIONAL_RULE_VERSION":
            if (
                not isinstance(evidence, GuidanceEvidence)
                or evidence.kind != "OPERATIONAL_EVIDENCE"
                or citation.citation_key != str(evidence.evidence_id)
            ):
                raise _Unresolved
        else:
            raise _Unresolved
        keys.append(citation.citation_key)
    if len(keys) != len(set(keys)):
        raise _Unresolved
    _bounded_document(rule.rule_document)
    validated = validate_rule_document(rule.rule_document, keys)
    if (
        validated.expression is None
        or validated.required is not rule.required
        or validated.rule_kind != rule.rule_kind
        or validated.result_reason_code != rule.result_reason_code
    ):
        raise _Unresolved
    if not isinstance(rule.classification_scopes, tuple) or len(rule.classification_scopes) > 32:
        raise _Unresolved
    scopes = tuple(
        CodeScope(s["field"], s["code_system"], s["code_version"])
        for s in rule.classification_scopes
    )
    return validated.expression, scopes


def _intersect(parts: tuple[_Constraints, ...]) -> _Constraints:
    result: _Constraints = {}
    for part in parts:
        for key, values in part.items():
            result[key] = result[key] & values if key in result else values
            if not result[key]:
                # A contradictory source is not evidence for an alternative payout.
                raise _Unresolved
    return result


def _constraints(
    node: CompiledExpression, scopes: tuple[CodeScope, ...], budget: _Budget
) -> _Constraints:
    budget.take()
    if node.operator in {"all", "any"}:
        parts = tuple(
            _constraints(cast(CompiledExpression, child), scopes, budget) for child in node.operands
        )
        if node.operator == "all":
            return _intersect(parts)
        common = set.intersection(*(set(part) for part in parts))
        result = {key: frozenset().union(*(part[key] for part in parts)) for key in common}
        if any(len(values) > MAX_SOURCE_CASE_VALUES for values in result.values()):
            raise _Unresolved
        return result
    if node.operator not in {"equals", "in"}:
        return {}
    field = cast(str, node.operands[0])
    if field not in _FIELDS:
        return {}
    identities = {scope for scope in scopes if scope.field_path == field}
    if len(identities) == 1:
        identity = next(iter(identities))
        key: _Identity = (field, identity.code_system, identity.code_version)
    elif not identities and field == "MedicalEvent.classification":
        key = (field, None, None)
    else:
        return {}
    raw = (
        (node.operands[1],)
        if node.operator == "equals"
        else cast(tuple[object, ...], node.operands[1])
    )
    if any(type(value) is not str or not 1 <= len(value) <= 160 for value in raw):
        return {}
    return {key: frozenset(cast(tuple[str, ...], raw))}


def _case_constraints(case: GuidanceCoverageInput, budget: _Budget) -> _Constraints:
    if (
        not isinstance(case, GuidanceCoverageInput)
        or case.knowledge_incomplete
        or case.calculation is None
        or not isinstance(case.rules, tuple)
        or not 1 <= len(case.rules) <= 128
    ):
        raise _Unresolved
    _validated_calculation(case.calculation)
    mandatory = []
    seen: set[tuple[UUID, str]] = set()
    for rule in case.rules:
        budget.take(rule=True)
        expression, scopes = _rule_expression(rule, case)
        identity = (rule.publication_id, rule.rule_key)
        if identity in seen:
            raise _Unresolved
        seen.add(identity)
        if rule.required is True and rule.rule_kind != "exclusion":
            mandatory.append(_constraints(expression, scopes, budget))
    result = _intersect(tuple(mandatory))
    for field in {key[0] for key in result}:
        if sum(key[0] == field for key in result) > 1:
            raise _Unresolved
    if not result:
        raise _Unresolved
    return result


def source_case_relation(
    cases: tuple[GuidanceCoverageInput, ...],
) -> Literal["MUTUALLY_EXCLUSIVE", "UNRESOLVED"]:
    """Every pair needs a disjoint mandatory finite set on the same scoped field.

    The result concerns the current scalar fact model. It does not establish that
    a real event has only one diagnosis, that these cases cover every possibility,
    or that their individual amounts can be added or form a guaranteed range.
    """
    if not isinstance(cases, tuple) or not 2 <= len(cases) <= MAX_SOURCE_CASES:
        return "UNRESOLVED"
    try:
        budget = _Budget()
        constraints = tuple(_case_constraints(case, budget) for case in cases)
        return (
            "MUTUALLY_EXCLUSIVE"
            if all(
                any(left[key].isdisjoint(right[key]) for key in left.keys() & right.keys())
                for left, right in combinations(constraints, 2)
            )
            else "UNRESOLVED"
        )
    except ValueError, TypeError, KeyError, AttributeError, ArithmeticError:
        return "UNRESOLVED"

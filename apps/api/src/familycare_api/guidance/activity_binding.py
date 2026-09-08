"""Bind a generic performed observation only to an explicit source activity."""

from typing import cast

from familycare_api.clauses.dsl import CompiledExpression, validate_rule_document
from familycare_api.guidance.domain import GuidanceCoverageInput
from familycare_api.guidance.interpretation import Activity

_ACTIVITIES = frozenset({"admission", "outpatient", "surgery"})


def _activities(node: CompiledExpression) -> set[Activity]:
    if node.operator in {"all", "any"}:
        return set().union(
            *(
                _activities(child)
                for child in node.operands
                if isinstance(child, CompiledExpression)
            )
        )
    if node.operator not in {"equals", "in"} or node.operands[0] != "MedicalEvent.treatment_kind":
        return set()
    values = (node.operands[1],) if node.operator == "equals" else node.operands[1]
    return {
        cast(Activity, value) for value in cast(tuple[object, ...], values) if value in _ACTIVITIES
    }


def source_activity(coverage: GuidanceCoverageInput) -> Activity | None:
    activities: set[Activity] = set()
    for rule in coverage.rules:
        if rule.rule_kind not in {"eligibility", "classification", "indemnity_eligibility"}:
            continue
        if not rule.citations or any(not c.lineage_valid for c in rule.citations):
            continue
        try:
            validated = validate_rule_document(
                rule.rule_document, tuple(c.citation_key for c in rule.citations)
            )
            if (
                validated.expression is None
                or validated.rule_kind != rule.rule_kind
                or validated.required != rule.required
                or validated.result_reason_code != rule.result_reason_code
            ):
                continue
        except ValueError:
            continue
        activities.update(_activities(validated.expression))
    return next(iter(activities)) if len(activities) == 1 else None

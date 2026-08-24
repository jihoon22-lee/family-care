"""Bridge stored executable CoverageRule versions to the pure operators."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from familycare_api.clauses.dsl import (
    CompiledExpression,
    EvidenceIndex,
    RuleValidationError,
    ValidatedRule,
    validate_rule_document,
)
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.decisions.domain import FactContext, RuleEvaluation, TriState
from familycare_api.decisions.operators import OperatorEvaluationError, evaluate_expression


class RuleRuntimeError(ValueError):
    """Raised only when a caller requests a structurally invalid rule runtime."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


_APPROVED_STATES = frozenset({"AI_VERIFIED", "USER_CONFIRMED"})


def compile_rule_expression(rule: CoverageRuleVersion) -> CompiledExpression:
    """Revalidate one stored rule and return its data-only expression."""

    if not rule.executable or rule.review_state not in _APPROVED_STATES:
        raise RuleRuntimeError("RULE_NOT_EXECUTABLE")
    evidence_ids = frozenset(item.evidence_id for item in rule.evidence)
    try:
        validated = validate_rule_document(rule.rule_document, EvidenceIndex(evidence_ids))
    except RuleValidationError as error:
        raise RuleRuntimeError(f"UNSUPPORTED_DSL:{error.reason_code}") from None
    if validated.expression is None:
        raise RuleRuntimeError("RULE_EXPRESSION_MISSING")
    if frozenset(str(item) for item in validated.evidence_ids) != frozenset(
        str(item) for item in evidence_ids
    ):
        raise RuleRuntimeError("RULE_EVIDENCE_MISMATCH")
    _ensure_rule_metadata(rule, validated)
    return validated.expression


def evaluate_rule(
    rule: CoverageRuleVersion,
    context: FactContext,
    *,
    rider_id: UUID | None = None,
) -> RuleEvaluation:
    """Evaluate one executable rule, preserving UNKNOWN boundary failures."""

    resolved_rider_id = _resolve_rider_id(rule, rider_id)
    evidence = rule.evidence
    evidence_ids = tuple(item.evidence_id for item in evidence)
    if not rule.executable or rule.review_state not in _APPROVED_STATES:
        return _evaluation(
            rule,
            context,
            rider_id=resolved_rider_id,
            result="UNKNOWN",
            reason_code="RULE_NOT_EXECUTABLE",
            evidence_ids=evidence_ids,
            evidence=evidence,
        )
    if any(
        item.review_state not in _APPROVED_STATES
        or bool(getattr(item, "evidence_stale", False))
        or bool(getattr(item, "stale", False))
        for item in evidence
    ):
        return _evaluation(
            rule,
            context,
            rider_id=resolved_rider_id,
            result="UNKNOWN",
            reason_code="STALE_OR_UNCONFIRMED_EVIDENCE",
            evidence_ids=evidence_ids,
            evidence=evidence,
        )
    try:
        expression = compile_rule_expression(rule)
    except RuleRuntimeError:
        return _evaluation(
            rule,
            context,
            rider_id=resolved_rider_id,
            result="UNKNOWN",
            reason_code="UNSUPPORTED_DSL",
            evidence_ids=evidence_ids,
            evidence=evidence,
        )
    try:
        outcome = evaluate_expression(expression, context)
    except OperatorEvaluationError as error:
        return _evaluation(
            rule,
            context,
            rider_id=resolved_rider_id,
            result="UNKNOWN",
            reason_code=error.reason_code,
            evidence_ids=evidence_ids,
            evidence=evidence,
        )
    return _evaluation(
        rule,
        context,
        rider_id=resolved_rider_id,
        result=outcome.result,
        reason_code=(rule.result_reason_code if outcome.result == "MATCH" else outcome.reason_code),
        fact_paths=expression.referenced_fields,
        missing_fields=outcome.missing_fields,
        conflicting_fields=outcome.conflicting_fields,
        evidence_ids=evidence_ids,
        evidence=evidence,
    )


def _evaluation(
    rule: CoverageRuleVersion,
    context: FactContext,
    *,
    rider_id: UUID,
    result: str,
    reason_code: str,
    fact_paths: tuple[str, ...] = (),
    missing_fields: tuple[str, ...] = (),
    conflicting_fields: tuple[str, ...] = (),
    evidence_ids: tuple[UUID, ...] = (),
    evidence: tuple[EvidenceRef, ...] = (),
) -> RuleEvaluation:
    normalized_facts = {
        field: value for field in fact_paths if (value := context.get(field)) is not None
    }
    return RuleEvaluation(
        rider_id=rider_id,
        rule_version_id=rule.id,
        result=cast(TriState, result),
        required=rule.required,
        reason_code=reason_code,
        facts=normalized_facts,
        fact_paths=fact_paths,
        missing_fields=missing_fields,
        conflicting_fields=conflicting_fields,
        evidence_ids=evidence_ids,
        evidence=evidence,
    )


def _ensure_rule_metadata(rule: CoverageRuleVersion, validated: ValidatedRule) -> None:
    if (
        validated.schema_version != rule.schema_version
        or validated.rule_kind != rule.rule_kind
        or validated.required is not rule.required
        or validated.input_field_paths != rule.input_field_paths
        or validated.result_reason_code != rule.result_reason_code
    ):
        raise RuleRuntimeError("RULE_METADATA_MISMATCH")


def _resolve_rider_id(rule: CoverageRuleVersion, rider_id: UUID | None) -> UUID:
    """Resolve the actual Rider ID supplied by the scoped engine.

    CoverageRuleVersion intentionally stores only its CoverageRule ID.  The
    repository/engine supplies the actual rider association when evaluating.
    Falling back to the CoverageRule ID would silently corrupt the evaluation
    lineage, so an isolated caller must provide the keyword explicitly.
    """

    resolved = rider_id or getattr(rule, "rider_id", None)
    if not isinstance(resolved, UUID) or resolved.int == 0:
        raise RuleRuntimeError("RIDER_ID_REQUIRED")
    return resolved


__all__ = [
    "RuleRuntimeError",
    "compile_rule_expression",
    "evaluate_rule",
]

"""Evaluate source-preserving rule inputs through the existing data-only DSL."""

from dataclasses import replace

from familycare_api.clauses.dsl import RuleValidationError, validate_rule_document
from familycare_api.decisions.knowledge_domain import KnowledgeFactContext
from familycare_api.decisions.operators import OperatorEvaluationError, evaluate_expression
from familycare_api.guidance.domain import (
    GuidanceCoverageInput,
    GuidanceRuleEvaluation,
    GuidanceRuleInput,
)
from familycare_api.guidance.event_facts import CodeScope, EventFactRead, scope_code_facts
from familycare_api.guidance.runtime_facts import runtime_fact_context


class GuidanceRuleRuntime:
    def _evaluate_rule(
        self,
        facts: KnowledgeFactContext,
        coverage: GuidanceCoverageInput,
        rule: GuidanceRuleInput,
        *,
        event_read: EventFactRead | None = None,
    ) -> tuple[GuidanceRuleEvaluation, bool]:
        keys = tuple(item.citation_key for item in rule.citations)
        if (
            not keys
            or len(keys) != len(set(keys))
            or any(not item.lineage_valid for item in rule.citations)
        ):
            return self._unknown(rule, "CITATION_INVALID"), False
        try:
            validated = validate_rule_document(rule.rule_document, keys)
            if (
                validated.expression is None
                or validated.rule_kind != rule.rule_kind
                or validated.required is not rule.required
                or validated.result_reason_code != rule.result_reason_code
            ):
                raise RuleValidationError("RULE_METADATA_MISMATCH")
            if rule.terms_applicability != "MATCH":
                return self._unknown(rule, "TERMS_APPLICABILITY_UNRESOLVED"), False
            if rule.classification_scopes or set(validated.referenced_fields) & {
                "MedicalEvent.diagnosis_code",
                "MedicalEvent.procedure_code",
                "MedicalEvent.pathology_code",
                "MedicalEvent.anatomical_site_code",
            }:
                if event_read is None:
                    return self._unknown(rule, "EVENT_CODE_SCOPE_UNVERIFIED"), False
                facts = scope_code_facts(
                    replace(event_read, context=facts),
                    tuple(
                        CodeScope(s["field"], s["code_system"], s["code_version"])
                        for s in rule.classification_scopes
                    ),
                ).context
            outcome = evaluate_expression(
                validated.expression, runtime_fact_context(facts, coverage)
            )
        except RuleValidationError, OperatorEvaluationError:
            return self._unknown(rule, "UNSUPPORTED_DSL"), True
        result = outcome.result
        reason = rule.result_reason_code if result == "MATCH" else outcome.reason_code
        if rule.rule_kind == "exclusion":
            if result == "MATCH":
                result, reason = "NO_MATCH", rule.result_reason_code
            elif result == "NO_MATCH":
                result, reason = "MATCH", "EXCLUSION_NOT_ESTABLISHED"
        return GuidanceRuleEvaluation(
            source=rule,
            result=result,
            required=rule.required,
            reason_code=reason,
            fact_paths=validated.referenced_fields,
            missing_fields=outcome.missing_fields,
            conflicting_fields=outcome.conflicting_fields,
            citations=rule.citations,
        ), False

    @staticmethod
    def _unknown(rule: GuidanceRuleInput, reason: str) -> GuidanceRuleEvaluation:
        return GuidanceRuleEvaluation(
            source=rule,
            result="UNKNOWN",
            required=rule.required,
            reason_code=reason,
            citations=rule.citations,
        )

"""Pure document-based guidance; no provider calls or confirmed-status mutation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Literal
from uuid import UUID

from familycare_api.clauses.dsl import (
    CompiledCalculation,
    RuleValidationError,
    validate_rule_document,
)
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeDecisionContext,
    KnowledgeFactContext,
)
from familycare_api.decisions.knowledge_engine import (
    _CalculationInputUnavailable,
    _CalculationState,
    _legacy_fact_context,
)
from familycare_api.guidance.domain import (
    GuidanceCitation,
    GuidanceContext,
    GuidanceCoverageInput,
    GuidanceRuleEvaluation,
)
from familycare_api.guidance.event_facts import EventFactRead, build_event_facts
from familycare_api.guidance.models import (
    Freshness,
    GuidanceCandidate,
    GuidanceCondition,
    GuidanceEstimate,
    GuidanceEvidenceRef,
    GuidanceQuestion,
    GuidanceSupport,
    LocalGuidanceResponse,
)
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.rule_runtime import GuidanceRuleRuntime

_RELEVANCE_KINDS = frozenset({"eligibility", "classification", "indemnity_eligibility"})
_FIELD_LABELS = {
    "Rider.insured_amount": "가입금액",
    "MedicalEvent.admission_days": "입원 일수",
    "Receipt.confirmed_amount": "확인된 비용",
    "Receipt.covered_amount": "보장대상 비용",
    "ClaimHistory.counted_occurrence": "이전 지급 횟수",
}


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _money(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _formula(node: CompiledCalculation) -> str:
    values = [
        _formula(item)
        if isinstance(item, CompiledCalculation)
        else _FIELD_LABELS.get(item, item)
        if isinstance(item, str)
        else _money(item)
        if isinstance(item, Decimal)
        else "?"
        for item in node.operands
    ]
    symbols = {"add": " + ", "subtract": " - ", "multiply": " × "}
    if node.operator in symbols:
        return "(" + symbols[node.operator].join(values) + ")"
    if node.operator == "round":
        return f"반올림[{node.rounding}]({', '.join(values)})"
    return f"{'최솟값' if node.operator == 'min' else '최댓값'}({', '.join(values)})"


def _evidence(
    citations: tuple[GuidanceCitation, ...],
    publication_id: UUID,
) -> tuple[GuidanceEvidenceRef, ...]:
    del publication_id
    return tuple(item.evidence for item in citations if item.lineage_valid)


def _event_status(event: MedicalEvent, coverage: GuidanceCoverageInput) -> Freshness | None:
    if event.event_date is not None and (
        (coverage.contract_start is not None and event.event_date < coverage.contract_start)
        or (coverage.contract_end is not None and event.event_date > coverage.contract_end)
    ):
        return None
    intervals = tuple(
        interval
        for interval in coverage.status_intervals
        if event.event_date is not None
        and interval.effective_from <= event.event_date <= interval.effective_through
    )
    if len(intervals) == 1:
        interval = intervals[0]
        if interval.decision == "MATCH":
            return "CONFIRMED_AT_EVENT" if interval.confirmed_status == "active" else None
        return "STATUS_UNRESOLVED"
    if intervals or coverage.current_confirmation_decision == "NO_MATCH":
        return "STATUS_UNRESOLVED"
    if coverage.current_confirmed_status in {"inactive", "lapsed", "terminated"}:
        return "STATUS_UNRESOLVED"
    return "DOCUMENT_CONTINUITY"


class LocalGuidanceEngine:
    """Separate relevance, status assumptions and calculation readiness."""

    def __init__(self) -> None:
        # Reuse the existing bounded DSL evaluator and Decimal calculation runtime.
        # Its aggregate legacy policy and insured-amount fallback are not used here.
        self.rule_runtime = GuidanceRuleRuntime()

    def evaluate(
        self,
        scope: HouseholdScope,
        event: MedicalEvent,
        context: GuidanceContext | KnowledgeDecisionContext,
    ) -> LocalGuidanceResponse:
        if isinstance(context, KnowledgeDecisionContext):
            context = adapt_private_guidance(context)
        if (
            event.household_space_id != scope.household_space_id
            or context.household_space_id != scope.household_space_id
            or context.family_member_id != event.family_member_id
        ):
            raise ValueError("guidance scope mismatch")
        event_read = build_event_facts(
            event,
            context.normalizers,
            selected_subject_terms=context.selected_subject_terms,
            other_subject_terms=context.other_subject_terms,
        )
        normalized = event_read.context
        facts = KnowledgeFactContext(
            facts={**context.supporting_facts, **normalized.facts},
            audit_conflicts=normalized.audit_conflicts,
        )
        candidates: list[GuidanceCandidate] = []
        unsupported = 0
        failures: list[str] = []
        for coverage in sorted(
            context.coverages, key=lambda item: (item.ref.kind, str(item.ref.coverage_id))
        ):
            coverage_facts = facts
            if (
                context.receipt_currency is None
                or context.receipt_currency != coverage.currency
                or (
                    coverage.canonical_identity is not None
                    and "currency" in coverage.canonical_identity.field_conflicts
                )
            ):
                coverage_facts = KnowledgeFactContext(
                    facts={
                        path: value
                        for path, value in facts.facts.items()
                        if path not in {"Receipt.covered_amount", "Receipt.confirmed_amount"}
                    },
                    audit_conflicts=facts.audit_conflicts,
                )
            try:
                candidate, supported = self._coverage(
                    event, coverage_facts, coverage, event_read=event_read
                )
            except ValueError, ArithmeticError:
                candidate, supported = None, False
                failures.append("GUIDANCE_COVERAGE_FAILED")
            if candidate is not None:
                candidates.append(candidate)
            unsupported += not supported
        outcome: Literal[
            "CANDIDATES", "NO_RELEVANT_COVERAGE", "INPUT_UNRESOLVED", "KNOWLEDGE_PENDING"
        ] = "NO_RELEVANT_COVERAGE"
        if candidates:
            outcome = "CANDIDATES"
        elif not context.coverages or unsupported:
            outcome = "KNOWLEDGE_PENDING"
        elif not any(
            item.value is not None
            and path not in {"MedicalEvent.event_date", "MedicalEvent.visit_date"}
            for path, item in normalized.facts.items()
        ):
            outcome = "INPUT_UNRESOLVED"
        return LocalGuidanceResponse(
            schema_version="2",
            family_member_id=event.family_member_id,
            medical_event_id=event.id,
            event_version=event.version,
            event_date=event.event_date,
            outcome=outcome,
            versions=context.versions.model_copy(update={"engine": "local-guidance-v2"}),
            candidates=tuple(candidates),
            support=GuidanceSupport(
                total_coverages=len(context.coverages),
                evaluated_coverages=len(context.coverages) - unsupported,
                unsupported_coverages=unsupported,
                failure_codes=_unique(failures),
            ),
        )

    def _coverage(
        self,
        event: MedicalEvent,
        facts: KnowledgeFactContext,
        coverage: GuidanceCoverageInput,
        *,
        event_read: EventFactRead | None = None,
    ) -> tuple[GuidanceCandidate | None, bool]:
        if (
            coverage.disposition == "NOT_APPLICABLE"
            or "NO_MATCH"
            in (
                coverage.enrollment_decision,
                coverage.subject_binding_decision,
                coverage.mapping_enrollment_decision,
                coverage.document_identity_decision,
                coverage.edition_applicability_decision,
                coverage.section_mapping_decision,
                coverage.overall_mapping_decision,
            )
            or coverage.mapping_applicability == "NOT_APPLICABLE"
        ):
            return None, True
        if (
            coverage.enrollment_decision != "MATCH"
            or coverage.subject_binding_decision != "MATCH"
            or coverage.component_classification != "BENEFIT_COVERAGE"
            or coverage.disposition == "BLOCKED"
            or not coverage.rules
        ):
            return None, False
        freshness = _event_status(event, coverage)
        if freshness is None:
            return None, True
        # This copy is an event-time evaluator input only. The original current
        # status and its stored confirmation are never changed or upgraded.
        field_conflicts = (
            coverage.canonical_identity.field_conflicts if coverage.canonical_identity else ()
        )
        effective_coverage = replace(
            coverage,
            insured_amount=None if "insured_amount" in field_conflicts else coverage.insured_amount,
            currency=None if "currency" in field_conflicts else coverage.currency,
            current_confirmed_status="active" if freshness == "CONFIRMED_AT_EVENT" else None,
            current_confirmation_decision="MATCH" if freshness == "CONFIRMED_AT_EVENT" else None,
        )
        outcomes = tuple(
            self.rule_runtime._evaluate_rule(facts, effective_coverage, rule, event_read=event_read)
            for rule in coverage.rules
        )
        evaluations = tuple(item[0] for item in outcomes)
        relevant = any(
            rule.rule_kind in _RELEVANCE_KINDS and evaluation.result == "MATCH"
            for rule, evaluation in zip(coverage.rules, evaluations, strict=True)
        )
        if not relevant:
            return None, not any(failed for _, failed in outcomes)
        required = tuple(item for item in evaluations if item.required)
        if any(item.result == "NO_MATCH" for item in required):
            return None, True
        conditions: Literal["MATCH", "UNKNOWN"] = (
            "UNKNOWN"
            if coverage.knowledge_incomplete or any(item.result == "UNKNOWN" for item in required)
            else "MATCH"
        )
        assumptions = []
        if freshness == "DOCUMENT_CONTINUITY":
            assumptions.append("DOCUMENT_CONTINUITY_ASSUMED")
        elif freshness == "STATUS_UNRESOLVED":
            assumptions.append("EVENT_STATUS_UNRESOLVED")
        if event.event_date is None:
            assumptions.append("EVENT_DATE_REQUIRED")
        group: Literal["PRIMARY", "CONDITIONAL"] = (
            "CONDITIONAL"
            if conditions == "UNKNOWN"
            or freshness == "STATUS_UNRESOLVED"
            or event.event_date is None
            else "PRIMARY"
        )
        estimate = self._estimate(
            facts, effective_coverage, conditions=conditions, assumptions=assumptions
        )
        question_paths = _unique(
            [
                path
                for item in evaluations
                for path in (*item.missing_fields, *item.conflicting_fields)
            ]
            + list(estimate.missing_inputs)
        )
        return GuidanceCandidate(
            ref=coverage.canonical_identity.ref
            if coverage.canonical_identity is not None
            else coverage.ref,
            canonical_identity=coverage.canonical_identity,
            contract_label=coverage.contract_label,
            coverage_label=coverage.coverage_label,
            benefit_kind=coverage.benefit_type,
            group=group,
            freshness=freshness,
            condition_result=conditions,
            reason_codes=_unique(
                ["DOCUMENTED_RELEVANT_COVERAGE"]
                + (["OPERATIONAL_SOURCE_FIELD_CONFLICT"] if field_conflicts else [])
                + (["SEMANTIC_KNOWLEDGE_PARTIAL"] if coverage.knowledge_incomplete else [])
                + [item.reason_code for item in required if item.result != "MATCH"]
            ),
            assumptions=tuple(assumptions),
            conditions=tuple(self._condition(item) for item in evaluations),
            estimate=estimate,
            questions=tuple(
                GuidanceQuestion(field_path=path, reason_code="EVENT_FACT_NEEDED")
                for path in question_paths
                if path.startswith(("MedicalEvent.", "Receipt.", "ClaimHistory."))
            ),
        ), not coverage.knowledge_incomplete and not any(failed for _, failed in outcomes)

    @staticmethod
    def _condition(value: GuidanceRuleEvaluation) -> GuidanceCondition:
        semantic = value.source.source_kind == "SEMANTIC_NODE"
        return GuidanceCondition(
            rule_id=None if semantic else value.rule_publication_id,
            semantic_publication_id=value.rule_publication_id if semantic else None,
            semantic_node_id=value.source.semantic_node_id if semantic else None,
            result=value.result,
            reason_code=value.reason_code,
            evidence=_evidence(value.citations, value.rule_publication_id),
        )

    @staticmethod
    def _estimate(
        facts: KnowledgeFactContext,
        coverage: GuidanceCoverageInput,
        *,
        conditions: Literal["MATCH", "UNKNOWN"],
        assumptions: list[str],
    ) -> GuidanceEstimate:
        publication = coverage.calculation
        if publication is None:
            return GuidanceEstimate(kind="UNAVAILABLE", reason_code="CALCULATION_NOT_PUBLISHED")
        if not publication.citations or any(
            not item.lineage_valid for item in publication.citations
        ):
            return GuidanceEstimate(kind="UNAVAILABLE", reason_code="CALCULATION_CITATION_INVALID")
        try:
            validated = validate_rule_document(
                publication.calculation_document,
                tuple(item.citation_key for item in publication.citations),
            )
            calculation = validated.calculation
            if (
                calculation is None
                or publication.calculation_kind != coverage.benefit_type
                or validated.result_reason_code != publication.result_reason_code
            ):
                raise ValueError("calculation metadata mismatch")
            formula = _formula(calculation)
        except RuleValidationError, ValueError:
            return GuidanceEstimate(kind="UNAVAILABLE", reason_code="CALCULATION_UNSUPPORTED")
        legacy = _legacy_fact_context(facts, coverage)
        missing = tuple(
            path
            for path in validated.input_field_paths
            if (fact := legacy.get(path)) is None
            or fact.value is None
            or not fact.is_confirmed
            or fact.evidence_stale
            or (
                path == "Rider.insured_amount"
                and (
                    coverage.certificate_amount_decision != "MATCH"
                    or coverage.certificate_amount_evidence_state != "DIRECT"
                )
            )
        )
        estimate_assumptions = _unique(
            [*assumptions, *(["CONDITIONS_REMAIN"] if conditions == "UNKNOWN" else [])]
        )
        evidence = _evidence(publication.citations, publication.publication_id)
        if (
            publication.source_currency is not None
            and publication.source_currency != coverage.currency
        ):
            return GuidanceEstimate(
                kind="FORMULA",
                currency=publication.source_currency,
                formula=formula,
                missing_inputs=("Rider.currency",),
                assumptions=estimate_assumptions,
                reason_code="CALCULATION_CURRENCY_MISMATCH",
                evidence=evidence,
            )
        if missing or coverage.currency is None:
            return GuidanceEstimate(
                kind="FORMULA",
                currency=coverage.currency,
                formula=formula,
                missing_inputs=missing,
                assumptions=estimate_assumptions,
                reason_code="CALCULATION_INPUT_NEEDED",
                evidence=evidence,
            )
        try:
            amount = _CalculationState(legacy, coverage.currency).evaluate(calculation)
            if amount < 0 or not amount.is_finite():
                raise ValueError("invalid calculated amount")
        except _CalculationInputUnavailable, ArithmeticError, ValueError:
            return GuidanceEstimate(
                kind="FORMULA",
                currency=coverage.currency,
                formula=formula,
                assumptions=estimate_assumptions,
                reason_code="CALCULATION_INPUT_NEEDED",
                evidence=evidence,
            )
        return GuidanceEstimate(
            kind="POINT",
            currency=coverage.currency,
            amount=_money(amount),
            formula=formula,
            assumptions=estimate_assumptions,
            reason_code="DOCUMENT_BASED_ESTIMATE",
            evidence=evidence,
        )

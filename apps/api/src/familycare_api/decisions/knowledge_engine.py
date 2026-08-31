"""Deterministic private-knowledge eligibility and benefit evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
)
from typing import cast
from uuid import UUID, uuid4

from familycare_api.clauses.dsl import (
    CompiledCalculation,
    RuleValidationError,
    validate_rule_document,
)
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import (
    FactConfirmation,
    FactContext,
    FactValue,
    MedicalEvent,
    TriState,
)
from familycare_api.decisions.knowledge_domain import (
    KnowledgeAnalysisCompleteness,
    KnowledgeBenefitCalculation,
    KnowledgeCalculationPublication,
    KnowledgeCalculationStatus,
    KnowledgeCalculationStep,
    KnowledgeClaimCandidate,
    KnowledgeCoverageContext,
    KnowledgeDecisionContext,
    KnowledgeDecisionResult,
    KnowledgeFact,
    KnowledgeFactContext,
    KnowledgeFixedSubtotal,
    KnowledgeIndemnitySummary,
    KnowledgeQuestion,
    KnowledgeRuleEvaluation,
    KnowledgeRulePublication,
)
from familycare_api.decisions.knowledge_facts import normalize_private_event_facts
from familycare_api.decisions.operators import OperatorEvaluationError, evaluate_expression

ENGINE_VERSION = "private-knowledge-engine-v2"
_ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "up": ROUND_CEILING,
    "down": ROUND_DOWN,
}
_NON_EXECUTABLE_SUMMARY_HOLDS = frozenset(
    {
        "COVERAGE_PUBLICATION_BLOCKED",
        "NO_PUBLISHED_RULE",
    }
)


@dataclass(frozen=True)
class _CoverageOutcome:
    candidate: KnowledgeClaimCandidate
    evaluations: tuple[KnowledgeRuleEvaluation, ...]
    calculation: KnowledgeBenefitCalculation
    publication_incomplete: bool
    runtime_failed: bool


class _CalculationInputUnavailable(ValueError):
    """A valid calculation lacks one trusted event-time input."""


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _aggregate_required(evaluations: Sequence[KnowledgeRuleEvaluation]) -> TriState:
    required = tuple(item for item in evaluations if item.required)
    if not required:
        return "UNKNOWN"
    if any(item.result == "NO_MATCH" for item in required):
        return "NO_MATCH"
    if any(item.result == "UNKNOWN" for item in required):
        return "UNKNOWN"
    return "MATCH"


class DeterministicKnowledgeDecisionEngine:
    """Evaluate already-scoped, user-published rules without I/O or AI."""

    def __init__(self, *, id_factory: Callable[[], UUID] = uuid4) -> None:
        self.id_factory = id_factory

    def evaluate(
        self,
        scope: HouseholdScope,
        event: MedicalEvent,
        context: KnowledgeDecisionContext,
        *,
        run_id: UUID,
    ) -> KnowledgeDecisionResult:
        if (
            event.household_space_id != scope.household_space_id
            or context.household_space_id != scope.household_space_id
            or context.family_member_id != event.family_member_id
        ):
            raise ValueError("private knowledge scope mismatch")
        event_facts = normalize_private_event_facts(event, context.normalizers)
        facts = KnowledgeFactContext(
            facts={**context.supporting_facts, **event_facts.facts},
            audit_conflicts=event_facts.audit_conflicts,
        )
        outcomes: list[_CoverageOutcome] = []
        failures: list[str] = []
        for coverage in sorted(context.coverages, key=lambda item: str(item.knowledge_coverage_id)):
            if coverage.disposition == "NOT_APPLICABLE":
                continue
            try:
                coverage_facts = facts
                if (
                    context.receipt_currency is not None
                    and coverage.currency is not None
                    and context.receipt_currency != coverage.currency
                ):
                    coverage_facts = KnowledgeFactContext(
                        facts={
                            path: value
                            for path, value in facts.facts.items()
                            if not path.startswith("Receipt.")
                        },
                        audit_conflicts=facts.audit_conflicts,
                    )
                outcome = self._evaluate_coverage(event, coverage_facts, coverage)
            except Exception:
                outcome = self._failed_coverage(coverage)
            outcomes.append(outcome)
            if outcome.runtime_failed:
                failures.append("KNOWLEDGE_COVERAGE_EVALUATION_FAILED")

        candidates = tuple(item.candidate for item in outcomes)
        evaluations = tuple(evaluation for item in outcomes for evaluation in item.evaluations)
        calculations = tuple(item.calculation for item in outcomes)
        incomplete = any(item.publication_incomplete for item in outcomes)
        if not context.coverages:
            completeness: KnowledgeAnalysisCompleteness = "UNAVAILABLE"
        elif incomplete or failures:
            completeness = "PARTIAL"
        else:
            completeness = "COMPLETE"
        return KnowledgeDecisionResult(
            run_id=run_id,
            knowledge_import_run_id=context.knowledge_import_run_id,
            rule_import_run_id=context.rule_import_run_id,
            status_projection_digest_sha256=context.status_projection_digest_sha256,
            fact_context=facts,
            candidates=candidates,
            evaluations=evaluations,
            calculations=calculations,
            fixed_subtotals=_fixed_subtotals(candidates, calculations),
            indemnity_summary=_indemnity_summary(candidates, calculations),
            completeness=completeness,
            source_failure_codes=_unique(failures),
        )

    def _evaluate_coverage(
        self,
        event: MedicalEvent,
        facts: KnowledgeFactContext,
        coverage: KnowledgeCoverageContext,
    ) -> _CoverageOutcome:
        precondition, precondition_reason = _coverage_precondition(event, coverage)
        evaluations: list[KnowledgeRuleEvaluation] = []
        runtime_failed = False
        for rule in coverage.rules:
            evaluation, failed = self._evaluate_rule(facts, coverage, rule)
            evaluations.append(evaluation)
            runtime_failed = runtime_failed or failed
        rule_aggregate = _aggregate_required(evaluations)
        aggregate = rule_aggregate
        ai_suggested_paths = tuple(
            sorted(
                {
                    path
                    for evaluation in evaluations
                    for path in evaluation.fact_paths
                    if (fact := facts.get(path)) is not None and fact.provenance == "AI_SUGGESTED"
                }
            )
        )
        hold_reasons = (
            ["COVERAGE_PUBLICATION_ADVISORY"] if coverage.disposition == "ADVISORY" else []
        )
        hold_reasons.extend(item.reason_code for item in evaluations if item.result != "MATCH")
        if ai_suggested_paths:
            aggregate = "UNKNOWN"
            hold_reasons.append("AI_STRUCTURED_FACTS_UNCONFIRMED")
        if precondition == "NO_MATCH":
            aggregate = "NO_MATCH"
            hold_reasons.insert(0, precondition_reason)
        elif precondition == "UNKNOWN" and aggregate != "NO_MATCH":
            aggregate = "UNKNOWN"
            hold_reasons.insert(0, precondition_reason)
        if not coverage.rules:
            aggregate = "UNKNOWN"
            hold_reasons.append("NO_PUBLISHED_RULE")
        required = tuple(item for item in evaluations if item.required)
        questions = list(_questions(evaluations))
        questions.extend(
            KnowledgeQuestion(path, "AI_STRUCTURED_FACTS_UNCONFIRMED")
            for path in ai_suggested_paths
            if not any(item.field_path == path for item in questions)
        )
        if (
            precondition == "UNKNOWN"
            and precondition_reason == "EVENT_DATE_STATUS_UNCONFIRMED"
            and not any(item.field_path == "Rider.status" for item in questions)
        ):
            questions.append(KnowledgeQuestion("Rider.status", "EVENT_DATE_STATUS_UNCONFIRMED"))
        candidate = KnowledgeClaimCandidate(
            candidate_id=self.id_factory(),
            knowledge_contract_id=coverage.knowledge_contract_id,
            knowledge_coverage_id=coverage.knowledge_coverage_id,
            contract_label=coverage.contract_label,
            coverage_label=coverage.coverage_label,
            benefit_type=coverage.benefit_type,
            result=aggregate,
            evaluations=tuple(evaluations),
            questions=tuple(questions),
            hold_reason_codes=_unique(hold_reasons),
            required_match_count=sum(item.result == "MATCH" for item in required),
            required_unknown_count=sum(item.result == "UNKNOWN" for item in required),
            required_no_match_count=sum(item.result == "NO_MATCH" for item in required),
        )
        conditional_hold_reason = None
        if (
            rule_aggregate == "MATCH"
            and precondition == "UNKNOWN"
            and coverage.benefit_type == "FIXED"
        ):
            if coverage.disposition == "ADVISORY" and precondition_reason in {
                "COVERAGE_PUBLICATION_ADVISORY",
                "COVERAGE_AUTHORITY_INCOMPLETE",
            }:
                conditional_hold_reason = "COVERAGE_PUBLICATION_ADVISORY"
            elif precondition_reason in {
                "EVENT_DATE_REQUIRED",
                "EVENT_DATE_STATUS_UNCONFIRMED",
            }:
                conditional_hold_reason = precondition_reason
        if (
            rule_aggregate == "MATCH"
            and aggregate == "UNKNOWN"
            and conditional_hold_reason is None
            and ai_suggested_paths
            and coverage.benefit_type == "FIXED"
        ):
            conditional_hold_reason = "AI_STRUCTURED_FACTS_UNCONFIRMED"
        calculation, calculation_failed = self._calculate(
            facts,
            coverage,
            candidate,
            conditional_hold_reason=conditional_hold_reason,
        )
        calculation_publication_missing = (
            candidate.result == "MATCH"
            and coverage.benefit_type == "FIXED"
            and coverage.calculation is None
        )
        publication_incomplete = (
            coverage.disposition != "PUBLISHED"
            or precondition == "UNKNOWN"
            or any(item.reason_code == "CITATION_INVALID" for item in evaluations)
            or not coverage.rules
            or runtime_failed
            or calculation_failed
            or calculation_publication_missing
        )
        return _CoverageOutcome(
            candidate=candidate,
            evaluations=tuple(evaluations),
            calculation=calculation,
            publication_incomplete=publication_incomplete,
            runtime_failed=runtime_failed or calculation_failed,
        )

    def _evaluate_rule(
        self,
        facts: KnowledgeFactContext,
        coverage: KnowledgeCoverageContext,
        rule: KnowledgeRulePublication,
    ) -> tuple[KnowledgeRuleEvaluation, bool]:
        citation_keys = tuple(item.citation_key for item in rule.citations)
        if (
            not rule.citations
            or len(citation_keys) != len(set(citation_keys))
            or any(not item.lineage_valid for item in rule.citations)
        ):
            return self._unknown_evaluation(
                coverage,
                rule,
                reason_code="CITATION_INVALID",
            ), False
        try:
            validated = validate_rule_document(rule.rule_document, citation_keys)
            if (
                validated.expression is None
                or validated.rule_kind != rule.rule_kind
                or validated.required is not rule.required
                or validated.result_reason_code != rule.result_reason_code
            ):
                raise RuleValidationError("RULE_METADATA_MISMATCH")
            legacy_context = _legacy_fact_context(facts, coverage)
            outcome = evaluate_expression(validated.expression, legacy_context)
        except RuleValidationError, OperatorEvaluationError:
            return self._unknown_evaluation(
                coverage,
                rule,
                reason_code="UNSUPPORTED_DSL",
            ), True

        result = outcome.result
        reason = rule.result_reason_code if result == "MATCH" else outcome.reason_code
        if rule.rule_kind == "exclusion":
            if result == "MATCH":
                result = "NO_MATCH"
                reason = rule.result_reason_code
            elif result == "NO_MATCH":
                result = "MATCH"
                reason = "EXCLUSION_NOT_ESTABLISHED"
        return (
            KnowledgeRuleEvaluation(
                evaluation_id=self.id_factory(),
                knowledge_coverage_id=coverage.knowledge_coverage_id,
                rule_publication_id=rule.publication_id,
                result=result,
                required=rule.required,
                reason_code=reason,
                fact_paths=validated.referenced_fields,
                missing_fields=outcome.missing_fields,
                conflicting_fields=outcome.conflicting_fields,
                citations=rule.citations,
            ),
            False,
        )

    def _unknown_evaluation(
        self,
        coverage: KnowledgeCoverageContext,
        rule: KnowledgeRulePublication,
        *,
        reason_code: str,
    ) -> KnowledgeRuleEvaluation:
        return KnowledgeRuleEvaluation(
            evaluation_id=self.id_factory(),
            knowledge_coverage_id=coverage.knowledge_coverage_id,
            rule_publication_id=rule.publication_id,
            result="UNKNOWN",
            required=rule.required,
            reason_code=reason_code,
            citations=rule.citations,
        )

    def _calculate(
        self,
        facts: KnowledgeFactContext,
        coverage: KnowledgeCoverageContext,
        candidate: KnowledgeClaimCandidate,
        *,
        conditional_hold_reason: str | None = None,
    ) -> tuple[KnowledgeBenefitCalculation, bool]:
        publication = coverage.calculation
        if candidate.result == "NO_MATCH":
            return self._calculation_unknown(
                candidate,
                coverage,
                status="NOT_APPLICABLE",
                reason="CANDIDATE_NOT_MATCHED",
            ), False
        if candidate.result != "MATCH" and conditional_hold_reason is None:
            return self._calculation_unknown(
                candidate,
                coverage,
                status="UNKNOWN",
                reason="CANDIDATE_NOT_RESOLVED",
            ), False
        if publication is None:
            if (
                coverage.benefit_type == "FIXED"
                and coverage.insured_amount is not None
                and coverage.insured_amount.is_finite()
                and coverage.insured_amount >= 0
                and coverage.currency is not None
            ):
                amount = coverage.insured_amount
                amount_hold_reason = conditional_hold_reason
                if (
                    coverage.certificate_amount_decision != "MATCH"
                    or coverage.certificate_amount_evidence_state != "DIRECT"
                ):
                    amount_hold_reason = "CERTIFICATE_AMOUNT_EVIDENCE_REVIEW_REQUIRED"
                return (
                    KnowledgeBenefitCalculation(
                        calculation_id=self.id_factory(),
                        candidate_id=candidate.candidate_id,
                        knowledge_coverage_id=coverage.knowledge_coverage_id,
                        calculation_publication_id=None,
                        kind="FIXED",
                        status="CALCULATED",
                        currency=coverage.currency,
                        conditional_amount=amount,
                        # A certificate amount is useful for an estimate, but
                        # without a reviewed calculation publication it must
                        # never become an authority-bearing confirmed amount.
                        confirmed_amount=None,
                        hold_reason_code=amount_hold_reason,
                        certificate_amount_decision=coverage.certificate_amount_decision,
                        certificate_amount_evidence_state=(
                            coverage.certificate_amount_evidence_state
                        ),
                        certificate_evidence=coverage.certificate_evidence,
                        steps=(
                            KnowledgeCalculationStep(
                                step_number=1,
                                operation="certificate_insured_amount",
                                input_amount=amount,
                                output_amount=amount,
                                currency=coverage.currency,
                                rounding_rule=None,
                                reason_code="CERTIFICATE_INSURED_AMOUNT_ESTIMATE",
                            ),
                        ),
                    ),
                    False,
                )
            reason = (
                "RECEIPT_COVERED_AMOUNT_REQUIRED"
                if coverage.benefit_type == "INDEMNITY"
                else "CALCULATION_NOT_PUBLISHED"
            )
            return self._calculation_unknown(
                candidate,
                coverage,
                status="UNKNOWN",
                reason=reason,
            ), False
        if not publication.citations or any(
            not item.lineage_valid for item in publication.citations
        ):
            return self._calculation_unknown(
                candidate,
                coverage,
                status="FAILED",
                reason="CALCULATION_CITATION_INVALID",
                publication=publication,
            ), True
        input_field_paths = publication.calculation_document.get("input_field_paths")
        uses_certificate_amount = isinstance(input_field_paths, list) and any(
            item == "Rider.insured_amount" for item in input_field_paths
        )
        calculation_hold_reason = conditional_hold_reason
        if uses_certificate_amount and (
            coverage.certificate_amount_decision != "MATCH"
            or coverage.certificate_amount_evidence_state != "DIRECT"
        ):
            calculation_hold_reason = "CERTIFICATE_AMOUNT_EVIDENCE_REVIEW_REQUIRED"
        citation_keys = tuple(item.citation_key for item in publication.citations)
        try:
            validated = validate_rule_document(
                publication.calculation_document,
                citation_keys,
            )
            if (
                validated.calculation is None
                or publication.calculation_kind != coverage.benefit_type
                or validated.result_reason_code != publication.result_reason_code
            ):
                raise RuleValidationError("CALCULATION_METADATA_MISMATCH")
            state = _CalculationState(_legacy_fact_context(facts, coverage), coverage.currency)
            amount = state.evaluate(validated.calculation)
            if amount < 0 or not amount.is_finite() or coverage.currency is None:
                raise ValueError
        except _CalculationInputUnavailable:
            return self._calculation_unknown(
                candidate,
                coverage,
                status="UNKNOWN",
                reason="CALCULATION_INPUT_UNAVAILABLE",
                publication=publication,
            ), False
        except RuleValidationError, OperatorEvaluationError, ArithmeticError, ValueError:
            return self._calculation_unknown(
                candidate,
                coverage,
                status="UNKNOWN",
                reason="CALCULATION_INPUT_UNAVAILABLE",
                publication=publication,
            ), True
        return (
            KnowledgeBenefitCalculation(
                calculation_id=self.id_factory(),
                candidate_id=candidate.candidate_id,
                knowledge_coverage_id=coverage.knowledge_coverage_id,
                calculation_publication_id=publication.publication_id,
                kind=publication.calculation_kind,
                status="CALCULATED",
                currency=coverage.currency,
                conditional_amount=amount,
                confirmed_amount=(amount if calculation_hold_reason is None else None),
                rounding_rule=state.last_rounding,
                hold_reason_code=calculation_hold_reason,
                certificate_amount_decision=coverage.certificate_amount_decision,
                certificate_amount_evidence_state=coverage.certificate_amount_evidence_state,
                certificate_evidence=coverage.certificate_evidence,
                steps=tuple(state.steps),
            ),
            False,
        )

    def _calculation_unknown(
        self,
        candidate: KnowledgeClaimCandidate,
        coverage: KnowledgeCoverageContext,
        *,
        status: KnowledgeCalculationStatus,
        reason: str,
        publication: KnowledgeCalculationPublication | None = None,
    ) -> KnowledgeBenefitCalculation:
        return KnowledgeBenefitCalculation(
            calculation_id=self.id_factory(),
            candidate_id=candidate.candidate_id,
            knowledge_coverage_id=coverage.knowledge_coverage_id,
            calculation_publication_id=(
                publication.publication_id if publication is not None else None
            ),
            kind=coverage.benefit_type,
            status=status,
            currency=(coverage.currency if status == "UNKNOWN" else None),
            conditional_amount=None,
            hold_reason_code=reason,
        )

    def _failed_coverage(self, coverage: KnowledgeCoverageContext) -> _CoverageOutcome:
        hold_reasons = ["KNOWLEDGE_COVERAGE_EVALUATION_FAILED"]
        if coverage.disposition == "BLOCKED":
            hold_reasons.insert(0, "COVERAGE_PUBLICATION_BLOCKED")
        elif coverage.disposition == "ADVISORY":
            hold_reasons.insert(0, "COVERAGE_PUBLICATION_ADVISORY")
        candidate = KnowledgeClaimCandidate(
            candidate_id=self.id_factory(),
            knowledge_contract_id=coverage.knowledge_contract_id,
            knowledge_coverage_id=coverage.knowledge_coverage_id,
            contract_label=coverage.contract_label,
            coverage_label=coverage.coverage_label,
            benefit_type=coverage.benefit_type,
            result="UNKNOWN",
            evaluations=(),
            questions=(),
            hold_reason_codes=tuple(hold_reasons),
            required_match_count=0,
            required_unknown_count=0,
            required_no_match_count=0,
        )
        return _CoverageOutcome(
            candidate=candidate,
            evaluations=(),
            calculation=self._calculation_unknown(
                candidate,
                coverage,
                status="FAILED",
                reason="KNOWLEDGE_COVERAGE_EVALUATION_FAILED",
            ),
            publication_incomplete=True,
            runtime_failed=True,
        )


def _coverage_precondition(
    event: MedicalEvent,
    coverage: KnowledgeCoverageContext,
) -> tuple[TriState, str]:
    if coverage.disposition == "BLOCKED":
        return "UNKNOWN", "COVERAGE_PUBLICATION_BLOCKED"
    advisory = coverage.disposition == "ADVISORY"
    if coverage.enrollment_decision == "NO_MATCH":
        return "NO_MATCH", "CERTIFICATE_ENROLLMENT_NO_MATCH"
    if (
        coverage.subject_binding_decision == "NO_MATCH"
        or coverage.mapping_applicability == "NOT_APPLICABLE"
        or coverage.mapping_enrollment_decision == "NO_MATCH"
        or coverage.document_identity_decision == "NO_MATCH"
        or coverage.edition_applicability_decision == "NO_MATCH"
        or coverage.section_mapping_decision == "NO_MATCH"
        or coverage.overall_mapping_decision == "NO_MATCH"
        or coverage.current_confirmation_decision == "NO_MATCH"
    ):
        return "NO_MATCH", "COVERAGE_AUTHORITY_NO_MATCH"
    if coverage.current_confirmation_decision == "MATCH" and (
        coverage.current_confirmed_status in {"inactive", "lapsed", "terminated"}
    ):
        return "NO_MATCH", "CURRENT_CONTRACT_INACTIVE"
    if event.event_date is not None and (
        (coverage.contract_start is not None and event.event_date < coverage.contract_start)
        or (coverage.contract_end is not None and event.event_date > coverage.contract_end)
    ):
        return "NO_MATCH", "EVENT_DATE_OUTSIDE_CONTRACT_TERM"
    intervals = tuple(
        item
        for item in coverage.status_intervals
        if event.event_date is not None
        and item.effective_from <= event.event_date <= item.effective_through
    )
    if len(intervals) == 1:
        interval = intervals[0]
        if interval.decision == "NO_MATCH":
            return "NO_MATCH", "EVENT_DATE_STATUS_NO_MATCH"
        if interval.decision == "MATCH" and interval.confirmed_status != "active":
            return "NO_MATCH", "EVENT_DATE_CONTRACT_INACTIVE"
    required_matches = (
        coverage.subject_binding_decision == "MATCH",
        coverage.enrollment_decision == "MATCH",
        coverage.component_classification == "BENEFIT_COVERAGE",
        coverage.mapping_applicability == "APPLICABLE",
        coverage.mapping_enrollment_decision == "MATCH",
        coverage.document_identity_decision == "MATCH",
        coverage.edition_applicability_decision == "MATCH",
        coverage.section_mapping_decision == "MATCH",
        coverage.overall_mapping_decision == "MATCH",
        coverage.current_confirmation_decision == "MATCH",
        coverage.current_confirmed_status == "active",
    )
    if not all(required_matches):
        return "UNKNOWN", "COVERAGE_AUTHORITY_INCOMPLETE"
    if event.event_date is None:
        return "UNKNOWN", "EVENT_DATE_REQUIRED"
    if len(intervals) != 1:
        return "UNKNOWN", "EVENT_DATE_STATUS_UNCONFIRMED"
    interval = intervals[0]
    if interval.decision != "MATCH":
        return "UNKNOWN", "EVENT_DATE_STATUS_UNCONFIRMED"
    if interval.confirmed_status != "active":
        return "NO_MATCH", "EVENT_DATE_CONTRACT_INACTIVE"
    if advisory:
        return "UNKNOWN", "COVERAGE_PUBLICATION_ADVISORY"
    return "MATCH", "COVERAGE_AUTHORITY_MATCH"


def _fact_value(value: KnowledgeFact) -> FactValue:
    confirmation: FactConfirmation = (
        "user"
        if value.is_trusted
        else "ai_structured"
        if value.provenance == "AI_SUGGESTED" and not value.stale
        else "conflicting"
        if value.provenance == "CONFLICTING"
        else "unconfirmed"
    )
    return FactValue(
        value=value.value,
        confirmation=confirmation,
        evidence_ids=(),
        evidence_stale=value.stale,
    )


def _legacy_fact_context(
    facts: KnowledgeFactContext,
    coverage: KnowledgeCoverageContext,
) -> FactContext:
    medical: dict[str, FactValue] = {}
    policy: dict[str, FactValue] = {}
    claim_history: dict[str, FactValue] = {}
    receipt: dict[str, FactValue] = {}
    for field_path, value in facts.facts.items():
        if field_path.startswith("MedicalEvent."):
            medical[field_path] = _fact_value(value)
        elif field_path.startswith("PolicyContract."):
            policy[field_path] = _fact_value(value)
        elif field_path.startswith("ClaimHistory."):
            claim_history[field_path] = _fact_value(value)
        elif field_path.startswith("Receipt."):
            receipt[field_path] = _fact_value(value)
    policy.update(
        {
            "PolicyContract.contract_start": FactValue(
                coverage.contract_start,
                "user" if coverage.contract_start is not None else "unconfirmed",
                (),
            ),
            "PolicyContract.contract_end": FactValue(
                coverage.contract_end,
                "user" if coverage.contract_end is not None else "unconfirmed",
                (),
            ),
        }
    )
    rider = {
        "Rider.insured_amount": FactValue(
            coverage.insured_amount,
            (
                "user"
                if coverage.insured_amount is not None
                and coverage.certificate_amount_decision == "MATCH"
                and coverage.certificate_amount_evidence_state == "DIRECT"
                else "ai_structured"
                if coverage.insured_amount is not None
                else "unconfirmed"
            ),
            (),
        ),
        "Rider.status": FactValue(
            coverage.current_confirmed_status,
            "user" if coverage.current_confirmed_status is not None else "unconfirmed",
            (),
        ),
    }
    if coverage.claim_history_counted_occurrence is not None:
        claim_history["ClaimHistory.counted_occurrence"] = _fact_value(
            coverage.claim_history_counted_occurrence
        )
    event_date = facts.get("MedicalEvent.event_date")
    as_of_date = (
        event_date.value if event_date is not None and isinstance(event_date.value, date) else None
    )
    return FactContext(
        medical_event=medical,
        policy=policy,
        rider=rider,
        claim_history=claim_history,
        receipt=receipt,
        as_of_date=as_of_date,
    )


def _questions(
    evaluations: Sequence[KnowledgeRuleEvaluation],
) -> tuple[KnowledgeQuestion, ...]:
    result: list[KnowledgeQuestion] = []
    seen: set[str] = set()
    for evaluation in evaluations:
        for field_path in (*evaluation.missing_fields, *evaluation.conflicting_fields):
            if field_path in seen:
                continue
            seen.add(field_path)
            result.append(KnowledgeQuestion(field_path, evaluation.reason_code))
    return tuple(result)


@dataclass
class _CalculationState:
    context: FactContext
    currency: str | None
    steps: list[KnowledgeCalculationStep] = field(default_factory=list)
    last_rounding: str | None = None

    def evaluate(self, node: CompiledCalculation) -> Decimal:
        values = tuple(self._operand(value) for value in node.operands)
        if node.operator == "add":
            output = sum(values, Decimal("0"))
        elif node.operator == "subtract":
            output = values[0] - sum(values[1:], Decimal("0"))
        elif node.operator == "multiply":
            output = Decimal("1")
            for value in values:
                output *= value
        elif node.operator == "min":
            output = min(values)
        elif node.operator == "max":
            output = max(values)
        elif node.operator == "round":
            rounding = _ROUNDING.get(cast(str, node.rounding))
            if rounding is None:
                raise ValueError
            output = values[0].quantize(Decimal("1"), rounding=rounding)
            self.last_rounding = node.rounding
        else:
            raise ValueError
        if not output.is_finite():
            raise ValueError
        self.steps.append(
            KnowledgeCalculationStep(
                step_number=len(self.steps) + 1,
                operation=node.operator,
                input_amount=values[0] if values else None,
                output_amount=output,
                currency=self.currency,
                rounding_rule=node.rounding,
                reason_code=f"KNOWLEDGE_{node.operator.upper()}",
            )
        )
        return output

    def _operand(self, value: object) -> Decimal:
        if isinstance(value, CompiledCalculation):
            return self.evaluate(value)
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str):
            fact = self.context.get(value)
            if (
                fact is None
                or fact.value is None
                or fact.confirmation not in {"user", "ai_structured"}
                or fact.evidence_stale
                or isinstance(fact.value, bool)
            ):
                raise _CalculationInputUnavailable
            try:
                result = Decimal(str(fact.value))
            except ValueError, ArithmeticError:
                raise _CalculationInputUnavailable from None
            if not result.is_finite():
                raise _CalculationInputUnavailable
            return result
        raise ValueError


def _fixed_subtotals(
    candidates: Sequence[KnowledgeClaimCandidate],
    calculations: Sequence[KnowledgeBenefitCalculation],
) -> tuple[KnowledgeFixedSubtotal, ...]:
    candidate_by_id = {item.candidate_id: item for item in candidates}
    amounts: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    calculated: dict[str, int] = defaultdict(int)
    unresolved: dict[str, int] = defaultdict(int)
    for calculation in calculations:
        candidate = candidate_by_id[calculation.candidate_id]
        if (
            candidate.benefit_type != "FIXED"
            or candidate.result == "NO_MATCH"
            or not _is_executable_summary_candidate(candidate)
        ):
            continue
        currency = calculation.currency
        if currency is None:
            continue
        if _is_authorized_fixed_amount(candidate, calculation):
            assert calculation.conditional_amount is not None
            amounts[currency] += calculation.conditional_amount
            calculated[currency] += 1
        else:
            unresolved[currency] += 1
    return tuple(
        KnowledgeFixedSubtotal(
            currency=currency,
            amount=amounts[currency],
            calculated_candidate_count=calculated[currency],
            unresolved_candidate_count=unresolved[currency],
        )
        for currency in sorted(set(amounts) | set(unresolved))
    )


def _indemnity_summary(
    candidates: Sequence[KnowledgeClaimCandidate],
    calculations: Sequence[KnowledgeBenefitCalculation],
) -> KnowledgeIndemnitySummary:
    candidate_ids = {
        item.candidate_id
        for item in candidates
        if item.benefit_type == "INDEMNITY"
        and item.result != "NO_MATCH"
        and _is_executable_summary_candidate(item)
    }
    relevant = tuple(item for item in calculations if item.candidate_id in candidate_ids)
    if not candidate_ids:
        return KnowledgeIndemnitySummary("NONE", 0, 0, 0)
    candidate_by_id = {item.candidate_id: item for item in candidates}
    calculated = sum(
        item.status == "CALCULATED" and candidate_by_id[item.candidate_id].result == "MATCH"
        for item in relevant
    )
    unresolved = len(candidate_ids) - calculated
    return KnowledgeIndemnitySummary(
        "CALCULATED" if unresolved == 0 else "UNKNOWN",
        len(candidate_ids),
        calculated,
        unresolved,
    )


def _is_executable_summary_candidate(candidate: KnowledgeClaimCandidate) -> bool:
    return not _NON_EXECUTABLE_SUMMARY_HOLDS.intersection(candidate.hold_reason_codes)


def _is_authorized_fixed_amount(
    candidate: KnowledgeClaimCandidate,
    calculation: KnowledgeBenefitCalculation,
) -> bool:
    if calculation.status != "CALCULATED" or calculation.conditional_amount is None:
        return False
    if candidate.result == "MATCH":
        return calculation.hold_reason_code in {
            None,
            "CERTIFICATE_AMOUNT_EVIDENCE_REVIEW_REQUIRED",
        }
    return (
        candidate.result == "UNKNOWN"
        and calculation.confirmed_amount is None
        and calculation.hold_reason_code is not None
        and (
            calculation.hold_reason_code in candidate.hold_reason_codes
            or calculation.hold_reason_code == "CERTIFICATE_AMOUNT_EVIDENCE_REVIEW_REQUIRED"
        )
    )


def summarize_knowledge_results(
    candidates: Sequence[KnowledgeClaimCandidate],
    calculations: Sequence[KnowledgeBenefitCalculation],
) -> tuple[tuple[KnowledgeFixedSubtotal, ...], KnowledgeIndemnitySummary]:
    """Rebuild derived summaries from an immutable stored result snapshot."""

    return _fixed_subtotals(candidates, calculations), _indemnity_summary(candidates, calculations)


__all__ = [
    "DeterministicKnowledgeDecisionEngine",
    "ENGINE_VERSION",
    "summarize_knowledge_results",
]

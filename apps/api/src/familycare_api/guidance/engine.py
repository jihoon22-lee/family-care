"""Pure document-based guidance; no provider calls or confirmed-status mutation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Literal
from uuid import UUID

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeDecisionContext,
    KnowledgeFact,
    KnowledgeFactContext,
)
from familycare_api.guidance.activity_binding import source_activity
from familycare_api.guidance.calculation_runtime import CalculationSourceRef
from familycare_api.guidance.case_relations import source_case_relation
from familycare_api.guidance.domain import (
    GuidanceCitation,
    GuidanceContext,
    GuidanceCoverageInput,
    GuidancePayoutCaseInput,
    GuidanceRuleEvaluation,
)
from familycare_api.guidance.estimates import estimate_coverage
from familycare_api.guidance.event_facts import CodeScope, EventFactRead, build_event_facts
from familycare_api.guidance.expense_projection import (
    RECEIPT_FIELDS,
    covered_cost_input,
    expense_failure,
    expense_summary,
)
from familycare_api.guidance.interpretation import Activity
from familycare_api.guidance.models import (
    Freshness,
    GuidanceCandidate,
    GuidanceCondition,
    GuidanceEventSpan,
    GuidanceEvidenceRef,
    GuidanceQuestion,
    GuidanceRelevance,
    GuidanceSupport,
    LocalGuidanceResponse,
)
from familycare_api.guidance.payout_cases import (
    MAX_PAYOUT_CASES,
    case_coverage,
    combine_payout_cases,
    source_payout_case,
)
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.relevance import find_relevance
from familycare_api.guidance.rule_runtime import GuidanceRuleRuntime
from familycare_api.guidance.scenarios import planned_care_scenarios


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


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
        activity_reads: dict[Activity | None, EventFactRead] = {None: event_read}
        candidates: list[GuidanceCandidate] = []
        unsupported = 0
        expense_error = expense_failure(context.expenses, event)
        failures = [*context.failure_codes, *([expense_error] if expense_error else [])]
        groups: dict[CanonicalCoverageRef, list[GuidanceCoverageInput]] = {}
        for source_coverage in sorted(
            context.coverages, key=lambda item: (item.ref.kind, str(item.ref.coverage_id))
        ):
            canonical_ref = (
                source_coverage.canonical_identity.ref
                if source_coverage.canonical_identity is not None
                else source_coverage.ref
            )
            groups.setdefault(canonical_ref, []).append(source_coverage)
        for variants in groups.values():
            if sum(len(source.cases) or 1 for source in variants) > MAX_PAYOUT_CASES:
                failures.append("GUIDANCE_PAYOUT_CASE_LIMIT_EXCEEDED")
                unsupported += 1
                continue
            views = [
                (
                    case_coverage(source, case) if case is not None else source,
                    source_payout_case(source, case, namespace=len(variants) > 1)
                    if case is not None or len(variants) > 1
                    else None,
                )
                for source in variants
                for case in (source.cases or (None,))
            ]
            matches: list[tuple[GuidancePayoutCaseInput, GuidanceCandidate]] = []
            matched_views: list[GuidanceCoverageInput] = []
            all_supported = True
            for coverage, case in views:
                activity = source_activity(coverage)
                if activity not in activity_reads:
                    activity_reads[activity] = build_event_facts(
                        event,
                        context.normalizers,
                        selected_subject_terms=context.selected_subject_terms,
                        other_subject_terms=context.other_subject_terms,
                        activity=activity,
                    )
                coverage_read = activity_reads[activity]
                normalized = coverage_read.context
                facts = KnowledgeFactContext(
                    facts={**context.supporting_facts, **normalized.facts},
                    audit_conflicts=normalized.audit_conflicts,
                )
                cost = covered_cost_input(
                    context.expenses,
                    event,
                    None
                    if (
                        coverage.canonical_identity is not None
                        and "currency" in coverage.canonical_identity.field_conflicts
                    )
                    else coverage.currency,
                )
                # The old private context aggregated confirmed excluded costs too.
                # The v2 runtime uses the receipt reader's covered subset and real refs.
                receipt_facts = (
                    {path: KnowledgeFact(cost.value, "USER_CONFIRMED") for path in RECEIPT_FIELDS}
                    if cost.value is not None
                    else {}
                )
                coverage_facts = KnowledgeFactContext(
                    facts={
                        **{
                            path: value
                            for path, value in facts.facts.items()
                            if path not in RECEIPT_FIELDS
                        },
                        **receipt_facts,
                    },
                    audit_conflicts=facts.audit_conflicts,
                )
                try:
                    candidate, supported = self._coverage(
                        event,
                        coverage_facts,
                        coverage,
                        event_read=coverage_read,
                        normalizer_code_scopes=context.normalizer_code_scopes,
                        activity=activity,
                        supporting_sources={path: cost.source_refs for path in receipt_facts},
                        partial_costs=cost.partial,
                    )
                except ValueError, ArithmeticError:
                    candidate, supported = None, False
                    failures.append("GUIDANCE_COVERAGE_FAILED")
                all_supported = all_supported and supported
                if candidate is not None:
                    if case is None:
                        candidates.append(candidate)
                    else:
                        matches.append((case, candidate))
                        matched_views.append(coverage)
            if matches:
                candidates.append(
                    combine_payout_cases(tuple(matches), source_case_relation(tuple(matched_views)))
                )
            unsupported += not all_supported
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
            for path, item in event_read.context.facts.items()
        ):
            outcome = "INPUT_UNRESOLVED"
        response = LocalGuidanceResponse(
            schema_version="2",
            family_member_id=event.family_member_id,
            medical_event_id=event.id,
            event_version=event.version,
            event_date=event.event_date,
            outcome=outcome,
            versions=context.versions.model_copy(update={"engine": "local-guidance-v2"}),
            candidates=tuple(candidates),
            expenses=expense_summary(context.expenses, event),
            support=GuidanceSupport(
                total_coverages=len(groups),
                evaluated_coverages=len(groups) - unsupported,
                unsupported_coverages=unsupported,
                failure_codes=_unique(failures),
            ),
        )
        from familycare_api.guidance.models import GuidanceFixedSubtotal, GuidanceSubtotalOmission
        from familycare_api.guidance.subtotals import fixed_subtotal_projection

        try:
            totals = fixed_subtotal_projection(response)
            return response.model_copy(
                update={
                    "fixed_subtotals": tuple(
                        GuidanceFixedSubtotal.model_validate(item)
                        for item in totals["fixed_subtotals"]
                    ),
                    "subtotal_omissions": tuple(
                        GuidanceSubtotalOmission.model_validate(item)
                        for item in totals["subtotal_omissions"]
                    ),
                }
            )
        except ValueError, ArithmeticError:
            return response.model_copy(
                update={
                    "support": response.support.model_copy(
                        update={
                            "failure_codes": _unique(
                                [*response.support.failure_codes, "GUIDANCE_SUBTOTAL_FAILED"]
                            )
                        }
                    )
                }
            )

    def _coverage(
        self,
        event: MedicalEvent,
        facts: KnowledgeFactContext,
        coverage: GuidanceCoverageInput,
        *,
        event_read: EventFactRead,
        normalizer_code_scopes: Mapping[str, CodeScope],
        activity: Activity | None,
        supporting_sources: Mapping[str, tuple[CalculationSourceRef, ...]],
        partial_costs: bool,
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
        # A known subset can price that subset, but cannot settle a predicate
        # about the full expense total (including a decisive upper/lower bound).
        rule_facts = KnowledgeFactContext(
            facts={
                path: replace(fact, value=None, provenance="UNCONFIRMED")
                if partial_costs and path in RECEIPT_FIELDS
                else fact
                for path, fact in facts.facts.items()
            },
            audit_conflicts=facts.audit_conflicts,
        )
        outcomes = tuple(
            self.rule_runtime._evaluate_rule(
                rule_facts, effective_coverage, rule, event_read=event_read
            )
            for rule in coverage.rules
        )
        evaluations = tuple(item[0] for item in outcomes)
        required = tuple(item for item in evaluations if item.required)
        if any(item.result == "NO_MATCH" for item in required):
            return None, True
        relevance = find_relevance(
            rule_facts,
            effective_coverage,
            event_read,
            normalizer_code_scopes=normalizer_code_scopes,
            activity=activity,
        )
        if not relevance:
            return None, not any(failed for _, failed in outcomes)
        conditions: Literal["MATCH", "UNKNOWN"] = (
            "UNKNOWN"
            if coverage.knowledge_incomplete
            or not any(item.kind == "CONFIRMED_EVENT" for item in relevance)
            or any(item.result == "UNKNOWN" for item in required)
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
        estimate = estimate_coverage(
            event,
            facts,
            effective_coverage,
            event_read,
            conditions=conditions,
            assumptions=assumptions,
            supporting_sources=supporting_sources,
            partial_costs=partial_costs,
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
            contract_amount=coverage.contract_amount,
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
                + [
                    "PLANNED_EVENT_RELEVANCE"
                    if item.kind == "PLANNED_EVENT"
                    else "LOCAL_TOPIC_RELEVANCE"
                    for item in relevance
                    if item.kind != "CONFIRMED_EVENT"
                ]
                + [item.reason_code for item in required if item.result != "MATCH"]
            ),
            assumptions=tuple(assumptions),
            conditions=tuple(self._condition(item) for item in evaluations),
            relevance=tuple(
                GuidanceRelevance(
                    kind=item.kind,
                    field_path=item.field_path,
                    publication_id=item.publication_id,
                    rule_key=item.rule_key,
                    source_kind=item.source_kind,
                    semantic_node_id=item.semantic_node_id,
                    spans=tuple(GuidanceEventSpan(start=s.start, end=s.end) for s in item.spans),
                    normalizer_key=item.normalizer_key,
                    evidence=_evidence(item.citations, item.publication_id),
                )
                for item in relevance
            ),
            estimate=estimate,
            scenarios=planned_care_scenarios(
                event,
                facts,
                effective_coverage,
                event_read,
                assumptions=assumptions,
            )
            if any(item.kind == "PLANNED_EVENT" for item in relevance)
            else (),
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
            required=value.required,
            rule_id=None if semantic else value.rule_publication_id,
            semantic_publication_id=value.rule_publication_id if semantic else None,
            semantic_node_id=value.source.semantic_node_id if semantic else None,
            result=value.result,
            reason_code=value.reason_code,
            evidence=_evidence(value.citations, value.rule_publication_id),
        )

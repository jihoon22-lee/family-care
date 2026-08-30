"""Strict HTTP schemas for structured events and deterministic decisions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictStr, model_validator

from familycare_api.common.evidence import EvidenceRef
from familycare_api.decisions.assistance import AnalysisAssistance, AnalysisRecommendation
from familycare_api.decisions.domain import (
    ClaimCandidate,
    DecisionRunResult,
    FactConfirmation,
    FactValue,
    MedicalEvent,
    Question,
    RuleEvaluation,
    TriState,
)
from familycare_api.decisions.knowledge_domain import (
    KnowledgeBenefitCalculation,
    KnowledgeCalculationStep,
    KnowledgeCitation,
    KnowledgeClaimCandidate,
    KnowledgeFixedSubtotal,
    KnowledgeIndemnitySummary,
    KnowledgeRuleEvaluation,
)
from familycare_api.decisions.structuring_schemas import (
    FactFieldId,
    OptionalQuestionResponse,
    StructuredFactResponse,
    is_valid_structured_fact_value,
)

FactScalar = str | int | Decimal | date | None
_EVENT_FIELDS = frozenset(
    {
        "MedicalEvent.classification",
        "MedicalEvent.admission_days",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactInput(StrictModel):
    value: FactScalar
    confirmation: FactConfirmation


class StructuredFactInput(StrictModel):
    field_id: FactFieldId
    value: Annotated[str, Field(min_length=1, max_length=160)] | bool | None


class MedicalEventCreateRequest(StrictModel):
    family_member_id: UUID
    mode: Literal["pre_visit", "post_treatment"]
    situation: Annotated[str, Field(min_length=1, max_length=2_000)]
    event_date: date | None = None
    visit_date: date | None = None
    facts: dict[str, FactInput] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def validate_fact_paths(self) -> Self:
        if not self.situation.strip():
            raise ValueError("situation cannot be blank")
        _require_event_fields(self.facts)
        return self


class MedicalEventUpdateRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1)]
    mode: Literal["pre_visit", "post_treatment"] | None = None
    situation: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None
    event_date: date | None = None
    visit_date: date | None = None
    facts: dict[str, FactInput] | None = Field(default=None, max_length=16)
    structured_facts: list[StructuredFactInput] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if "mode" in self.model_fields_set and self.mode is None:
            raise ValueError("mode cannot be null")
        if "situation" in self.model_fields_set and (
            self.situation is None or not self.situation.strip()
        ):
            raise ValueError("situation cannot be blank")
        if "facts" in self.model_fields_set and self.facts is None:
            raise ValueError("facts cannot be null")
        if "structured_facts" in self.model_fields_set and self.structured_facts is None:
            raise ValueError("structured facts cannot be null")
        if self.facts is not None:
            _require_event_fields(self.facts)
        if self.structured_facts is not None:
            _require_structured_facts(self.structured_facts)
        if self.model_fields_set <= {"expected_version"}:
            raise ValueError("empty update")
        return self


class ExpectedVersionRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1)]


class FactResponse(StrictModel):
    value: FactScalar
    confirmation: FactConfirmation

    @classmethod
    def from_domain(cls, value: FactValue) -> Self:
        return cls(value=cast(FactScalar, value.value), confirmation=value.confirmation)


class MedicalEventResponse(StrictModel):
    id: UUID
    family_member_id: UUID
    mode: Literal["pre_visit", "post_treatment"]
    situation: str
    event_date: date | None
    visit_date: date | None
    facts: dict[str, FactResponse]
    structured_facts: list[StructuredFactResponse] = Field(default_factory=list)
    optional_questions: list[OptionalQuestionResponse] = Field(default_factory=list)
    version: int
    deleted: bool

    @classmethod
    def from_value(cls, value: MedicalEvent | dict[str, object]) -> Self:
        if isinstance(value, MedicalEvent):
            return cls(
                id=value.id,
                family_member_id=value.family_member_id,
                mode=value.mode,
                situation=value.situation,
                event_date=value.event_date,
                visit_date=value.visit_date,
                facts={key: FactResponse.from_domain(item) for key, item in value.facts.items()},
                structured_facts=[
                    StructuredFactResponse.model_validate(item) for item in value.structured_facts
                ],
                optional_questions=[
                    OptionalQuestionResponse.model_validate(item)
                    for item in value.optional_questions
                ],
                version=value.version,
                deleted=value.deleted_at is not None,
            )
        return cls.model_validate(value)


ReasonCode = Annotated[
    StrictStr,
    Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$"),
]
VersionString = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
DecimalString = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=19,
        pattern=r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$",
    ),
]
CurrencyCode = Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")]
SafeLabel = Annotated[StrictStr, Field(min_length=1, max_length=800)]
BoundedCount = Annotated[StrictInt, Field(ge=0, le=10_000)]
FieldPath = Annotated[
    StrictStr,
    Field(
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}\.[A-Za-z][A-Za-z0-9_]{0,95}$",
    ),
]


def _wire_decimal(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise ValueError("decision amount must be a finite non-negative decimal")
    return format(value, "f")


class OperationalEvidenceCitationResponse(StrictModel):
    kind: Literal["OPERATIONAL_EVIDENCE"]
    evidence_id: UUID
    document_version_id: UUID
    extraction_id: UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_page: int = Field(ge=1, le=500)
    bbox: tuple[float, float, float, float] | None
    review_state: Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]

    @classmethod
    def from_domain(cls, value: EvidenceRef) -> Self:
        bbox = None if value.bbox is None else tuple(float(item) for item in value.bbox)
        return cls(
            kind="OPERATIONAL_EVIDENCE",
            evidence_id=value.evidence_id,
            document_version_id=value.document_version_id,
            extraction_id=value.extraction_id,
            content_sha256=value.content_sha256,
            physical_page=value.physical_page,
            bbox=cast(tuple[float, float, float, float] | None, bbox),
            review_state=value.review_state,
        )


class PrivateKnowledgeCitationResponse(StrictModel):
    kind: Literal["PRIVATE_KNOWLEDGE_CITATION"]
    terms_section_id: UUID
    source_clause_id: UUID | None
    fact_id: UUID | None
    evidence_purpose: ReasonCode
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)

    @model_validator(mode="after")
    def validate_page_range(self) -> Self:
        if self.page_end < self.page_start or self.page_end - self.page_start > 20:
            raise ValueError("private citation page range is invalid")
        return self

    @classmethod
    def from_domain(cls, value: KnowledgeCitation) -> Self:
        return cls(
            kind="PRIVATE_KNOWLEDGE_CITATION",
            terms_section_id=value.terms_section_id,
            source_clause_id=value.source_clause_id,
            fact_id=value.fact_id,
            evidence_purpose=value.evidence_purpose,
            page_start=value.page_start,
            page_end=value.page_end,
        )


class QuestionResponse(StrictModel):
    field_path: FieldPath
    reason_code: ReasonCode

    @classmethod
    def from_domain(cls, value: Question) -> Self:
        return cls(field_path=value.field_path, reason_code=value.reason_code)


class OperationalEvaluationSourceResponse(StrictModel):
    kind: Literal["OPERATIONAL_RIDER"]
    rider_id: UUID
    rule_version_id: UUID


class PrivateKnowledgeEvaluationSourceResponse(StrictModel):
    kind: Literal["PRIVATE_KNOWLEDGE_COVERAGE"]
    knowledge_coverage_id: UUID
    rule_publication_id: UUID


class EvaluationBaseResponse(StrictModel):
    evaluation_id: UUID
    result: TriState
    required: bool
    reason_code: ReasonCode
    fact_paths: list[FieldPath] = Field(max_length=32)
    missing_fields: list[FieldPath] = Field(max_length=32)
    conflicting_fields: list[FieldPath] = Field(max_length=32)
    engine_version: VersionString


class OperationalEvaluationResponse(EvaluationBaseResponse):
    source: OperationalEvaluationSourceResponse
    citations: list[OperationalEvidenceCitationResponse] = Field(min_length=1, max_length=16)

    @classmethod
    def from_domain(cls, value: RuleEvaluation) -> Self:
        if value.id is None:
            raise ValueError("evaluation id is required")
        return cls(
            evaluation_id=value.id,
            source=OperationalEvaluationSourceResponse(
                kind="OPERATIONAL_RIDER",
                rider_id=value.rider_id,
                rule_version_id=value.rule_version_id,
            ),
            result=value.result,
            required=value.required,
            reason_code=value.reason_code,
            fact_paths=list(value.fact_paths),
            missing_fields=list(value.missing_fields),
            conflicting_fields=list(value.conflicting_fields),
            citations=[
                OperationalEvidenceCitationResponse.from_domain(item) for item in value.evidence
            ],
            engine_version=value.evaluator_version,
        )


class PrivateKnowledgeEvaluationResponse(EvaluationBaseResponse):
    source: PrivateKnowledgeEvaluationSourceResponse
    citations: list[PrivateKnowledgeCitationResponse] = Field(min_length=1, max_length=16)

    @classmethod
    def from_domain(cls, value: KnowledgeRuleEvaluation) -> Self:
        return cls(
            evaluation_id=value.evaluation_id,
            source=PrivateKnowledgeEvaluationSourceResponse(
                kind="PRIVATE_KNOWLEDGE_COVERAGE",
                knowledge_coverage_id=value.knowledge_coverage_id,
                rule_publication_id=value.rule_publication_id,
            ),
            result=value.result,
            required=value.required,
            reason_code=value.reason_code,
            fact_paths=list(value.fact_paths),
            missing_fields=list(value.missing_fields),
            conflicting_fields=list(value.conflicting_fields),
            citations=[
                PrivateKnowledgeCitationResponse.from_domain(item) for item in value.citations
            ],
            engine_version=value.evaluator_version,
        )


class RuleEvaluationResponse(
    RootModel[OperationalEvaluationResponse | PrivateKnowledgeEvaluationResponse]
):
    @classmethod
    def from_operational(cls, value: RuleEvaluation) -> Self:
        return cls(root=OperationalEvaluationResponse.from_domain(value))

    @classmethod
    def from_private(cls, value: KnowledgeRuleEvaluation) -> Self:
        return cls(root=PrivateKnowledgeEvaluationResponse.from_domain(value))


class KnowledgeCalculationStepResponse(StrictModel):
    step_number: int = Field(ge=1, le=64)
    operation: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    input_amount: DecimalString | None
    output_amount: DecimalString | None
    currency: CurrencyCode | None
    rounding_rule: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None
    reason_code: ReasonCode

    @classmethod
    def from_domain(cls, value: KnowledgeCalculationStep) -> Self:
        return cls(
            step_number=value.step_number,
            operation=value.operation,
            input_amount=(
                _wire_decimal(value.input_amount) if value.input_amount is not None else None
            ),
            output_amount=(
                _wire_decimal(value.output_amount) if value.output_amount is not None else None
            ),
            currency=value.currency,
            rounding_rule=value.rounding_rule,
            reason_code=value.reason_code,
        )


class KnowledgeBenefitCalculationResponse(StrictModel):
    calculation_id: UUID
    calculation_publication_id: UUID | None
    kind: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    status: Literal["CALCULATED", "UNKNOWN", "NOT_APPLICABLE", "FAILED"]
    currency: CurrencyCode | None
    conditional_amount: DecimalString | None
    confirmed_amount: DecimalString | None
    excluded_amount: DecimalString | None
    deductible_amount: DecimalString | None
    applied_rate: DecimalString | None
    applied_limit: DecimalString | None
    rounding_rule: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None
    hold_reason_code: ReasonCode | None
    steps: list[KnowledgeCalculationStepResponse] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_calculation_state(self) -> Self:
        amounts = (
            self.conditional_amount,
            self.confirmed_amount,
            self.excluded_amount,
            self.deductible_amount,
            self.applied_limit,
        )
        if any(value is not None for value in amounts) and self.currency is None:
            raise ValueError("calculation amounts require a currency")
        if self.status == "CALCULATED" and (
            self.conditional_amount is None or self.currency is None
        ):
            raise ValueError("calculated benefit requires a conditional amount")
        if self.status != "CALCULATED" and self.conditional_amount is not None:
            raise ValueError("unresolved benefit cannot expose a conditional amount")
        if tuple(item.step_number for item in self.steps) != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("calculation steps must be contiguous")
        return self

    @classmethod
    def from_domain(cls, value: KnowledgeBenefitCalculation) -> Self:
        return cls(
            calculation_id=value.calculation_id,
            calculation_publication_id=value.calculation_publication_id,
            kind=value.kind,
            status=value.status,
            currency=value.currency,
            conditional_amount=(
                _wire_decimal(value.conditional_amount)
                if value.conditional_amount is not None
                else None
            ),
            confirmed_amount=(
                _wire_decimal(value.confirmed_amount)
                if value.confirmed_amount is not None
                else None
            ),
            excluded_amount=(
                _wire_decimal(value.excluded_amount) if value.excluded_amount is not None else None
            ),
            deductible_amount=(
                _wire_decimal(value.deductible_amount)
                if value.deductible_amount is not None
                else None
            ),
            applied_rate=(
                _wire_decimal(value.applied_rate) if value.applied_rate is not None else None
            ),
            applied_limit=(
                _wire_decimal(value.applied_limit) if value.applied_limit is not None else None
            ),
            rounding_rule=value.rounding_rule,
            hold_reason_code=value.hold_reason_code,
            steps=[KnowledgeCalculationStepResponse.from_domain(item) for item in value.steps],
        )


class OperationalCandidateSourceResponse(StrictModel):
    kind: Literal["OPERATIONAL_RIDER"]
    rider_id: UUID


class PrivateKnowledgeCandidateSourceResponse(StrictModel):
    kind: Literal["PRIVATE_KNOWLEDGE_COVERAGE"]
    knowledge_contract_id: UUID
    knowledge_coverage_id: UUID


class CandidateBaseResponse(StrictModel):
    candidate_id: UUID
    contract_label: SafeLabel
    coverage_label: SafeLabel
    benefit_kind: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    aggregate_result: TriState
    required_match_count: BoundedCount
    required_unknown_count: BoundedCount
    required_no_match_count: BoundedCount
    questions: list[QuestionResponse] = Field(max_length=32)
    hold_reason_codes: list[ReasonCode] = Field(max_length=32)


class OperationalCandidateResponse(CandidateBaseResponse):
    source: OperationalCandidateSourceResponse
    calculation: None
    claim_start_ready: bool

    @classmethod
    def from_domain(cls, value: ClaimCandidate) -> Self:
        if value.id is None or value.rider_type not in {"fixed", "indemnity"}:
            raise ValueError("persisted candidate identity and type are required")
        return cls(
            candidate_id=value.id,
            source=OperationalCandidateSourceResponse(
                kind="OPERATIONAL_RIDER",
                rider_id=value.rider_id,
            ),
            contract_label="등록 보험 계약",
            coverage_label=value.rider_label or "보험 담보",
            benefit_kind="FIXED" if value.rider_type == "fixed" else "INDEMNITY",
            aggregate_result=value.aggregate_result,
            required_match_count=value.required_match_count,
            required_unknown_count=value.required_unknown_count,
            required_no_match_count=value.required_no_match_count,
            questions=[QuestionResponse.from_domain(item) for item in value.questions],
            hold_reason_codes=list(value.hold_reason_codes),
            calculation=None,
            claim_start_ready=value.aggregate_result == "MATCH",
        )


class PrivateKnowledgeCandidateResponse(CandidateBaseResponse):
    source: PrivateKnowledgeCandidateSourceResponse
    calculation: KnowledgeBenefitCalculationResponse | None
    claim_start_ready: Literal[False]

    @classmethod
    def from_domain(
        cls,
        value: KnowledgeClaimCandidate,
        calculation: KnowledgeBenefitCalculation | None,
    ) -> Self:
        return cls(
            candidate_id=value.candidate_id,
            source=PrivateKnowledgeCandidateSourceResponse(
                kind="PRIVATE_KNOWLEDGE_COVERAGE",
                knowledge_contract_id=value.knowledge_contract_id,
                knowledge_coverage_id=value.knowledge_coverage_id,
            ),
            contract_label=value.contract_label,
            coverage_label=value.coverage_label,
            benefit_kind=value.benefit_type,
            aggregate_result=value.result,
            required_match_count=value.required_match_count,
            required_unknown_count=value.required_unknown_count,
            required_no_match_count=value.required_no_match_count,
            questions=[
                QuestionResponse(field_path=item.field_path, reason_code=item.reason_code)
                for item in value.questions
            ],
            hold_reason_codes=list(value.hold_reason_codes),
            calculation=(
                KnowledgeBenefitCalculationResponse.from_domain(calculation)
                if calculation is not None
                else None
            ),
            claim_start_ready=False,
        )


class ClaimCandidateResponse(
    RootModel[OperationalCandidateResponse | PrivateKnowledgeCandidateResponse]
):
    @classmethod
    def from_operational(cls, value: ClaimCandidate) -> Self:
        return cls(root=OperationalCandidateResponse.from_domain(value))

    @classmethod
    def from_private(
        cls,
        value: KnowledgeClaimCandidate,
        calculation: KnowledgeBenefitCalculation | None,
    ) -> Self:
        return cls(root=PrivateKnowledgeCandidateResponse.from_domain(value, calculation))


class KnowledgeSnapshotVersionResponse(StrictModel):
    catalog_import_run_id: UUID | None
    rule_import_run_id: UUID | None
    event_fact_schema_version: VersionString

    @model_validator(mode="after")
    def validate_snapshot_pair(self) -> Self:
        if self.rule_import_run_id is not None and self.catalog_import_run_id is None:
            raise ValueError("rule snapshot requires a catalog snapshot")
        return self


class CatalogCoverageResponse(StrictModel):
    contract_count: BoundedCount
    benefit_coverage_count: BoundedCount
    published_coverage_count: BoundedCount
    blocked_coverage_count: BoundedCount
    not_applicable_coverage_count: BoundedCount

    @model_validator(mode="after")
    def validate_disposition_counts(self) -> Self:
        if (
            self.published_coverage_count
            + self.blocked_coverage_count
            + self.not_applicable_coverage_count
            > self.benefit_coverage_count
        ):
            raise ValueError("catalog disposition counts exceed benefit coverage count")
        return self


class ConditionalFixedSubtotalResponse(StrictModel):
    currency: CurrencyCode
    amount: DecimalString
    calculated_candidate_count: BoundedCount
    unresolved_candidate_count: BoundedCount

    @classmethod
    def from_domain(cls, value: KnowledgeFixedSubtotal) -> Self:
        return cls(
            currency=value.currency,
            amount=_wire_decimal(value.amount),
            calculated_candidate_count=value.calculated_candidate_count,
            unresolved_candidate_count=value.unresolved_candidate_count,
        )


class IndemnitySummaryResponse(StrictModel):
    status: Literal["NONE", "CALCULATED", "UNKNOWN"]
    candidate_count: BoundedCount
    calculated_candidate_count: BoundedCount
    unresolved_candidate_count: BoundedCount

    @model_validator(mode="after")
    def validate_candidate_counts(self) -> Self:
        if self.calculated_candidate_count + self.unresolved_candidate_count > self.candidate_count:
            raise ValueError("indemnity summary counts exceed candidate count")
        if self.status == "NONE" and self.candidate_count != 0:
            raise ValueError("NONE indemnity summary must be empty")
        return self

    @classmethod
    def from_domain(cls, value: KnowledgeIndemnitySummary) -> Self:
        return cls(
            status=value.status,
            candidate_count=value.candidate_count,
            calculated_candidate_count=value.calculated_candidate_count,
            unresolved_candidate_count=value.unresolved_candidate_count,
        )


class AssistanceCitationResponse(StrictModel):
    kind: Literal["FACT_CITATION"]
    terms_section_id: UUID
    source_clause_id: UUID
    fact_id: UUID
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)

    @model_validator(mode="after")
    def validate_page_range(self) -> Self:
        if self.page_end < self.page_start or self.page_end - self.page_start > 20:
            raise ValueError("assistance citation page range is invalid")
        return self


class AnalysisRecommendationResponse(StrictModel):
    recommendation_id: UUID
    rank: int = Field(ge=1, le=12)
    contract_label: SafeLabel
    coverage_label: SafeLabel
    clause_label: SafeLabel
    excerpt: Annotated[StrictStr, Field(min_length=1, max_length=240)]
    reason_code: ReasonCode
    explanation_code: ReasonCode | None
    question_code: ReasonCode | None
    citation: AssistanceCitationResponse

    @classmethod
    def from_domain(cls, value: AnalysisRecommendation) -> Self:
        return cls(
            recommendation_id=value.id,
            rank=value.rank,
            contract_label=value.contract_label,
            coverage_label=value.coverage_label,
            clause_label=value.clause_label,
            excerpt=value.excerpt,
            reason_code=value.reason_code,
            explanation_code=value.explanation_code,
            question_code=value.question_code,
            citation=AssistanceCitationResponse(
                kind="FACT_CITATION",
                terms_section_id=value.terms_section_id,
                source_clause_id=value.source_clause_id,
                fact_id=value.knowledge_fact_id,
                page_start=value.page_start,
                page_end=value.page_end,
            ),
        )


class AnalysisAssistanceResponse(StrictModel):
    mode: Literal["STRUCTURED_SEARCH", "LLM_ASSISTED", "NONE"]
    state: Literal["SEARCH_READY", "LLM_PENDING", "LLM_READY"]
    outcome_code: ReasonCode
    model_label: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None
    recommendations: list[AnalysisRecommendationResponse] = Field(max_length=12)

    @model_validator(mode="after")
    def validate_mode_state(self) -> Self:
        if self.mode == "NONE" and (self.state != "SEARCH_READY" or self.recommendations):
            raise ValueError("NONE assistance must be an empty ready result")
        if self.mode == "LLM_ASSISTED":
            if self.state != "LLM_READY" or self.model_label is None:
                raise ValueError("LLM assistance requires ready model provenance")
        elif self.model_label is not None:
            raise ValueError("local assistance cannot expose a model label")
        if tuple(item.rank for item in self.recommendations) != tuple(
            range(1, len(self.recommendations) + 1)
        ):
            raise ValueError("assistance recommendation ranks must be contiguous")
        return self

    @classmethod
    def from_domain(cls, value: AnalysisAssistance | None) -> Self:
        if value is None:
            return cls(
                mode="NONE",
                state="SEARCH_READY",
                outcome_code="NO_ASSISTANCE",
                model_label=None,
                recommendations=[],
            )
        return cls(
            mode=value.mode,
            state=value.state,
            outcome_code=value.outcome_code,
            model_label=value.model_label,
            recommendations=[
                AnalysisRecommendationResponse.from_domain(item) for item in value.recommendations
            ],
        )


class CoverageDecisionResponse(StrictModel):
    schema_version: Literal["2"]
    run_id: UUID
    medical_event_id: UUID
    event_version: int = Field(ge=1)
    engine_version: VersionString
    rule_set_version: VersionString
    knowledge_snapshot_version: KnowledgeSnapshotVersionResponse
    policy_snapshot_at: datetime
    stale: bool
    analysis_completeness: Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]
    catalog_coverage: CatalogCoverageResponse
    candidates: list[ClaimCandidateResponse] = Field(max_length=128)
    evaluations: list[RuleEvaluationResponse] = Field(max_length=512)
    conditional_fixed_subtotals: list[ConditionalFixedSubtotalResponse] = Field(max_length=32)
    indemnity_summary: IndemnitySummaryResponse
    source_failure_codes: list[ReasonCode] = Field(max_length=32)
    assistance: AnalysisAssistanceResponse

    @classmethod
    def from_value(cls, value: DecisionRunResult | dict[str, object]) -> Self:
        if isinstance(value, DecisionRunResult):
            knowledge = value.knowledge_result
            calculations = (
                {item.candidate_id: item for item in knowledge.calculations}
                if knowledge is not None
                else {}
            )
            knowledge_candidates = knowledge.candidates if knowledge is not None else ()
            knowledge_evaluations = knowledge.evaluations if knowledge is not None else ()
            return cls(
                schema_version="2",
                run_id=value.run_id,
                medical_event_id=value.medical_event_id,
                event_version=value.event_version,
                engine_version=value.engine_version,
                rule_set_version=value.rule_set_version,
                knowledge_snapshot_version=KnowledgeSnapshotVersionResponse(
                    catalog_import_run_id=value.knowledge_import_run_id,
                    rule_import_run_id=value.knowledge_rule_import_run_id,
                    event_fact_schema_version=value.event_fact_schema_version,
                ),
                policy_snapshot_at=value.policy_snapshot_at,
                stale=value.stale,
                analysis_completeness=value.analysis_completeness,
                catalog_coverage=CatalogCoverageResponse(
                    contract_count=value.catalog_coverage.contract_count,
                    benefit_coverage_count=value.catalog_coverage.benefit_coverage_count,
                    published_coverage_count=value.catalog_coverage.published_coverage_count,
                    blocked_coverage_count=value.catalog_coverage.blocked_coverage_count,
                    not_applicable_coverage_count=(
                        value.catalog_coverage.not_applicable_coverage_count
                    ),
                ),
                candidates=[
                    ClaimCandidateResponse.from_operational(item) for item in value.candidates
                ]
                + [
                    ClaimCandidateResponse.from_private(
                        item,
                        calculations.get(item.candidate_id),
                    )
                    for item in knowledge_candidates
                ],
                evaluations=[
                    RuleEvaluationResponse.from_operational(item) for item in value.evaluations
                ]
                + [RuleEvaluationResponse.from_private(item) for item in knowledge_evaluations],
                conditional_fixed_subtotals=[
                    ConditionalFixedSubtotalResponse.from_domain(item)
                    for item in (knowledge.fixed_subtotals if knowledge is not None else ())
                ],
                indemnity_summary=IndemnitySummaryResponse.from_domain(
                    knowledge.indemnity_summary
                    if knowledge is not None
                    else KnowledgeIndemnitySummary(
                        status="NONE",
                        candidate_count=0,
                        calculated_candidate_count=0,
                        unresolved_candidate_count=0,
                    )
                ),
                source_failure_codes=list(value.source_failure_codes),
                assistance=AnalysisAssistanceResponse.from_domain(value.assistance),
            )
        return cls.model_validate(value)


class DecisionErrorResponse(StrictModel):
    error_code: str
    message: str


def _require_event_fields(facts: dict[str, FactInput]) -> None:
    if any(field not in _EVENT_FIELDS for field in facts):
        raise ValueError("unsupported fact field")
    classification = facts.get("MedicalEvent.classification")
    if classification is not None and classification.value is not None:
        value = classification.value
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError("invalid event classification")
    admission_days = facts.get("MedicalEvent.admission_days")
    if admission_days is not None and admission_days.value is not None:
        value = admission_days.value
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 36_500:
            raise ValueError("invalid admission days")


def _require_structured_facts(facts: list[StructuredFactInput]) -> None:
    if not facts or len({item.field_id for item in facts}) != len(facts):
        raise ValueError("structured fact fields must be unique")
    for item in facts:
        if not is_valid_structured_fact_value(item.field_id, item.value):
            raise ValueError("invalid structured fact value")


__all__ = [
    "CoverageDecisionResponse",
    "DecisionErrorResponse",
    "ExpectedVersionRequest",
    "FactInput",
    "MedicalEventCreateRequest",
    "MedicalEventResponse",
    "MedicalEventUpdateRequest",
    "StructuredFactInput",
]

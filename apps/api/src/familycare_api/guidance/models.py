"""Canonical models for persisted, transport-neutral local guidance snapshots."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.common.coverage_identity import CanonicalCoverageIdentity, CanonicalCoverageRef

Code = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")]
Label = Annotated[str, Field(min_length=1, max_length=800)]
Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=80)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
FactPath = Annotated[str, Field(min_length=1, max_length=160)]
Freshness = Literal["CONFIRMED_AT_EVENT", "DOCUMENT_CONTINUITY", "STATUS_UNRESOLVED"]
Number = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=162)]
Unit = Literal["MONEY", "DAYS", "COUNT", "RATIO", "NUMBER", "UNKNOWN"]
CalculationPath = Annotated[
    str, Field(max_length=512, pattern=r"^/calculation(?:/args/[0-9]{1,2})*$")
]


class GuidanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GuidanceEvidence(GuidanceModel):
    kind: Literal["TERMS_SECTION", "OPERATIONAL_EVIDENCE"]
    evidence_id: UUID
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)
    publication_id: UUID | None = None
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def ordered_pages(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("invalid evidence page order")
        return self


class GuidanceQuestion(GuidanceModel):
    field_path: FactPath
    reason_code: Code


class GuidanceSemanticEvidence(GuidanceModel):
    kind: Literal["SEMANTIC_CITATION"] = "SEMANTIC_CITATION"
    citation_id: UUID
    publication_id: UUID
    document_version_id: UUID
    terms_edition_id: UUID
    generation_id: UUID
    root_node_id: Annotated[str, Field(min_length=1, max_length=128)]
    source_node_id: Annotated[str, Field(min_length=1, max_length=128)]
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)
    start: int = Field(ge=0, le=262144)
    end: int = Field(ge=1, le=262144)
    source_layer: Literal["native", "ocr"]
    bbox: tuple[float, float, float, float]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def valid_address(self) -> Self:
        import math

        if (
            self.end <= self.start
            or self.page_start != self.page_end
            or any(not math.isfinite(n) or not 0 <= n <= 100000 for n in self.bbox)
            or self.bbox[0] > self.bbox[2]
            or self.bbox[1] > self.bbox[3]
        ):
            raise ValueError("invalid semantic citation address")
        return self


GuidanceEvidenceRef = GuidanceEvidence | GuidanceSemanticEvidence


class GuidanceEventSpan(GuidanceModel):
    start: int = Field(ge=0, le=2000)
    end: int = Field(ge=1, le=2000)

    @model_validator(mode="after")
    def ordered_span(self) -> Self:
        if self.start >= self.end:
            raise ValueError("invalid event span")
        return self


class GuidanceRelevance(GuidanceModel):
    kind: Literal["CONFIRMED_EVENT", "LOCAL_TOPIC", "PLANNED_EVENT"]
    field_path: FactPath
    publication_id: UUID
    rule_key: Annotated[str, Field(min_length=1, max_length=160)]
    source_kind: Literal["PRIVATE_RULE_PUBLICATION", "OPERATIONAL_RULE_VERSION", "SEMANTIC_NODE"]
    semantic_node_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    spans: tuple[GuidanceEventSpan, ...] = Field(default=(), max_length=128)
    normalizer_key: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    evidence: tuple[GuidanceEvidenceRef, ...] = Field(min_length=1, max_length=64)


class GuidanceCondition(GuidanceModel):
    rule_id: UUID | None = None
    semantic_publication_id: UUID | None = None
    semantic_node_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    result: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    reason_code: Code
    evidence: tuple[GuidanceEvidenceRef, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def actual_rule_identity(self) -> Self:
        if self.rule_id is None:
            if self.semantic_publication_id is None or self.semantic_node_id is None:
                raise ValueError("semantic condition requires its actual source identity")
        elif self.semantic_publication_id is not None or self.semantic_node_id is not None:
            raise ValueError("condition source identity is ambiguous")
        return self


class GuidanceSourceReference(GuidanceModel):
    source_kind: Code
    source_id: Annotated[str, Field(min_length=1, max_length=256)]
    version: (
        Annotated[int, Field(ge=1, strict=True)]
        | Annotated[str, Field(min_length=1, max_length=128)]
        | None
    ) = None
    digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class GuidancePrivateCertificate(GuidanceModel):
    kind: Literal["PRIVATE_CERTIFICATE"] = "PRIVATE_CERTIFICATE"
    catalog_import_run_id: UUID
    coverage_id: UUID
    document_alias: Label
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)

    @model_validator(mode="after")
    def ordered_pages(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("invalid certificate page order")
        return self


class GuidanceContractAmount(GuidanceModel):
    amount: Money | None = None
    currency: Currency | None = None
    amount_authority: Literal[
        "PROGRAM_VERIFIED", "USER_CONFIRMED", "DOCUMENT_REVIEWED", "UNCONFIRMED"
    ]
    currency_authority: Literal[
        "PROGRAM_VERIFIED", "USER_CONFIRMED", "DOCUMENT_REVIEWED", "UNCONFIRMED"
    ]
    source_refs: tuple[GuidanceSourceReference, ...] = Field(default=(), max_length=64)
    evidence: tuple[GuidanceEvidence | GuidancePrivateCertificate, ...] = Field(
        default=(), max_length=128
    )


class GuidanceCalculationOperand(GuidanceModel):
    expression_path: CalculationPath
    kind: Literal["FIELD", "LITERAL", "CHILD"]
    field_path: FactPath | None = None
    child_path: CalculationPath | None = None
    value: Number | None = None
    supplied_value: Number | None = None
    unit: Unit
    currency: Currency | None = None
    provenance: Code | None = None
    source_refs: tuple[GuidanceSourceReference, ...] = Field(default=(), max_length=64)
    status: Literal["AVAILABLE", "UNAVAILABLE", "FAILED"]
    reason_codes: tuple[Code, ...] = Field(default=(), max_length=32)
    stale: bool = False


class GuidanceCalculationStep(GuidanceModel):
    step_number: int = Field(ge=1, le=256)
    expression_path: CalculationPath
    operation: Literal["add", "subtract", "multiply", "min", "max", "round"]
    operands: tuple[GuidanceCalculationOperand, ...] = Field(min_length=1, max_length=16)
    value: Number | None = None
    unit: Unit
    currency: Currency | None = None
    rounding_rule: Literal["half_up", "half_even", "up", "down"] | None = None
    unit_source_refs: tuple[GuidanceSourceReference, ...] = Field(default=(), max_length=64)
    status: Literal["AVAILABLE", "UNAVAILABLE", "FAILED"]
    reason_codes: tuple[Code, ...] = Field(default=(), max_length=32)


class GuidanceCalculatedComponent(GuidanceModel):
    parent_path: CalculationPath
    expression_path: CalculationPath
    value: Number
    unit: Unit
    currency: Currency | None = None


class GuidanceCalculationTrace(GuidanceModel):
    publication_id: UUID
    source_revision: Annotated[str, Field(min_length=1, max_length=128)]
    source_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    formula_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    runtime_revision: Annotated[str, Field(min_length=1, max_length=128)]
    source_refs: tuple[GuidanceSourceReference, ...] = Field(default=(), max_length=64)
    status: Literal["COMPLETE", "PARTIAL", "UNAVAILABLE", "FAILED"]
    value: Number | None = None
    unit: Unit
    currency: Currency | None = None
    steps: tuple[GuidanceCalculationStep, ...] = Field(default=(), max_length=256)
    missing_paths: tuple[FactPath, ...] = Field(default=(), max_length=64)
    calculated_components: tuple[GuidanceCalculatedComponent, ...] = Field(
        default=(), max_length=256
    )
    reason_codes: tuple[Code, ...] = Field(default=(), max_length=32)


class GuidanceEstimate(GuidanceModel):
    kind: Literal["POINT", "RANGE", "FORMULA", "UNAVAILABLE"]
    currency: Currency | None = None
    amount: Money | None = None
    lower: Money | None = None
    upper: Money | None = None
    formula: Annotated[str, Field(max_length=4000)] | None = None
    missing_inputs: tuple[FactPath, ...] = Field(default=(), max_length=64)
    assumptions: tuple[Code, ...] = Field(default=(), max_length=32)
    reason_code: Code
    evidence: tuple[GuidanceEvidenceRef, ...] = Field(default=(), max_length=64)
    basis: Literal[
        "DOCUMENT_FORMULA",
        "REGISTERED_COSTS",
        "CONFIRMED_COST_SUBSET",
        "USER_SCENARIO",
        "SOURCE_ALTERNATIVES",
    ] = "DOCUMENT_FORMULA"
    partial_amount: Money | None = None
    trace: GuidanceCalculationTrace | None = None

    @model_validator(mode="after")
    def amount_requires_formula_and_evidence(self) -> Self:
        from decimal import Decimal

        if self.kind == "POINT":
            if (
                self.amount is None
                or self.currency is None
                or not self.formula
                or not self.evidence
            ):
                raise ValueError("point estimate requires formula, evidence and currency")
        elif self.amount is not None:
            raise ValueError("only point estimates have a point amount")
        if self.kind == "RANGE":
            if (
                self.lower is None
                or self.upper is None
                or self.currency is None
                or not self.formula
                or not self.evidence
                or Decimal(self.lower) > Decimal(self.upper)
            ):
                raise ValueError("range requires ordered bounds and evidence")
        elif self.lower is not None or self.upper is not None:
            raise ValueError("only range estimates have bounds")
        if self.kind == "FORMULA" and (not self.formula or not self.evidence):
            raise ValueError("formula estimate requires evidence")
        if self.partial_amount is not None and (
            self.kind != "FORMULA"
            or self.currency is None
            or self.basis != "CONFIRMED_COST_SUBSET"
            or self.trace is None
        ):
            raise ValueError("partial amount requires its cost-subset formula and trace")
        return self


class GuidanceHypothesis(GuidanceModel):
    field_path: FactPath
    value: bool | int | str
    provenance: Literal["SCENARIO_ASSUMPTION"] = "SCENARIO_ASSUMPTION"
    spans: tuple[GuidanceEventSpan, ...] = Field(min_length=1, max_length=32)
    source_refs: tuple[GuidanceSourceReference, ...] = Field(min_length=1, max_length=64)


class GuidanceScenario(GuidanceModel):
    scenario_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    kind: Literal["PLANNED_CARE"]
    hypotheses: tuple[GuidanceHypothesis, ...] = Field(min_length=1, max_length=32)
    estimate: GuidanceEstimate

    @model_validator(mode="after")
    def explicit_assumed_estimate(self) -> Self:
        if (
            self.estimate.basis != "USER_SCENARIO"
            or "PLANNED_CARE_ASSUMED" not in self.estimate.assumptions
        ):
            raise ValueError("scenario estimate requires its explicit assumptions")
        return self


class GuidancePayoutCase(GuidanceModel):
    case_key: Annotated[str, Field(min_length=1, max_length=256)]
    benefit_kind: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    condition_result: Literal["MATCH", "UNKNOWN"]
    reason_codes: tuple[Code, ...] = Field(min_length=1, max_length=32)
    assumptions: tuple[Code, ...] = Field(default=(), max_length=32)
    conditions: tuple[GuidanceCondition, ...] = Field(default=(), max_length=128)
    relevance: tuple[GuidanceRelevance, ...] = Field(default=(), max_length=64)
    estimate: GuidanceEstimate
    scenarios: tuple[GuidanceScenario, ...] = Field(default=(), max_length=32)
    questions: tuple[GuidanceQuestion, ...] = Field(default=(), max_length=64)


class GuidanceCandidate(GuidanceModel):
    ref: CanonicalCoverageRef
    canonical_identity: CanonicalCoverageIdentity | None = None
    contract_label: Label
    coverage_label: Label
    benefit_kind: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    group: Literal["PRIMARY", "CONDITIONAL"]
    enrollment: Literal["DOCUMENTED"] = "DOCUMENTED"
    freshness: Freshness
    condition_result: Literal["MATCH", "UNKNOWN"]
    reason_codes: tuple[Code, ...] = Field(min_length=1, max_length=32)
    assumptions: tuple[Code, ...] = Field(default=(), max_length=32)
    conditions: tuple[GuidanceCondition, ...] = Field(default=(), max_length=128)
    relevance: tuple[GuidanceRelevance, ...] = Field(default=(), max_length=64)
    contract_amount: GuidanceContractAmount | None = None
    estimate: GuidanceEstimate
    scenarios: tuple[GuidanceScenario, ...] = Field(default=(), max_length=32)
    cases: tuple[GuidancePayoutCase, ...] = Field(default=(), max_length=32)
    case_relation: Literal["MUTUALLY_EXCLUSIVE", "UNRESOLVED"] = "UNRESOLVED"
    questions: tuple[GuidanceQuestion, ...] = Field(default=(), max_length=64)


class GuidanceVersions(GuidanceModel):
    engine: Literal["local-guidance-v1", "local-guidance-v2"] = "local-guidance-v1"
    assumption_policy: Literal["document-continuity-v1"] = "document-continuity-v1"
    catalog_import_run_id: UUID | None = None
    rule_import_run_id: UUID | None = None
    status_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class GuidanceSupport(GuidanceModel):
    total_coverages: int = Field(ge=0)
    evaluated_coverages: int = Field(ge=0)
    unsupported_coverages: int = Field(ge=0)
    failure_codes: tuple[Code, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def counts_partition_input(self) -> Self:
        if self.evaluated_coverages + self.unsupported_coverages != self.total_coverages:
            raise ValueError("support counts must cover all input coverages")
        return self


class GuidanceCostGroup(GuidanceModel):
    unit: Literal["COST"] = "COST"
    line_ids: tuple[UUID, ...] = Field(max_length=256)
    known_line_ids: tuple[UUID, ...] = Field(max_length=256)
    unknown_amount_line_ids: tuple[UUID, ...] = Field(max_length=256)
    known_cost: Money | None
    total_cost: Money | None
    source_refs: tuple[GuidanceSourceReference, ...] = Field(max_length=64)


class GuidanceCurrencyCosts(GuidanceModel):
    currency: Currency
    covered: GuidanceCostGroup
    excluded: GuidanceCostGroup
    coverage_review: GuidanceCostGroup
    unconfirmed: GuidanceCostGroup


class GuidanceExpenses(GuidanceModel):
    event_id: UUID
    event_version: int | None = Field(ge=1)
    status: Literal["AVAILABLE", "EMPTY", "PARTIAL", "UNAVAILABLE"]
    reader_revision: str = Field(min_length=1, max_length=128)
    digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    currencies: tuple[GuidanceCurrencyCosts, ...] = Field(max_length=256)
    unassigned_line_ids: tuple[UUID, ...] = Field(max_length=256)
    reason_codes: tuple[Code, ...] = Field(max_length=32)


class LocalGuidanceResponse(GuidanceModel):
    schema_version: Literal["1", "2"] = "1"
    family_member_id: UUID
    medical_event_id: UUID
    event_version: int = Field(ge=1)
    event_date: date | None
    outcome: Literal["CANDIDATES", "NO_RELEVANT_COVERAGE", "INPUT_UNRESOLVED", "KNOWLEDGE_PENDING"]
    versions: GuidanceVersions
    candidates: tuple[GuidanceCandidate, ...] = Field(max_length=1000)
    support: GuidanceSupport
    expenses: GuidanceExpenses | None = None
    review_state: Literal["NOT_REQUESTED"] = "NOT_REQUESTED"

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        keys = [(item.ref.kind, item.ref.coverage_id) for item in self.candidates]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate guidance coverage")
        if bool(self.candidates) != (self.outcome == "CANDIDATES"):
            raise ValueError("guidance outcome differs from candidate presence")
        return self

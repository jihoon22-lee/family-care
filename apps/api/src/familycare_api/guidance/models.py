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


class GuidanceCondition(GuidanceModel):
    rule_id: UUID
    result: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    reason_code: Code
    evidence: tuple[GuidanceEvidence, ...] = Field(max_length=64)


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
    evidence: tuple[GuidanceEvidence, ...] = Field(default=(), max_length=64)

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
        return self


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
    estimate: GuidanceEstimate
    questions: tuple[GuidanceQuestion, ...] = Field(default=(), max_length=64)


class GuidanceVersions(GuidanceModel):
    engine: Literal["local-guidance-v1"] = "local-guidance-v1"
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


class LocalGuidanceResponse(GuidanceModel):
    schema_version: Literal["1"] = "1"
    family_member_id: UUID
    medical_event_id: UUID
    event_version: int = Field(ge=1)
    event_date: date | None
    outcome: Literal["CANDIDATES", "NO_RELEVANT_COVERAGE", "INPUT_UNRESOLVED", "KNOWLEDGE_PENDING"]
    versions: GuidanceVersions
    candidates: tuple[GuidanceCandidate, ...] = Field(max_length=1000)
    support: GuidanceSupport
    review_state: Literal["NOT_REQUESTED"] = "NOT_REQUESTED"

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        keys = [(item.ref.kind, item.ref.coverage_id) for item in self.candidates]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate guidance coverage")
        if bool(self.candidates) != (self.outcome == "CANDIDATES"):
            raise ValueError("guidance outcome differs from candidate presence")
        return self

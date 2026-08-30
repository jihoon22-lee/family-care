"""Bounded HTTP schemas for current private insurance knowledge."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from familycare_api.private_knowledge.reconciliation import KnowledgeEntityCounts

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
TriState = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=240)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=8_000)]


class PrivateKnowledgeErrorResponse(BaseModel):
    model_config = _STRICT

    error_code: str
    message: str
    fields: list[str] | None = None


class CurrentKnowledgeResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    run_id: UUID
    counts: KnowledgeEntityCounts
    executable_fact_count: int = Field(ge=0)
    executable_mapping_count: int = Field(ge=0)
    unsafe_operational_binding_count: int = Field(ge=0)


class KnowledgeContractListItemResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    subject_id: UUID
    family_alias: str = Field(min_length=1, max_length=240)
    insurer_display: str = Field(min_length=1, max_length=240)
    product_display: str = Field(min_length=1, max_length=800)
    contract_start: date | None
    contract_end: date | None
    certificate_decision: TriState
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    coverage_count: int = Field(ge=0)
    enrollment_match_count: int = Field(ge=0)
    enrollment_no_match_count: int = Field(ge=0)
    enrollment_unknown_count: int = Field(ge=0)
    document_identity_decision: TriState
    edition_applicability_decision: TriState
    terms_overall_decision: TriState


class KnowledgeContractPageResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    items: tuple[KnowledgeContractListItemResponse, ...] = Field(max_length=100)
    next_cursor: UUID | None


class KnowledgeCoverageResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    display_name: str = Field(min_length=1, max_length=800)
    component_role: Literal["MAIN_CONTRACT", "RIDER"]
    component_classification: Literal[
        "BENEFIT_COVERAGE",
        "NON_BENEFIT_CONTRACT_COMPONENT",
        "UNKNOWN",
    ]
    enrollment_decision: TriState
    benefit_type: Literal["FIXED", "INDEMNITY", "UNKNOWN", "NOT_APPLICABLE"]
    insured_amount: Decimal | None = Field(ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    coverage_start: date | None
    coverage_end: date | None
    renewal_state: Literal["YES", "NO", "UNKNOWN", "NOT_APPLICABLE"]
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]


class KnowledgeTermsAssignmentResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    document_identity_decision: TriState
    edition_applicability_decision: TriState
    overall_decision: TriState
    reason_codes: tuple[ShortText, ...] = Field(max_length=64)
    selected_source_count: int = Field(ge=0, le=8)


class KnowledgeCoverageMappingResponse(BaseModel):
    model_config = _STRICT

    coverage_id: UUID
    terms_section_id: UUID | None
    mapping_applicability: Literal["APPLICABLE", "NOT_APPLICABLE", "UNKNOWN"]
    enrollment_decision: TriState
    document_identity_decision: TriState
    edition_applicability_decision: TriState
    section_mapping_decision: TriState
    overall_decision: TriState
    reason_codes: tuple[ShortText, ...] = Field(max_length=64)
    executable: Literal[False]


class KnowledgeFactCitationResponse(BaseModel):
    model_config = _STRICT

    source_document_ref: UUID
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    clause_label: str | None = Field(default=None, max_length=800)
    clause_title: str | None = Field(default=None, max_length=800)


class KnowledgeFactConditionsResponse(BaseModel):
    model_config = _STRICT

    details_ko: tuple[LongText, ...] = Field(max_length=64)
    decision_impact: ShortText
    confidence: Literal["high", "medium"]
    unresolved_reference: bool


class KnowledgeFactResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    fact_type: Literal[
        "PAYMENT_TRIGGER",
        "DEFINITION",
        "EXCLUSION",
        "WAITING_PERIOD",
        "REDUCTION",
        "FREQUENCY",
        "AMOUNT",
        "RENEWAL",
        "REQUIRED_DOCUMENT",
        "TERMINATION",
        "CROSS_REFERENCE",
        "OTHER",
    ]
    statement: str = Field(min_length=1, max_length=8_000)
    conditions: KnowledgeFactConditionsResponse
    numeric_terms: tuple[LongText, ...] = Field(max_length=64)
    review_state: Literal[
        "DIRECT_REVIEWED",
        "NEEDS_REVIEW",
        "USER_CONFIRMED",
        "UNKNOWN",
    ]
    executable: Literal[False]
    citations: tuple[KnowledgeFactCitationResponse, ...] = Field(max_length=32)


class KnowledgeTermsSectionResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    heading: str = Field(min_length=1, max_length=800)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    review_state: Literal[
        "DIRECT_REVIEWED",
        "NEEDS_REVIEW",
        "USER_CONFIRMED",
        "UNKNOWN",
    ]
    section_summary: str = Field(min_length=1, max_length=8_000)
    confidence: Literal["high", "medium"]
    found_categories: tuple[ShortText, ...] = Field(max_length=32)
    missing_categories: tuple[ShortText, ...] = Field(max_length=32)
    warnings: tuple[ShortText, ...] = Field(max_length=128)
    facts: tuple[KnowledgeFactResponse, ...] = Field(max_length=1_000)


class KnowledgeContractDetailResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    contract: KnowledgeContractListItemResponse
    coverages: tuple[KnowledgeCoverageResponse, ...] = Field(max_length=256)
    terms_assignments: tuple[KnowledgeTermsAssignmentResponse, ...] = Field(max_length=8)
    coverage_mappings: tuple[KnowledgeCoverageMappingResponse, ...] = Field(max_length=256)
    terms_sections: tuple[KnowledgeTermsSectionResponse, ...] = Field(max_length=50)
    next_section_cursor: UUID | None

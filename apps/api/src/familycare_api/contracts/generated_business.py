"""Generated from packages/contracts/schemas; do not edit manually."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

__all__ = [
    "AggregateId",
    "BenefitType",
    "CandidateConfirmationRequest",
    "CandidateCorrectionRequest",
    "CandidateErrorCode",
    "CandidateErrorResponse",
    "CandidateField",
    "CandidateIssueCode",
    "CandidateKind",
    "CandidateRejectionReason",
    "CandidateRejectionRequest",
    "CandidateScalar",
    "CandidateStatus",
    "CandidateVersionId",
    "DocumentVersionId",
    "Evidence",
    "EvidenceId",
    "EvidenceRef",
    "EvidenceReviewState",
    "FamilyMemberId",
    "FamilyMemberRecord",
    "KnowledgeContractDetailResponse",
    "KnowledgeContractListItemResponse",
    "KnowledgeCoverageMappingResponse",
    "KnowledgeCoverageResponse",
    "KnowledgeFactCitationResponse",
    "KnowledgeFactConditionsResponse",
    "KnowledgeFactResponse",
    "KnowledgeTermsAssignmentResponse",
    "KnowledgeTermsSectionResponse",
    "PartyRole",
    "PolicyApiErrorCode",
    "PolicyCandidate",
    "PolicyCandidateBatch",
    "PolicyCandidateFieldId",
    "PolicyErrorCode",
    "PolicyId",
    "PolicyLedger",
    "PolicyPartyId",
    "PolicyPartyRecord",
    "PolicyRecord",
    "PolicyReviewItem",
    "PolicyStatus",
    "PositiveVersion",
    "ReviewIssue",
    "ReviewItemId",
    "RiderId",
    "RiderRecord",
]


AggregateId = str


BenefitType = Literal[
    "fixed",
    "indemnity",
]


CandidateErrorCode = Literal[
    "INVALID_CANDIDATE_CORRECTION",
    "REVIEW_ITEM_NOT_FOUND",
    "VERSION_CONFLICT",
]


CandidateIssueCode = Literal[
    "COMMON_SPECIAL_TERMS_CONFLICT",
    "CONFLICTING_EVIDENCE",
    "INVALID_DATE",
    "INVALID_UNIT",
    "LOW_CONFIDENCE",
    "MISSING_EVIDENCE",
    "STALE_EVIDENCE",
    "TERMS_ONLY_RIDER",
    "UNSUPPORTED_DSL",
    "UNSUPPORTED_STRUCTURE",
    "WRONG_EDITION",
]


CandidateKind = Literal[
    "coverage_rule",
    "policy_contract",
    "policy_party",
    "rider",
    "rider_clause",
]


CandidateRejectionReason = Literal[
    "DUPLICATE_CANDIDATE",
    "INVALID_EVIDENCE",
    "NOT_ENROLLED",
    "TERMS_ONLY_RIDER",
    "UNSUPPORTED_STRUCTURE",
]


CandidateScalar = str | float | bool | None


CandidateStatus = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
    "rejected",
]


CandidateVersionId = str


DocumentVersionId = str


EvidenceId = str


EvidenceReviewState = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
]


FamilyMemberId = str


PartyRole = Literal[
    "additional_insured",
    "beneficiary",
    "policyholder",
    "primary_insured",
]


PolicyApiErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "EVIDENCE_INVALID",
    "FAMILY_MEMBER_NOT_FOUND",
    "INVALID_REQUEST",
    "POLICY_NOT_FOUND",
    "POLICY_STATE_CONFLICT",
    "RESOURCE_LIMIT_EXCEEDED",
    "VERSION_CONFLICT",
]


PolicyCandidateFieldId = Literal[
    "benefit_type",
    "clause_id",
    "contract_end",
    "contract_start",
    "coverage_end",
    "coverage_start",
    "currency",
    "date_boundary",
    "decimal_boundary",
    "fact_field",
    "insurer",
    "link_review_state",
    "policy_status",
    "product_name",
    "renewable",
    "required",
    "rider_id",
    "rider_key",
    "rider_name",
    "rider_status",
    "rule_kind",
    "rule_operator",
    "sum_assured",
    "terms_edition_id",
    "unit",
]


PolicyErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "EVIDENCE_INVALID",
    "FAMILY_MEMBER_NOT_FOUND",
    "POLICY_NOT_FOUND",
    "POLICY_STATE_CONFLICT",
    "VERSION_CONFLICT",
]


PolicyId = str


PolicyPartyId = str


PolicyStatus = Literal[
    "active",
    "cancelled",
    "expired",
    "inactive",
    "unknown",
]


PositiveVersion = int


ReviewItemId = str


RiderId = str


class CandidateConfirmationRequest(TypedDict):
    expected_version: PositiveVersion


class CandidateCorrectionRequest(TypedDict):
    evidence_id: EvidenceId
    expected_version: PositiveVersion
    field_id: PolicyCandidateFieldId
    value: CandidateScalar


class CandidateErrorResponse(TypedDict):
    error_code: CandidateErrorCode
    message: str


class CandidateField(TypedDict):
    evidence_ids: list[EvidenceId]
    field_id: PolicyCandidateFieldId
    value: CandidateScalar


class CandidateRejectionRequest(TypedDict):
    expected_version: PositiveVersion
    reason_code: CandidateRejectionReason


class Evidence(TypedDict):
    bbox: list[float] | None
    content_sha256: str
    document_version_id: DocumentVersionId
    evidence_id: EvidenceId
    physical_page: int
    review_state: EvidenceReviewState


class EvidenceRef(TypedDict):
    bbox: list[float] | None
    bounded_excerpt: str
    document_label: str
    document_version_id: DocumentVersionId
    evidence_id: EvidenceId
    page: int


class FamilyMemberRecord(TypedDict):
    deleted: bool
    display_name: str
    id: FamilyMemberId
    internal_alias: str
    version: PositiveVersion


class KnowledgeContractDetailResponse(TypedDict):
    contract: KnowledgeContractListItemResponse
    coverage_mappings: list[KnowledgeCoverageMappingResponse]
    coverages: list[KnowledgeCoverageResponse]
    next_section_cursor: str | None
    schema_version: Literal["1"]
    terms_assignments: list[KnowledgeTermsAssignmentResponse]
    terms_sections: list[KnowledgeTermsSectionResponse]


class KnowledgeContractListItemResponse(TypedDict):
    certificate_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    contract_document_completeness: Literal[
        "CERTIFICATE_AND_TERMS",
        "CERTIFICATE_ONLY",
        "CERTIFICATE_REVIEW_REQUIRED_AND_TERMS",
        "UNVERIFIED",
    ]
    contract_end: str | None
    contract_start: str | None
    coverage_count: int
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    current_status_as_of: str | None
    current_status_authority: Literal["USER_CONFIRMED_CURRENT_ENROLLMENT"] | None
    current_status_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    document_identity_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    edition_applicability_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    enrollment_match_count: int
    enrollment_no_match_count: int
    enrollment_unknown_count: int
    family_alias: str
    family_member_id: str | None
    id: str
    insurer_display: str
    product_display: str
    semantic_fact_count: int
    semantic_section_count: int
    subject_binding_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    subject_id: str
    terms_overall_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    terms_source_count: int


class KnowledgeCoverageMappingResponse(TypedDict):
    coverage_id: str
    document_identity_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    edition_applicability_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    enrollment_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    executable: Literal[False]
    mapping_applicability: Literal["APPLICABLE", "NOT_APPLICABLE", "UNKNOWN"]
    overall_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    reason_codes: list[str]
    section_mapping_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    terms_section_id: str | None


class KnowledgeCoverageResponse(TypedDict):
    benefit_type: Literal["FIXED", "INDEMNITY", "NOT_APPLICABLE", "UNKNOWN"]
    component_classification: Literal[
        "BENEFIT_COVERAGE", "NON_BENEFIT_CONTRACT_COMPONENT", "UNKNOWN"
    ]
    component_role: Literal["MAIN_CONTRACT", "RIDER"]
    coverage_end: str | None
    coverage_start: str | None
    currency: NotRequired[str | None]
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    display_name: str
    enrollment_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    id: str
    insured_amount: str | None
    renewal_state: Literal["NO", "NOT_APPLICABLE", "UNKNOWN", "YES"]


class KnowledgeFactCitationResponse(TypedDict):
    clause_label: NotRequired[str | None]
    clause_title: NotRequired[str | None]
    page_end: int
    page_start: int
    source_document_ref: str


class KnowledgeFactConditionsResponse(TypedDict):
    confidence: Literal["high", "medium"]
    decision_impact: str
    details_ko: list[str]
    unresolved_reference: bool


class KnowledgeFactResponse(TypedDict):
    citations: list[KnowledgeFactCitationResponse]
    conditions: KnowledgeFactConditionsResponse
    executable: Literal[False]
    fact_type: Literal[
        "AMOUNT",
        "CROSS_REFERENCE",
        "DEFINITION",
        "EXCLUSION",
        "FREQUENCY",
        "OTHER",
        "PAYMENT_TRIGGER",
        "REDUCTION",
        "RENEWAL",
        "REQUIRED_DOCUMENT",
        "TERMINATION",
        "WAITING_PERIOD",
    ]
    id: str
    numeric_terms: list[str]
    review_state: Literal["DIRECT_REVIEWED", "NEEDS_REVIEW", "UNKNOWN", "USER_CONFIRMED"]
    statement: str


class KnowledgeTermsAssignmentResponse(TypedDict):
    document_identity_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    edition_applicability_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    id: str
    overall_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    reason_codes: list[str]
    selected_source_count: int


class KnowledgeTermsSectionResponse(TypedDict):
    confidence: Literal["high", "medium"]
    facts: list[KnowledgeFactResponse]
    found_categories: list[str]
    heading: str
    id: str
    missing_categories: list[str]
    page_end: int
    page_start: int
    review_state: Literal["DIRECT_REVIEWED", "NEEDS_REVIEW", "UNKNOWN", "USER_CONFIRMED"]
    section_summary: str
    warnings: list[str]


class PolicyCandidate(TypedDict):
    aggregate_id: AggregateId | None
    candidate_kind: CandidateKind
    candidate_version_id: CandidateVersionId
    evidence: list[EvidenceRef]
    expected_version: PositiveVersion
    fields: list[CandidateField]
    issues: list[ReviewIssue]
    status: CandidateStatus


class PolicyCandidateBatch(TypedDict):
    candidates: list[PolicyCandidate]
    schema_version: Literal["1"]


class PolicyLedger(TypedDict):
    evidence: Evidence
    family_member_id: FamilyMemberId
    policy_id: PolicyId
    rider_id: RiderId
    schema_version: Literal["1"]
    status: PolicyStatus
    version: PositiveVersion


class PolicyPartyRecord(TypedDict):
    effective_from: str | None
    effective_to: str | None
    evidence: Evidence
    family_member_id: FamilyMemberId
    id: PolicyPartyId
    role: PartyRole
    version: PositiveVersion


class PolicyRecord(TypedDict):
    contract_date: str | None
    coverage_end_date: str | None
    coverage_start_date: str | None
    deleted: bool
    id: PolicyId
    insurer_display: str
    insurer_key: str
    parties: list[PolicyPartyRecord]
    product_display: str
    product_key: str
    source_document_version_id: DocumentVersionId
    source_evidence: Evidence
    status: PolicyStatus
    status_evidence: Evidence | None
    version: PositiveVersion


class PolicyReviewItem(TypedDict):
    aggregate_id: AggregateId | None
    candidate_kind: CandidateKind
    candidate_version_id: CandidateVersionId
    evidence: list[EvidenceRef]
    expected_version: PositiveVersion
    fields: list[CandidateField]
    issues: list[ReviewIssue]
    review_item_id: ReviewItemId
    status: CandidateStatus


class ReviewIssue(TypedDict):
    code: CandidateIssueCode
    field_id: PolicyCandidateFieldId | None


class RiderRecord(TypedDict):
    benefit_type: BenefitType
    coverage_end_date: str | None
    coverage_start_date: str | None
    currency: str | None
    display_name: str
    id: RiderId
    insured_amount: str | None
    normalized_key: str
    policy_contract_id: PolicyId
    renewable: bool | None
    source_evidence: Evidence
    status: PolicyStatus
    status_evidence: Evidence | None
    version: PositiveVersion

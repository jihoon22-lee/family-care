"""Strict models for private-analysis-package.sol-v2."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


def _private_text(value: str) -> str:
    if "\x00" in value or not value.strip():
        raise ValueError("invalid private text")
    return value


def _database_number(value: int | float) -> int | float:
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid database number") from None
    exponent = decimal_value.as_tuple().exponent
    if (
        decimal_value < 0
        or decimal_value > Decimal("9999999999999999.9999")
        or not isinstance(exponent, int)
        or exponent < -4
    ):
        raise ValueError("database number exceeds Numeric(20,4)")
    return value


ShortText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=240),
    AfterValidator(_private_text),
]
MediumText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=800),
    AfterValidator(_private_text),
]
LongText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=8_000),
    AfterValidator(_private_text),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
IsoDate = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositivePage = Annotated[int, Field(ge=1)]
BoundedInt = Annotated[int, Field(ge=-(10**18), le=10**18)]
BoundedFloat = Annotated[float, Field(ge=-(10**18), le=10**18, allow_inf_nan=False)]
UnitScore = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
NonNegativeNumber = Annotated[
    int | float,
    Field(ge=0, allow_inf_nan=False),
    AfterValidator(_database_number),
]
ReviewedScalar = ShortText | BoundedInt | BoundedFloat | bool | None
TriState = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
SourceMappingDecision = Literal["MATCH", "NO_MATCH", "NOT_APPLICABLE", "UNKNOWN"]
ComponentClass = Literal[
    "BENEFIT_COVERAGE",
    "NON_BENEFIT_CONTRACT_COMPONENT",
    "UNKNOWN",
]


class StrictPackageModel(BaseModel):
    """Reject coercion and undeclared source fields at every nesting level."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AuthorityBoundaries(StrictPackageModel):
    enrollment: Literal["certificate_only"]
    terms_presence_never_establishes_enrollment: Literal[True]
    current_status: Literal["latest_contract_state_required"]
    edition_applicability: Literal["contract_date_and_exact_edition_required"]
    individual_claim_decision: Literal["not_performed"]
    executable_rules: Literal[False]


class ReconciliationCounts(StrictPackageModel):
    policy_count: NonNegativeInt
    coverage_component_count: NonNegativeInt
    benefit_coverage_count: NonNegativeInt
    non_benefit_contract_component_count: NonNegativeInt
    certificate_enrollment_match_count: NonNegativeInt
    certificate_enrollment_unknown_count: NonNegativeInt
    policy_terms_identity_match_count: NonNegativeInt
    policy_terms_edition_match_count: NonNegativeInt
    coverage_section_match_count: NonNegativeInt
    coverage_section_no_match_count: NonNegativeInt = 0
    coverage_section_unknown_count: NonNegativeInt
    coverage_section_not_applicable_count: NonNegativeInt
    terms_section_review_count: NonNegativeInt
    source_clause_review_count: NonNegativeInt
    semantic_fact_count: NonNegativeInt
    previous_fact_recheck_count: NonNegativeInt
    previous_fact_needs_review_count: NonNegativeInt
    restored_distinct_object_row_count: NonNegativeInt
    true_duplicate_row_count: NonNegativeInt
    current_status_unknown_count: NonNegativeInt
    database_write_count: NonNegativeInt
    executable_rule_count: NonNegativeInt


class ManifestFile(StrictPackageModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    bytes: Annotated[int, Field(ge=0, le=32 * 1024 * 1024)]
    sha256: Sha256


class PackageManifest(StrictPackageModel):
    schema_version: Literal["private-analysis-package.sol-v2"]
    review_authority: Literal["gpt-5.6-sol_direct_local_review_no_model_api"]
    authority_boundaries: AuthorityBoundaries
    counts: ReconciliationCounts
    files: Annotated[list[ManifestFile], Field(min_length=8, max_length=24)]


class EvidenceLocationRecord(StrictPackageModel):
    document_alias: MediumText
    line: PositivePage
    physical_page: PositivePage


class ContractSourceMemberRecord(StrictPackageModel):
    decision: Literal["approved", "needs_review"]
    document_alias: MediumText
    evidence_pages: Annotated[list[PositivePage], Field(min_length=1, max_length=128)]
    local_policy_id: ShortText


class ContractGroupReviewRecord(StrictPackageModel):
    confidence: Literal["high", "medium"]
    merge_decision: Literal["keep_separate", "same_contract"]
    reason_codes: Annotated[list[ShortText], Field(max_length=64)]


class ContractFieldReviewRecord(StrictPackageModel):
    candidate_value: ReviewedScalar
    decision: TriState
    evidence_locations: Annotated[list[EvidenceLocationRecord], Field(max_length=128)]
    field: ShortText


class ContractRowReconciliationRecord(StrictPackageModel):
    balanced: bool
    benefit_coverages: NonNegativeInt
    canonical_components: NonNegativeInt
    certificate_rows_detected: NonNegativeInt
    duplicate_rows_removed: NonNegativeInt
    non_benefit_contract_components: NonNegativeInt
    unresolved_enrollment_rows: NonNegativeInt


class ContractTermsPairingRecord(StrictPackageModel):
    canonical_policy_id: ShortText
    document_identity_decision: TriState
    edition_applicability_decision: TriState
    executable_rule: Literal[False]
    previous_decision: Literal["linked", "needs_review", "unlinked"]
    reason_codes: Annotated[list[ShortText], Field(max_length=64)]
    review_decision: TriState
    selected_terms_aliases: Annotated[list[MediumText], Field(max_length=8)]


class TermsCandidateRecord(StrictPackageModel):
    candidate_score: UnitScore
    clause_count: NonNegativeInt
    clause_hashes: Annotated[list[MediumText], Field(max_length=128)]
    clause_pages: Annotated[list[PositivePage], Field(max_length=128)]
    direct_body_name_hit: bool
    physical_page: PositivePage
    physical_page_lineage: bool
    section_id: ShortText
    terms_alias: MediumText
    title_similarity: UnitScore


class MappingRecord(StrictPackageModel):
    canonical_rider_id: ShortText
    canonical_policy_id: ShortText
    enrollment_decision: TriState
    component_class: ComponentClass
    pairing_aliases: Annotated[list[MediumText], Field(max_length=8)]
    previous_mapping_decision: ShortText
    previous_terms_alias: MediumText | None
    previous_section_id: ShortText | None
    executable_rule: Literal[False]
    mapping_decision: SourceMappingDecision
    selected_terms_alias: MediumText | None
    selected_section_id: ShortText | None
    physical_page: PositivePage | None
    clause_count: NonNegativeInt
    reason_codes: Annotated[list[ShortText], Field(max_length=64)]
    top_candidates: Annotated[list[TermsCandidateRecord], Field(max_length=10)]
    pairing_review_decision: TriState
    pairing_document_identity_decision: TriState
    pairing_edition_applicability_decision: TriState
    current_coverage_applicability_decision: TriState
    mapping_inherited_from_rider_id: ShortText | None


class ContractRecord(StrictPackageModel):
    canonical_policy_id: ShortText
    family_alias: ShortText
    insurer: ShortText
    product_name: MediumText
    contract_start: IsoDate | None
    contract_end: IsoDate | None
    current_status: Literal["UNKNOWN"]
    monthly_premium_krw: NonNegativeNumber | None
    source_members: Annotated[
        list[ContractSourceMemberRecord],
        Field(min_length=1, max_length=128),
    ]
    group_review: ContractGroupReviewRecord
    duplicate_rows_removed: NonNegativeInt
    field_conflicts: Annotated[list[None], Field(max_length=0)]
    review_state: Literal["NEEDS_REVIEW"]
    previous_candidate_review_state: Literal["AI_VERIFIED", "NEEDS_REVIEW"]
    direct_review_state: Literal["SOL_DIRECT_GROUNDED"]
    candidate_current_status: Literal["active", "unknown"]
    field_reviews: Annotated[list[ContractFieldReviewRecord], Field(max_length=64)]
    row_reconciliation: ContractRowReconciliationRecord
    terms_pairing: ContractTermsPairingRecord


class CoverageSourceReferenceRecord(StrictPackageModel):
    document_alias: MediumText
    evidence_pages: Annotated[list[PositivePage], Field(min_length=1, max_length=128)]
    local_policy_id: ShortText
    local_rider_id: ShortText


class CertificateReviewRecord(StrictPackageModel):
    amount_decision: Literal["ALIGNMENT_REVIEW", "MATCH", "NOT_APPLICABLE", "UNKNOWN"]
    amount_evidence_locations: Annotated[list[EvidenceLocationRecord], Field(max_length=128)]
    amount_support: LongText
    candidate_benefit_type: Literal["fixed", "indemnity", "unknown"]
    candidate_current_status: Literal["active", "unknown"]
    candidate_sum_assured_krw: NonNegativeNumber | None
    canonical_policy_id: ShortText
    canonical_rider_id: ShortText
    classification_findings: Annotated[list[LongText], Field(max_length=128)]
    component_class: ComponentClass
    enrollment_decision: TriState
    evidence_inherited_from_rider_id: ShortText | None
    evidence_locations: Annotated[list[EvidenceLocationRecord], Field(max_length=128)]
    executable_rule: Literal[False]
    insured_object_ref: ShortText | None
    manual_override_reason: LongText | None
    name: MediumText
    name_support: LongText
    object_identity_review_state: Literal["UNKNOWN"] | None
    reviewed_benefit_type: Literal["fixed", "indemnity", "unknown"]
    reviewed_current_status: Literal["unknown"]


class CoverageRecord(StrictPackageModel):
    canonical_policy_id: ShortText
    family_alias: ShortText
    name: MediumText
    coverage_role: Literal["main_contract", "rider"]
    benefit_type: Literal["fixed", "indemnity", "unknown"]
    sum_assured_krw: NonNegativeNumber | None
    currency: Currency | None
    coverage_start: IsoDate | None
    coverage_end: IsoDate | None
    renewable: bool | None
    current_status: Literal["UNKNOWN"]
    warnings: Annotated[list[ShortText], Field(max_length=128)]
    source_refs: Annotated[
        list[CoverageSourceReferenceRecord],
        Field(min_length=1, max_length=128),
    ]
    canonical_rider_id: ShortText
    candidate_current_status: Literal["active", "unknown"]
    certificate_review: CertificateReviewRecord
    review_state: Literal["NEEDS_REVIEW"]
    direct_review_state: Literal["SOL_DIRECT_GROUNDED"]
    terms_mapping: MappingRecord
    current_coverage_applicability_decision: TriState
    executable_rule: Literal[False]
    insured_object_ref: ShortText | None = None
    object_identity_review_state: ShortText | None = None


class PairingEvidenceRecord(StrictPackageModel):
    candidate_score: UnitScore
    clause_count: NonNegativeInt
    direct_insurer_identity: UnitScore
    direct_product_identity: UnitScore
    edition_decision: TriState
    edition_reason_code: ShortText
    physical_page_lineage: bool
    profile_insurer_grounded: bool
    profile_product_grounded: bool
    rider_count: NonNegativeInt
    rider_exact_count: NonNegativeInt
    rider_overlap_count: NonNegativeInt
    rider_overlap_ratio: UnitScore
    section_count: NonNegativeInt
    terms_alias: MediumText


class PairingRecord(StrictPackageModel):
    canonical_policy_id: ShortText
    previous_decision: Literal["linked", "needs_review", "unlinked"]
    selected_terms_aliases: Annotated[list[MediumText], Field(max_length=8)]
    document_identity_decision: TriState
    edition_applicability_decision: TriState
    review_decision: TriState
    reason_codes: Annotated[list[ShortText], Field(max_length=64)]
    selected_evidence: Annotated[list[PairingEvidenceRecord], Field(max_length=8)]
    top_alternative_evidence: Annotated[list[PairingEvidenceRecord], Field(max_length=8)]
    executable_rule: Literal[False]


class TermsSectionRecord(StrictPackageModel):
    terms_alias: MediumText
    position: Annotated[int, Field(ge=1)]
    title: MediumText
    section_id: ShortText
    physical_page: PositivePage
    page_mode: Literal["physical"]
    source_clause_count: NonNegativeInt
    semantic_fact_count: NonNegativeInt
    section_review_state: Literal["SOL_DIRECT_GROUNDED"]
    legacy_review_only: bool
    executable_rule: Literal[False]


class ClauseRecord(StrictPackageModel):
    terms_alias: MediumText
    section_id: ShortText
    clause_index: Annotated[int, Field(ge=1)]
    label: MediumText
    title: MediumText
    physical_page_start: PositivePage
    physical_page_end: PositivePage
    source_text_sha256: Sha256
    semantic_facets: Annotated[list[ShortText], Field(max_length=32)]


class CitationRecord(StrictPackageModel):
    clause_index: Annotated[int, Field(ge=1)]
    physical_page_end: PositivePage
    physical_page_start: PositivePage
    source_text_sha256: Sha256


class SemanticFactRecord(StrictPackageModel):
    category: Literal[
        "amount_basis",
        "claim_documents",
        "cross_reference",
        "definition",
        "exclusion",
        "frequency_limit",
        "payment_reason",
        "reduction_period",
        "renewal",
        "termination",
        "waiting_period",
    ]
    citations: Annotated[list[CitationRecord], Field(min_length=1, max_length=32)]
    condition_details_ko: Annotated[list[LongText], Field(max_length=64)]
    confidence: Literal["high", "medium"]
    decision_impact: ShortText
    executable_rule: Literal[False]
    fact_id: ShortText
    numeric_terms_ko: Annotated[list[LongText], Field(max_length=64)]
    review_state: Literal["NEEDS_REFERENCE_REVIEW", "SOL_DIRECT_GROUNDED"]
    statement_ko: LongText
    unresolved_reference: bool


class CoverageReferenceRecord(StrictPackageModel):
    canonical_policy_id: ShortText
    canonical_rider_id: ShortText
    current_coverage_applicability_decision: TriState
    enrollment_decision: TriState
    pairing_document_identity_decision: TriState
    pairing_edition_applicability_decision: TriState
    section_mapping_decision: TriState


class PreviousFactAuditRecord(StrictPackageModel):
    lexical_support_ratio: UnitScore
    previous_category: ShortText
    previous_fact_id: ShortText
    reason_codes: Annotated[list[ShortText], Field(max_length=64)]
    review_decision: Literal["NEEDS_REVIEW", "SUPPORTED"]


class SemanticReviewRecord(StrictPackageModel):
    terms_alias: MediumText
    section_id: ShortText
    section_physical_page: PositivePage
    analysis_status: Literal["complete"]
    confidence: Literal["high", "medium"]
    section_review_state: Literal["SOL_DIRECT_GROUNDED"]
    section_summary_ko: LongText
    summary_citations: Annotated[list[CitationRecord], Field(max_length=64)]
    facts: Annotated[list[SemanticFactRecord], Field(max_length=128)]
    found_categories: Annotated[list[ShortText], Field(max_length=32)]
    missing_categories: Annotated[list[ShortText], Field(max_length=32)]
    warnings: Annotated[list[ShortText], Field(max_length=128)]
    source_clause_count: NonNegativeInt
    classified_clause_count: NonNegativeInt
    unclassified_clause_count: NonNegativeInt
    previous_result_status: ShortText
    previous_fact_audit: Annotated[list[PreviousFactAuditRecord], Field(max_length=128)]
    executable_rule: Literal[False]
    coverage_references: Annotated[list[CoverageReferenceRecord], Field(max_length=128)]
    legacy_review_only: bool

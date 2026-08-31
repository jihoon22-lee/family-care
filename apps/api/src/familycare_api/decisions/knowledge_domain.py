"""Pure private-knowledge decision values with no database or provider dependency."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from familycare_api.clauses.dsl import RuleKind
from familycare_api.decisions.domain import TriState

KnowledgeFactProvenance = Literal[
    "USER_CONFIRMED",
    "DOCUMENT_REVIEWED",
    "DERIVED_CONFIRMED",
    "AI_SUGGESTED",
    "UNCONFIRMED",
    "CONFLICTING",
]
KnowledgeBenefitType = Literal["FIXED", "INDEMNITY", "UNKNOWN"]
KnowledgeDisposition = Literal["PUBLISHED", "ADVISORY", "BLOCKED", "NOT_APPLICABLE"]
KnowledgeAnalysisCompleteness = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]
KnowledgeCalculationStatus = Literal[
    "CALCULATED",
    "UNKNOWN",
    "NOT_APPLICABLE",
    "FAILED",
]
KnowledgeCertificateAmountDecision = Literal[
    "ALIGNMENT_REVIEW",
    "MATCH",
    "NOT_APPLICABLE",
    "UNKNOWN",
]
KnowledgeCertificateAmountEvidenceState = Literal[
    "DIRECT",
    "REVIEW_REQUIRED",
    "UNAVAILABLE",
]

_TRUSTED_PROVENANCE = frozenset({"USER_CONFIRMED", "DOCUMENT_REVIEWED", "DERIVED_CONFIRMED"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _nonzero(value: UUID, label: str) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{label} must be non-zero")


@dataclass(frozen=True)
class KnowledgeFactNormalizer:
    normalizer_key: str
    field_path: str
    normalized_tokens: tuple[str, ...]
    normalized_value: str | bool
    priority: int

    def __post_init__(self) -> None:
        if not self.normalizer_key or not self.field_path or "." not in self.field_path:
            raise ValueError("invalid normalizer identity")
        if not self.normalized_tokens or any(not value for value in self.normalized_tokens):
            raise ValueError("normalizer tokens are required")
        if isinstance(self.priority, bool) or not 0 <= self.priority <= 1000:
            raise ValueError("invalid normalizer priority")


@dataclass(frozen=True)
class KnowledgeFact:
    value: object | None
    provenance: KnowledgeFactProvenance
    normalizer_keys: tuple[str, ...] = ()
    evidence_keys: tuple[str, ...] = ()
    stale: bool = False

    @property
    def is_trusted(self) -> bool:
        return self.provenance in _TRUSTED_PROVENANCE and not self.stale


@dataclass(frozen=True)
class KnowledgeFactContext:
    facts: Mapping[str, KnowledgeFact]
    audit_conflicts: tuple[str, ...] = ()

    def get(self, field_path: str) -> KnowledgeFact | None:
        return self.facts.get(field_path)


@dataclass(frozen=True)
class KnowledgeCitation:
    citation_key: str
    terms_section_id: UUID
    source_clause_id: UUID | None
    fact_id: UUID | None
    evidence_purpose: str
    page_start: int
    page_end: int
    source_text_sha256: str
    lineage_valid: bool = True

    def __post_init__(self) -> None:
        if not self.citation_key or not self.evidence_purpose:
            raise ValueError("citation identity is required")
        _nonzero(self.terms_section_id, "terms section")
        if self.source_clause_id is not None:
            _nonzero(self.source_clause_id, "source clause")
        if self.fact_id is not None:
            _nonzero(self.fact_id, "fact")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("invalid citation pages")
        if _SHA256.fullmatch(self.source_text_sha256) is None:
            raise ValueError("invalid citation digest")
        if not isinstance(self.lineage_valid, bool):
            raise ValueError("invalid citation lineage state")


@dataclass(frozen=True)
class KnowledgeCertificateEvidence:
    document_alias: str
    evidence_pages: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.document_alias or len(self.document_alias) > 800:
            raise ValueError("invalid certificate evidence document")
        if (
            not self.evidence_pages
            or len(self.evidence_pages) > 128
            or any(isinstance(page, bool) or not 1 <= page <= 500 for page in self.evidence_pages)
            or len(self.evidence_pages) != len(set(self.evidence_pages))
        ):
            raise ValueError("invalid certificate evidence pages")


@dataclass(frozen=True)
class KnowledgeRulePublication:
    publication_id: UUID
    rule_key: str
    rule_kind: RuleKind
    required: bool
    result_reason_code: str
    rule_document: Mapping[str, object]
    citations: tuple[KnowledgeCitation, ...]

    def __post_init__(self) -> None:
        _nonzero(self.publication_id, "rule publication")
        if not self.rule_key or not self.result_reason_code:
            raise ValueError("rule identity is required")
        if not isinstance(self.required, bool):
            raise ValueError("required must be boolean")


@dataclass(frozen=True)
class KnowledgeCalculationPublication:
    publication_id: UUID
    calculation_key: str
    calculation_kind: Literal["FIXED", "INDEMNITY"]
    result_reason_code: str
    calculation_document: Mapping[str, object]
    citations: tuple[KnowledgeCitation, ...]

    def __post_init__(self) -> None:
        _nonzero(self.publication_id, "calculation publication")
        if not self.calculation_key or not self.result_reason_code:
            raise ValueError("calculation identity is required")


@dataclass(frozen=True)
class KnowledgeStatusInterval:
    effective_from: date
    effective_through: date
    decision: TriState
    confirmed_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    authority: Literal["USER_CONFIRMED_EVENT_DATE", "REVIEWED_STATUS_DOCUMENT"]

    def __post_init__(self) -> None:
        if self.effective_through < self.effective_from:
            raise ValueError("invalid status interval")
        if self.decision == "MATCH" and self.confirmed_status == "unknown":
            raise ValueError("matched status must be known")
        if self.decision != "MATCH" and self.confirmed_status != "unknown":
            raise ValueError("unmatched status must be unknown")


@dataclass(frozen=True)
class KnowledgeCoverageContext:
    knowledge_contract_id: UUID
    knowledge_coverage_id: UUID
    contract_label: str
    coverage_label: str
    benefit_type: KnowledgeBenefitType
    insured_amount: Decimal | None
    currency: str | None
    contract_start: date | None
    contract_end: date | None
    disposition: KnowledgeDisposition
    subject_binding_decision: TriState
    enrollment_decision: TriState
    component_classification: Literal[
        "BENEFIT_COVERAGE",
        "NON_BENEFIT_CONTRACT_COMPONENT",
        "UNKNOWN",
    ]
    mapping_applicability: Literal["APPLICABLE", "NOT_APPLICABLE", "UNKNOWN"]
    mapping_enrollment_decision: TriState
    document_identity_decision: TriState
    edition_applicability_decision: TriState
    section_mapping_decision: TriState
    overall_mapping_decision: TriState
    current_confirmation_decision: TriState | None
    current_confirmed_status: (
        Literal["active", "inactive", "lapsed", "terminated", "unknown"] | None
    )
    status_intervals: tuple[KnowledgeStatusInterval, ...]
    rules: tuple[KnowledgeRulePublication, ...]
    calculation: KnowledgeCalculationPublication | None
    certificate_amount_decision: KnowledgeCertificateAmountDecision = "UNKNOWN"
    certificate_amount_evidence_state: KnowledgeCertificateAmountEvidenceState = "UNAVAILABLE"
    certificate_evidence: tuple[KnowledgeCertificateEvidence, ...] = ()
    claim_history_counted_occurrence: KnowledgeFact | None = None

    def __post_init__(self) -> None:
        _nonzero(self.knowledge_contract_id, "knowledge contract")
        _nonzero(self.knowledge_coverage_id, "knowledge coverage")
        if not self.contract_label or len(self.contract_label) > 240:
            raise ValueError("invalid contract label")
        if not self.coverage_label or len(self.coverage_label) > 800:
            raise ValueError("invalid coverage label")
        if self.insured_amount is not None and self.insured_amount < 0:
            raise ValueError("invalid insured amount")
        if self.currency is not None and (
            len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isupper()
        ):
            raise ValueError("invalid currency")
        if (
            self.contract_start is not None
            and self.contract_end is not None
            and self.contract_end < self.contract_start
        ):
            raise ValueError("invalid contract dates")
        if self.certificate_amount_evidence_state == "DIRECT" and (
            self.certificate_amount_decision != "MATCH" or not self.certificate_evidence
        ):
            raise ValueError("direct certificate amount evidence requires a matched review")


@dataclass(frozen=True)
class KnowledgeDecisionContext:
    household_space_id: UUID
    family_member_id: UUID
    knowledge_import_run_id: UUID
    rule_import_run_id: UUID
    status_projection_digest_sha256: str
    coverages: tuple[KnowledgeCoverageContext, ...]
    normalizers: tuple[KnowledgeFactNormalizer, ...]
    supporting_facts: Mapping[str, KnowledgeFact] = field(default_factory=dict)
    receipt_currency: str | None = None

    def __post_init__(self) -> None:
        _nonzero(self.household_space_id, "household")
        _nonzero(self.family_member_id, "family member")
        _nonzero(self.knowledge_import_run_id, "knowledge import run")
        _nonzero(self.rule_import_run_id, "rule import run")
        if _SHA256.fullmatch(self.status_projection_digest_sha256) is None:
            raise ValueError("invalid status projection digest")
        coverage_ids = [item.knowledge_coverage_id for item in self.coverages]
        if len(coverage_ids) != len(set(coverage_ids)):
            raise ValueError("duplicate knowledge coverage")
        if any(
            path not in {"Receipt.confirmed_amount", "Receipt.covered_amount"}
            for path in self.supporting_facts
        ):
            raise ValueError("unsupported private supporting fact")
        if self.receipt_currency is not None and (
            len(self.receipt_currency) != 3
            or not self.receipt_currency.isascii()
            or not self.receipt_currency.isupper()
        ):
            raise ValueError("invalid receipt currency")


@dataclass(frozen=True)
class KnowledgeQuestion:
    field_path: str
    reason_code: str


@dataclass(frozen=True)
class KnowledgeRuleEvaluation:
    evaluation_id: UUID
    knowledge_coverage_id: UUID
    rule_publication_id: UUID
    result: TriState
    required: bool
    reason_code: str
    fact_paths: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    citations: tuple[KnowledgeCitation, ...] = ()
    evaluator_version: str = "private-knowledge-engine-v2"


@dataclass(frozen=True)
class KnowledgeClaimCandidate:
    candidate_id: UUID
    knowledge_contract_id: UUID
    knowledge_coverage_id: UUID
    contract_label: str
    coverage_label: str
    benefit_type: KnowledgeBenefitType
    result: TriState
    evaluations: tuple[KnowledgeRuleEvaluation, ...]
    questions: tuple[KnowledgeQuestion, ...]
    hold_reason_codes: tuple[str, ...]
    required_match_count: int
    required_unknown_count: int
    required_no_match_count: int
    claim_start_ready: Literal[False] = False


@dataclass(frozen=True)
class KnowledgeCalculationStep:
    step_number: int
    operation: str
    input_amount: Decimal | None
    output_amount: Decimal | None
    currency: str | None
    rounding_rule: str | None
    reason_code: str


@dataclass(frozen=True)
class KnowledgeBenefitCalculation:
    calculation_id: UUID
    candidate_id: UUID
    knowledge_coverage_id: UUID
    calculation_publication_id: UUID | None
    kind: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    status: KnowledgeCalculationStatus
    currency: str | None
    conditional_amount: Decimal | None
    confirmed_amount: Decimal | None = None
    excluded_amount: Decimal | None = None
    deductible_amount: Decimal | None = None
    applied_rate: Decimal | None = None
    applied_limit: Decimal | None = None
    rounding_rule: str | None = None
    hold_reason_code: str | None = None
    steps: tuple[KnowledgeCalculationStep, ...] = ()
    certificate_amount_decision: KnowledgeCertificateAmountDecision = "UNKNOWN"
    certificate_amount_evidence_state: KnowledgeCertificateAmountEvidenceState = "UNAVAILABLE"
    certificate_evidence: tuple[KnowledgeCertificateEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.confirmed_amount is not None and (
            self.status != "CALCULATED" or self.hold_reason_code is not None
        ):
            raise ValueError("confirmed amount requires a calculated result without a hold reason")
        if self.certificate_amount_evidence_state == "DIRECT" and (
            self.certificate_amount_decision != "MATCH" or not self.certificate_evidence
        ):
            raise ValueError("direct certificate amount evidence requires a matched review")


@dataclass(frozen=True)
class KnowledgeFixedSubtotal:
    currency: str
    amount: Decimal
    calculated_candidate_count: int
    unresolved_candidate_count: int


@dataclass(frozen=True)
class KnowledgeIndemnitySummary:
    status: Literal["NONE", "CALCULATED", "UNKNOWN"]
    candidate_count: int
    calculated_candidate_count: int
    unresolved_candidate_count: int


@dataclass(frozen=True)
class KnowledgeDecisionResult:
    run_id: UUID
    knowledge_import_run_id: UUID
    rule_import_run_id: UUID
    status_projection_digest_sha256: str
    fact_context: KnowledgeFactContext
    candidates: tuple[KnowledgeClaimCandidate, ...]
    evaluations: tuple[KnowledgeRuleEvaluation, ...]
    calculations: tuple[KnowledgeBenefitCalculation, ...]
    fixed_subtotals: tuple[KnowledgeFixedSubtotal, ...]
    indemnity_summary: KnowledgeIndemnitySummary
    completeness: KnowledgeAnalysisCompleteness
    source_failure_codes: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "KnowledgeAnalysisCompleteness",
    "KnowledgeBenefitCalculation",
    "KnowledgeBenefitType",
    "KnowledgeCalculationPublication",
    "KnowledgeCalculationStatus",
    "KnowledgeCertificateAmountDecision",
    "KnowledgeCertificateAmountEvidenceState",
    "KnowledgeCalculationStep",
    "KnowledgeCitation",
    "KnowledgeClaimCandidate",
    "KnowledgeCoverageContext",
    "KnowledgeDecisionContext",
    "KnowledgeDecisionResult",
    "KnowledgeDisposition",
    "KnowledgeFact",
    "KnowledgeFactContext",
    "KnowledgeFactNormalizer",
    "KnowledgeFactProvenance",
    "KnowledgeFixedSubtotal",
    "KnowledgeIndemnitySummary",
    "KnowledgeQuestion",
    "KnowledgeRuleEvaluation",
    "KnowledgeRulePublication",
    "KnowledgeStatusInterval",
]

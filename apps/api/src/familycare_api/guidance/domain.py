"""Source-neutral local evaluation inputs; identities keep their actual storage kind."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from familycare_api.clauses.dsl import RuleKind
from familycare_api.common.coverage_identity import CanonicalCoverageIdentity, CanonicalCoverageRef
from familycare_api.decisions.domain import TriState
from familycare_api.decisions.knowledge_domain import (
    KnowledgeBenefitType,
    KnowledgeCertificateAmountDecision,
    KnowledgeCertificateAmountEvidenceState,
    KnowledgeDisposition,
    KnowledgeFact,
    KnowledgeFactNormalizer,
    KnowledgeStatusInterval,
)
from familycare_api.guidance.event_facts import CodeScope
from familycare_api.guidance.expenses import ExpenseRead
from familycare_api.guidance.models import (
    GuidanceContractAmount,
    GuidanceEvidenceRef,
    GuidanceVersions,
)


@dataclass(frozen=True, slots=True, repr=False)
class GuidanceCitation:
    citation_key: str
    evidence: GuidanceEvidenceRef
    lineage_valid: bool = True


@dataclass(frozen=True, slots=True, repr=False)
class GuidanceRuleInput:
    publication_id: UUID
    rule_key: str
    rule_kind: RuleKind
    required: bool
    result_reason_code: str
    rule_document: Mapping[str, object]
    citations: tuple[GuidanceCitation, ...]
    source_kind: Literal["PRIVATE_RULE_PUBLICATION", "OPERATIONAL_RULE_VERSION", "SEMANTIC_NODE"]
    semantic_node_id: str | None = None
    classification_scopes: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class GuidanceCalculationInput:
    publication_id: UUID
    calculation_key: str
    calculation_kind: Literal["FIXED", "INDEMNITY"]
    result_reason_code: str
    calculation_document: Mapping[str, object]
    citations: tuple[GuidanceCitation, ...]
    source_kind: Literal["PRIVATE_RULE_PUBLICATION", "OPERATIONAL_RULE_VERSION", "SEMANTIC_NODE"]
    semantic_node_id: str | None = None
    source_currency: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class GuidancePayoutCaseInput:
    case_key: str
    rules: tuple[GuidanceRuleInput, ...]
    calculation: GuidanceCalculationInput | None
    benefit_type: KnowledgeBenefitType
    knowledge_incomplete: bool = False
    source_ref: CanonicalCoverageRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_key, str) or not 1 <= len(self.case_key) <= 256:
            raise ValueError("GUIDANCE_PAYOUT_CASE_IDENTITY_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class GuidanceCoverageInput:
    ref: CanonicalCoverageRef
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
        "BENEFIT_COVERAGE", "NON_BENEFIT_CONTRACT_COMPONENT", "UNKNOWN"
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
    rules: tuple[GuidanceRuleInput, ...]
    calculation: GuidanceCalculationInput | None
    certificate_amount_decision: KnowledgeCertificateAmountDecision = "UNKNOWN"
    certificate_amount_evidence_state: KnowledgeCertificateAmountEvidenceState = "UNAVAILABLE"
    claim_history_counted_occurrence: KnowledgeFact | None = None
    canonical_identity: CanonicalCoverageIdentity | None = None
    knowledge_incomplete: bool = False
    contract_amount: GuidanceContractAmount | None = None
    cases: tuple[GuidancePayoutCaseInput, ...] = ()

    def __post_init__(self) -> None:
        if len({case.case_key for case in self.cases}) != len(self.cases):
            raise ValueError("GUIDANCE_DUPLICATE_PAYOUT_CASE")
        if (
            self.canonical_identity is not None
            and self.ref not in self.canonical_identity.source_refs
        ):
            raise ValueError("GUIDANCE_SOURCE_IDENTITY_MISMATCH")
        if self.insured_amount is not None and (
            not self.insured_amount.is_finite() or self.insured_amount < 0
        ):
            raise ValueError("GUIDANCE_AMOUNT_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class GuidanceContext:
    household_space_id: UUID
    family_member_id: UUID
    coverages: tuple[GuidanceCoverageInput, ...]
    versions: GuidanceVersions = field(default_factory=GuidanceVersions)
    normalizers: tuple[KnowledgeFactNormalizer, ...] = ()
    normalizer_code_scopes: Mapping[str, CodeScope] = field(default_factory=dict)
    supporting_facts: Mapping[str, KnowledgeFact] = field(default_factory=dict)
    receipt_currency: str | None = None
    expenses: ExpenseRead | None = None
    selected_subject_terms: tuple[str, ...] = ()
    other_subject_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.household_space_id.int or not self.family_member_id.int:
            raise ValueError("GUIDANCE_SCOPE_INVALID")
        keys = [(item.ref.kind, item.ref.coverage_id) for item in self.coverages]
        canonical_refs = {
            item.canonical_identity.ref if item.canonical_identity is not None else item.ref
            for item in self.coverages
        }
        # Each admitted canonical coverage may retain its private and operational input.
        if len(keys) != len(set(keys)) or len(keys) > 2000 or len(canonical_refs) > 1000:
            raise ValueError("GUIDANCE_COVERAGE_SET_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class GuidanceRuleEvaluation:
    source: GuidanceRuleInput
    result: TriState
    required: bool
    reason_code: str
    citations: tuple[GuidanceCitation, ...]
    fact_paths: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()

    @property
    def rule_publication_id(self) -> UUID:
        return self.source.publication_id

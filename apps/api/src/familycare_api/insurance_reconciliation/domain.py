"""Pure derivation rules for insurance catalog and operational readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from familycare_api.insurance_documents.domain import UnreadableSource

TriState = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
PolicyStatus = Literal["active", "inactive", "expired", "cancelled", "unknown"]
Completeness = Literal["CERTIFICATE_AND_TERMS", "CERTIFICATE_ONLY"]
ReconciliationState = Literal[
    "EVIDENCE_READY",
    "DOCUMENTS_PENDING",
    "LINK_REVIEW_REQUIRED",
    "CONFLICT",
]
LinkAuthority = Literal[
    "SNAPSHOT_EXACT_EVIDENCE",
    "USER_CONFIRMED_OPERATIONAL_IDENTITY",
]


@dataclass(frozen=True)
class KnowledgeContractSource:
    id: UUID
    insurer_display: str
    product_display: str
    certificate_decision: TriState
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    snapshot_policy_contract_id: UUID | None
    snapshot_operational_decision: TriState
    snapshot_operational_reason_code: str


@dataclass(frozen=True)
class OperationalLinkHistory:
    id: UUID
    knowledge_contract_id: UUID
    policy_contract_id: UUID | None
    decision: TriState
    conflict: bool
    authority: Literal["USER_CONFIRMED_OPERATIONAL_IDENTITY"]
    reason_code: str
    confirmed_at: datetime


@dataclass(frozen=True)
class DocumentResolutionHistory:
    id: UUID
    failed_item_id: UUID
    replacement_item_id: UUID | None
    resolution: Literal["REPLACED", "DISMISSED", "REOPENED"]
    authority: Literal["USER_CONFIRMED_DOCUMENT_RESOLUTION"]
    reason_code: str
    confirmed_at: datetime


@dataclass(frozen=True)
class OperationalPolicySource:
    id: UUID
    insurer_display: str
    product_display: str
    status: PolicyStatus
    completeness: Completeness
    has_product_explanation: bool
    has_application: bool


@dataclass(frozen=True)
class OperationalLinkProjection:
    id: UUID | None
    policy_contract_id: UUID | None
    decision: TriState
    conflict: bool
    authority: LinkAuthority | None
    reason_code: str
    confirmed_at: datetime | None


@dataclass(frozen=True)
class DocumentReadiness:
    policy_contract_id: UUID
    completeness: Completeness
    has_product_explanation: bool
    has_application: bool


@dataclass(frozen=True)
class ContractReconciliation:
    knowledge_contract_id: UUID
    insurer_display: str
    product_display: str
    certificate_decision: TriState
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    reconciliation_state: ReconciliationState
    operational_link: OperationalLinkProjection
    document_readiness: DocumentReadiness | None


@dataclass(frozen=True)
class OrphanOperationalPolicy:
    policy_contract_id: UUID
    insurer_display: str
    product_display: str
    status: PolicyStatus
    completeness: Completeness


@dataclass(frozen=True)
class ReconciliationSummary:
    total_contracts: int
    evidence_ready_contracts: int
    documents_pending_contracts: int
    link_review_required_contracts: int
    conflict_contracts: int
    orphan_operational_contracts: int
    unresolved_unreadable_sources: int


@dataclass(frozen=True)
class MemberInsuranceReconciliation:
    member_id: UUID
    knowledge_run_id: UUID
    generated_at: datetime
    summary: ReconciliationSummary
    contracts: tuple[ContractReconciliation, ...]
    orphan_operational_contracts: tuple[OrphanOperationalPolicy, ...]
    unresolved_sources: tuple[UnreadableSource, ...]


def _unique_by_id[T](values: tuple[T, ...], *, label: str) -> dict[UUID, T]:
    indexed: dict[UUID, T] = {}
    for value in values:
        value_id = getattr(value, "id", None)
        if not isinstance(value_id, UUID):
            raise ValueError(f"invalid {label}")
        if value_id in indexed:
            raise ValueError(f"duplicate {label}")
        indexed[value_id] = value
    return indexed


def _current_links(
    values: tuple[OperationalLinkHistory, ...],
    *,
    contract_ids: set[UUID],
) -> dict[UUID, OperationalLinkHistory]:
    indexed: dict[UUID, OperationalLinkHistory] = {}
    matched_policies: set[UUID] = set()
    for value in values:
        if value.knowledge_contract_id not in contract_ids:
            raise ValueError("operational link contract is outside the projection")
        if value.knowledge_contract_id in indexed:
            raise ValueError("duplicate current operational link")
        if value.decision == "MATCH":
            if value.policy_contract_id is None or value.conflict:
                raise ValueError("invalid current operational link")
            if value.policy_contract_id in matched_policies:
                raise ValueError("duplicate current operational policy link")
            matched_policies.add(value.policy_contract_id)
        elif value.policy_contract_id is not None:
            raise ValueError("invalid current operational link")
        if value.conflict and value.decision != "UNKNOWN":
            raise ValueError("invalid current operational link conflict")
        indexed[value.knowledge_contract_id] = value
    return indexed


def _effective_link(
    contract: KnowledgeContractSource,
    current: OperationalLinkHistory | None,
) -> OperationalLinkProjection:
    if current is not None:
        return OperationalLinkProjection(
            id=current.id,
            policy_contract_id=current.policy_contract_id,
            decision=current.decision,
            conflict=current.conflict,
            authority=current.authority,
            reason_code=current.reason_code,
            confirmed_at=current.confirmed_at,
        )
    if contract.snapshot_operational_decision == "MATCH":
        return OperationalLinkProjection(
            id=None,
            policy_contract_id=contract.snapshot_policy_contract_id,
            decision="MATCH",
            conflict=contract.snapshot_policy_contract_id is None,
            authority="SNAPSHOT_EXACT_EVIDENCE",
            reason_code=contract.snapshot_operational_reason_code,
            confirmed_at=None,
        )
    return OperationalLinkProjection(
        id=None,
        policy_contract_id=None,
        decision=contract.snapshot_operational_decision,
        conflict=False,
        authority=None,
        reason_code=contract.snapshot_operational_reason_code,
        confirmed_at=None,
    )


def _contract_projection(
    contract: KnowledgeContractSource,
    link: OperationalLinkProjection,
    policy: OperationalPolicySource | None,
) -> ContractReconciliation:
    readiness: DocumentReadiness | None = None
    if link.conflict or (link.decision == "MATCH" and policy is None):
        state: ReconciliationState = "CONFLICT"
    elif link.decision != "MATCH":
        state = "LINK_REVIEW_REQUIRED"
    else:
        assert policy is not None
        readiness = DocumentReadiness(
            policy_contract_id=policy.id,
            completeness=policy.completeness,
            has_product_explanation=policy.has_product_explanation,
            has_application=policy.has_application,
        )
        state = (
            "EVIDENCE_READY"
            if policy.completeness == "CERTIFICATE_AND_TERMS"
            else "DOCUMENTS_PENDING"
        )
    return ContractReconciliation(
        knowledge_contract_id=contract.id,
        insurer_display=contract.insurer_display,
        product_display=contract.product_display,
        certificate_decision=contract.certificate_decision,
        current_status=contract.current_status,
        reconciliation_state=state,
        operational_link=link,
        document_readiness=readiness,
    )


def build_member_reconciliation(
    *,
    member_id: UUID,
    knowledge_run_id: UUID,
    generated_at: datetime,
    contracts: tuple[KnowledgeContractSource, ...],
    current_links: tuple[OperationalLinkHistory, ...],
    operational_policies: tuple[OperationalPolicySource, ...],
    unresolved_sources: tuple[UnreadableSource, ...],
) -> MemberInsuranceReconciliation:
    """Derive one closed member projection without changing either source authority."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone aware")
    contract_by_id = _unique_by_id(contracts, label="knowledge contract")
    policy_by_id = _unique_by_id(operational_policies, label="operational policy")
    link_by_contract = _current_links(
        current_links,
        contract_ids=set(contract_by_id),
    )
    projections: list[ContractReconciliation] = []
    linked_policy_ids: set[UUID] = set()
    for contract in contracts:
        link = _effective_link(contract, link_by_contract.get(contract.id))
        policy = (
            policy_by_id.get(link.policy_contract_id)
            if link.policy_contract_id is not None
            else None
        )
        projection = _contract_projection(contract, link, policy)
        projections.append(projection)
        if link.decision == "MATCH" and policy is not None and not link.conflict:
            if policy.id in linked_policy_ids:
                raise ValueError("duplicate current operational policy link")
            linked_policy_ids.add(policy.id)

    orphan_policies = tuple(
        OrphanOperationalPolicy(
            policy_contract_id=policy.id,
            insurer_display=policy.insurer_display,
            product_display=policy.product_display,
            status=policy.status,
            completeness=policy.completeness,
        )
        for policy in operational_policies
        if policy.id not in linked_policy_ids
    )
    states = tuple(item.reconciliation_state for item in projections)
    summary = ReconciliationSummary(
        total_contracts=len(projections),
        evidence_ready_contracts=states.count("EVIDENCE_READY"),
        documents_pending_contracts=states.count("DOCUMENTS_PENDING"),
        link_review_required_contracts=states.count("LINK_REVIEW_REQUIRED"),
        conflict_contracts=states.count("CONFLICT"),
        orphan_operational_contracts=len(orphan_policies),
        unresolved_unreadable_sources=len(unresolved_sources),
    )
    if (
        summary.evidence_ready_contracts
        + summary.documents_pending_contracts
        + summary.link_review_required_contracts
        + summary.conflict_contracts
        != summary.total_contracts
    ):
        raise ValueError("reconciliation summary is not closed")
    return MemberInsuranceReconciliation(
        member_id=member_id,
        knowledge_run_id=knowledge_run_id,
        generated_at=generated_at,
        summary=summary,
        contracts=tuple(projections),
        orphan_operational_contracts=orphan_policies,
        unresolved_sources=unresolved_sources,
    )


__all__ = [
    "ContractReconciliation",
    "DocumentResolutionHistory",
    "DocumentReadiness",
    "KnowledgeContractSource",
    "MemberInsuranceReconciliation",
    "OperationalLinkHistory",
    "OperationalPolicySource",
    "build_member_reconciliation",
]

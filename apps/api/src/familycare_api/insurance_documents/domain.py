"""Pure member insurance-document inventory vocabulary and projection rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

DocumentRole = Literal["policy", "terms", "product_explanation", "application", "supporting"]
ReviewState = Literal["SUGGESTED", "USER_CONFIRMED", "CONFLICT", "REJECTED"]
ProcessingState = Literal[
    "READY",
    "PENDING",
    "PASSWORD_REQUIRED",
    "OCR_REQUIRED",
    "FAILED",
]
UnreadableProcessingState = Literal["PASSWORD_REQUIRED", "OCR_REQUIRED", "FAILED"]
DuplicateState = Literal["UNIQUE", "SAME_MEMBER_DUPLICATE", "CROSS_MEMBER_COPY_POSSIBLE"]
Completeness = Literal["CERTIFICATE_AND_TERMS", "CERTIFICATE_ONLY"]
PolicyStatus = Literal["active", "inactive", "expired", "cancelled", "unknown"]
PrimaryClassification = Literal[
    "TERMS_ONLY",
    "PRODUCT_EXPLANATION_ONLY",
    "APPLICATION_ONLY",
    "POLICY_UNREVIEWED",
    "SUPPORTING_ONLY",
]

_ROLE_ORDER: tuple[DocumentRole, ...] = (
    "policy",
    "terms",
    "product_explanation",
    "application",
    "supporting",
)


@dataclass(frozen=True)
class InventoryComponent:
    id: UUID | None
    document_batch_item_id: UUID | None
    document_version_id: UUID
    content_sha256: str
    role: DocumentRole
    page_start: int
    page_end: int
    review_state: ReviewState
    processing_state: ProcessingState
    duplicate_state: DuplicateState

    @property
    def identity(self) -> tuple[str, int, int, DocumentRole]:
        return (self.content_sha256, self.page_start, self.page_end, self.role)


@dataclass(frozen=True)
class InventorySetItem:
    component: InventoryComponent
    match_state: ReviewState
    id: UUID | None = None
    version: int = 1


@dataclass(frozen=True)
class InventorySet:
    id: UUID
    policy_contract_id: UUID | None
    insurer_display: str | None
    product_display: str | None
    display_label: str
    version: int
    items: tuple[InventorySetItem, ...]


@dataclass(frozen=True)
class InventoryPolicy:
    id: UUID
    source_document_version_id: UUID
    source_content_sha256: str
    source_evidence_page: int
    insurer_display: str
    product_display: str
    status: PolicyStatus
    rider_count: int


@dataclass(frozen=True)
class InsuranceDocumentComponentRecord:
    id: UUID
    document_batch_item_id: UUID
    role: DocumentRole
    page_start: int
    page_end: int
    review_state: ReviewState
    version: int


@dataclass(frozen=True)
class InsuranceDocumentSetRecord:
    id: UUID
    member_id: UUID
    policy_contract_id: UUID | None
    insurer_display: str | None
    product_display: str | None
    display_label: str
    version: int


@dataclass(frozen=True)
class InsuranceDocumentSetItemRecord:
    id: UUID
    insurance_document_set_id: UUID
    insurance_document_component_id: UUID
    role: DocumentRole
    match_state: ReviewState
    version: int


@dataclass(frozen=True)
class UnreadableSource:
    document_batch_item_id: UUID
    source_kind: DocumentRole
    display_label: str
    processing_state: UnreadableProcessingState


@dataclass(frozen=True)
class RoleDocumentSummary:
    role: DocumentRole
    source_count: int
    component_count: int
    bundled_source: bool
    items: tuple[InventorySetItem, ...]


@dataclass(frozen=True)
class RegisteredPolicyInventory:
    policy: InventoryPolicy
    completeness: Completeness
    documents: tuple[RoleDocumentSummary, ...]
    has_product_explanation: bool
    has_application: bool
    missing_document_roles: tuple[DocumentRole, ...]
    document_set_id: UUID | None
    document_set_version: int | None


@dataclass(frozen=True)
class UnregisteredDocumentSetInventory:
    id: UUID
    insurer_display: str | None
    product_display: str | None
    display_label: str
    version: int
    primary_classification: PrimaryClassification
    enrollment_confirmed: bool
    has_product_explanation: bool
    has_application: bool
    source_count: int
    component_count: int
    items: tuple[InventorySetItem, ...]


@dataclass(frozen=True)
class InventorySummary:
    certificate_backed_policies: int
    certificate_and_terms: int
    certificate_only: int
    terms_only_documents: int
    product_explanation_documents: int
    application_documents: int
    unreadable_documents: int
    pairing_conflicts: int


@dataclass(frozen=True)
class MemberInsuranceDocumentInventory:
    member_id: UUID
    summary: InventorySummary
    registered_policies: tuple[RegisteredPolicyInventory, ...]
    unregistered_document_sets: tuple[UnregisteredDocumentSetInventory, ...]
    unpaired_components: tuple[InventoryComponent, ...]
    unreadable_sources: tuple[UnreadableSource, ...]


def _confirmed(item: InventorySetItem) -> bool:
    return item.match_state == "USER_CONFIRMED" and item.component.review_state == "USER_CONFIRMED"


def _role_summaries(
    items: tuple[InventorySetItem, ...],
    bundled_sources: frozenset[str],
) -> tuple[RoleDocumentSummary, ...]:
    summaries: list[RoleDocumentSummary] = []
    for role in _ROLE_ORDER:
        role_items = tuple(item for item in items if item.component.role == role)
        if not role_items:
            continue
        sources = {item.component.content_sha256 for item in role_items}
        identities = {item.component.identity for item in role_items}
        summaries.append(
            RoleDocumentSummary(
                role=role,
                source_count=len(sources),
                component_count=len(identities),
                bundled_source=any(source in bundled_sources for source in sources),
                items=role_items,
            )
        )
    return tuple(summaries)


def _primary_classification(items: tuple[InventorySetItem, ...]) -> PrimaryClassification:
    roles = {item.component.role for item in items if item.match_state != "REJECTED"}
    if "policy" in roles:
        return "POLICY_UNREVIEWED"
    if "terms" in roles:
        return "TERMS_ONLY"
    if "product_explanation" in roles:
        return "PRODUCT_EXPLANATION_ONLY"
    if "application" in roles:
        return "APPLICATION_ONLY"
    return "SUPPORTING_ONLY"


def _fallback_policy_item(policy: InventoryPolicy) -> InventorySetItem:
    return InventorySetItem(
        component=InventoryComponent(
            id=None,
            document_batch_item_id=None,
            document_version_id=policy.source_document_version_id,
            content_sha256=policy.source_content_sha256,
            role="policy",
            page_start=policy.source_evidence_page,
            page_end=policy.source_evidence_page,
            review_state="USER_CONFIRMED",
            processing_state="READY",
            duplicate_state="UNIQUE",
        ),
        match_state="USER_CONFIRMED",
    )


def build_member_inventory(
    member_id: UUID,
    *,
    policies: tuple[InventoryPolicy, ...],
    document_sets: tuple[InventorySet, ...],
    unpaired_components: tuple[InventoryComponent, ...] = (),
    unreadable_sources: tuple[UnreadableSource, ...] = (),
) -> MemberInsuranceDocumentInventory:
    """Derive completeness without granting enrollment authority to document metadata."""

    sets_by_policy = {
        item.policy_contract_id: item
        for item in document_sets
        if item.policy_contract_id is not None
    }
    source_component_identities: dict[
        str,
        set[tuple[str, int, int, DocumentRole]],
    ] = {}
    for document_set in document_sets:
        for item in document_set.items:
            if item.match_state == "REJECTED":
                continue
            source_component_identities.setdefault(
                item.component.content_sha256,
                set(),
            ).add(item.component.identity)
    for component in unpaired_components:
        source_component_identities.setdefault(component.content_sha256, set()).add(
            component.identity
        )
    bundled_sources = frozenset(
        source for source, identities in source_component_identities.items() if len(identities) > 1
    )
    registered: list[RegisteredPolicyInventory] = []
    all_items: list[InventorySetItem] = []
    for policy in policies:
        policy_document_set = sets_by_policy.get(policy.id)
        items = policy_document_set.items if policy_document_set is not None else ()
        authoritative_policy = any(
            _confirmed(item)
            and item.component.role == "policy"
            and item.component.document_version_id == policy.source_document_version_id
            and item.component.page_start <= policy.source_evidence_page <= item.component.page_end
            for item in items
        )
        if not authoritative_policy:
            items = (_fallback_policy_item(policy), *items)
        confirmed_terms = authoritative_policy and any(
            _confirmed(item) and item.component.role == "terms" for item in items
        )
        summaries = _role_summaries(tuple(items), bundled_sources)
        all_items.extend(items)
        registered.append(
            RegisteredPolicyInventory(
                policy=policy,
                completeness=("CERTIFICATE_AND_TERMS" if confirmed_terms else "CERTIFICATE_ONLY"),
                documents=summaries,
                has_product_explanation=any(
                    _confirmed(item) and item.component.role == "product_explanation"
                    for item in items
                ),
                has_application=any(
                    _confirmed(item) and item.component.role == "application" for item in items
                ),
                missing_document_roles=() if confirmed_terms else ("terms",),
                document_set_id=(
                    policy_document_set.id if policy_document_set is not None else None
                ),
                document_set_version=(
                    policy_document_set.version if policy_document_set is not None else None
                ),
            )
        )

    unregistered: list[UnregisteredDocumentSetInventory] = []
    for document_set in document_sets:
        if document_set.policy_contract_id is not None:
            continue
        active_items = tuple(item for item in document_set.items if item.match_state != "REJECTED")
        all_items.extend(active_items)
        sources = {item.component.content_sha256 for item in active_items}
        identities = {item.component.identity for item in active_items}
        unregistered.append(
            UnregisteredDocumentSetInventory(
                id=document_set.id,
                insurer_display=document_set.insurer_display,
                product_display=document_set.product_display,
                display_label=document_set.display_label,
                version=document_set.version,
                primary_classification=_primary_classification(active_items),
                enrollment_confirmed=False,
                has_product_explanation=any(
                    item.component.role == "product_explanation" for item in active_items
                ),
                has_application=any(item.component.role == "application" for item in active_items),
                source_count=len(sources),
                component_count=len(identities),
                items=active_items,
            )
        )

    counted_components = {
        item.component.identity: item.component
        for item in all_items
        if item.component.id is not None
    }
    counted_components.update({item.identity: item for item in unpaired_components})
    component_values = tuple(counted_components.values())
    terms_only_identities = {
        set_item.component.identity
        for item in unregistered
        if item.primary_classification == "TERMS_ONLY"
        for set_item in item.items
        if set_item.component.role == "terms"
    }
    terms_only_identities.update(
        item.identity for item in unpaired_components if item.role == "terms"
    )
    conflict_items = {
        item.component.identity
        for item in all_items
        if item.match_state == "CONFLICT" or item.component.review_state == "CONFLICT"
    }
    conflict_items.update(
        item.identity for item in unpaired_components if item.review_state == "CONFLICT"
    )
    summary = InventorySummary(
        certificate_backed_policies=len(registered),
        certificate_and_terms=sum(
            item.completeness == "CERTIFICATE_AND_TERMS" for item in registered
        ),
        certificate_only=sum(item.completeness == "CERTIFICATE_ONLY" for item in registered),
        terms_only_documents=len(terms_only_identities),
        product_explanation_documents=sum(
            item.role == "product_explanation" for item in component_values
        ),
        application_documents=sum(item.role == "application" for item in component_values),
        unreadable_documents=len(unreadable_sources),
        pairing_conflicts=len(conflict_items),
    )
    return MemberInsuranceDocumentInventory(
        member_id=member_id,
        summary=summary,
        registered_policies=tuple(registered),
        unregistered_document_sets=tuple(unregistered),
        unpaired_components=unpaired_components,
        unreadable_sources=unreadable_sources,
    )


__all__ = [
    "Completeness",
    "DocumentRole",
    "DuplicateState",
    "InventoryComponent",
    "InsuranceDocumentComponentRecord",
    "InsuranceDocumentSetItemRecord",
    "InsuranceDocumentSetRecord",
    "InventoryPolicy",
    "InventorySet",
    "InventorySetItem",
    "MemberInsuranceDocumentInventory",
    "ProcessingState",
    "PolicyStatus",
    "ReviewState",
    "UnreadableProcessingState",
    "UnreadableSource",
    "build_member_inventory",
]

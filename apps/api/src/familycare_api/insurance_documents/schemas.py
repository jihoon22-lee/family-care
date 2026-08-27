"""Strict HTTP schemas for insurance-document components, sets, and inventory."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.insurance_documents.domain import (
    Completeness,
    DocumentRole,
    DuplicateState,
    InsuranceDocumentComponentRecord,
    InsuranceDocumentSetItemRecord,
    InsuranceDocumentSetRecord,
    InventoryComponent,
    InventorySetItem,
    MemberInsuranceDocumentInventory,
    PolicyStatus,
    PrimaryClassification,
    ProcessingState,
    ReviewState,
    UnreadableProcessingState,
    UnreadableSource,
)

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class InsuranceDocumentErrorResponse(BaseModel):
    model_config = _STRICT

    error_code: str
    message: str
    fields: list[str] | None = None


class ComponentCreateRequest(BaseModel):
    model_config = _STRICT

    document_batch_item_id: UUID
    role: DocumentRole
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)
    evidence_id: UUID | None = None
    review_state: ReviewState = "USER_CONFIRMED"

    @model_validator(mode="after")
    def validate_page_range(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("page range is invalid")
        return self


class DocumentSetCreateRequest(BaseModel):
    model_config = _STRICT

    policy_contract_id: UUID | None = None
    insurer_display: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[^\r\n]+$",
    )
    product_display: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[^\r\n]+$",
    )
    display_label: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[^/\\\r\n]+$",
    )


class DocumentSetItemCreateRequest(BaseModel):
    model_config = _STRICT

    insurance_document_component_id: UUID
    match_state: ReviewState
    evidence_id: UUID | None = None
    expected_set_version: int = Field(ge=1)


class ExpectedItemVersionRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)


class InventoryComponentResponse(BaseModel):
    model_config = _STRICT

    id: UUID | None
    document_batch_item_id: UUID | None
    role: DocumentRole
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)
    review_state: ReviewState
    processing_state: ProcessingState
    duplicate_state: DuplicateState

    @classmethod
    def from_domain(cls, component: InventoryComponent) -> InventoryComponentResponse:
        return cls(
            id=component.id,
            document_batch_item_id=component.document_batch_item_id,
            role=component.role,
            page_start=component.page_start,
            page_end=component.page_end,
            review_state=component.review_state,
            processing_state=component.processing_state,
            duplicate_state=component.duplicate_state,
        )


class InventorySetItemResponse(BaseModel):
    model_config = _STRICT

    id: UUID | None
    version: int = Field(ge=1)
    match_state: ReviewState
    component: InventoryComponentResponse

    @classmethod
    def from_domain(cls, item: InventorySetItem) -> InventorySetItemResponse:
        return cls(
            id=item.id,
            version=item.version,
            match_state=item.match_state,
            component=InventoryComponentResponse.from_domain(item.component),
        )


class RoleDocumentSummaryResponse(BaseModel):
    model_config = _STRICT

    role: DocumentRole
    source_count: int = Field(ge=0)
    component_count: int = Field(ge=0)
    bundled_source: bool
    items: tuple[InventorySetItemResponse, ...] = Field(max_length=100)


class RegisteredPolicyInventoryResponse(BaseModel):
    model_config = _STRICT

    policy_id: UUID
    insurer_display: str = Field(min_length=1, max_length=160)
    product_display: str = Field(min_length=1, max_length=200)
    status: PolicyStatus
    rider_count: int = Field(ge=0)
    completeness: Completeness
    documents: tuple[RoleDocumentSummaryResponse, ...] = Field(max_length=5)
    has_product_explanation: bool
    has_application: bool
    missing_document_roles: tuple[DocumentRole, ...] = Field(max_length=5)
    document_set_id: UUID | None
    document_set_version: int | None


class UnregisteredDocumentSetResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    insurer_display: str | None = Field(min_length=1, max_length=160)
    product_display: str | None = Field(min_length=1, max_length=200)
    display_label: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    primary_classification: PrimaryClassification
    enrollment_confirmed: bool
    has_product_explanation: bool
    has_application: bool
    source_count: int = Field(ge=0)
    component_count: int = Field(ge=0)
    items: tuple[InventorySetItemResponse, ...] = Field(max_length=100)


class InventorySummaryResponse(BaseModel):
    model_config = _STRICT

    certificate_backed_policies: int = Field(ge=0)
    certificate_and_terms: int = Field(ge=0)
    certificate_only: int = Field(ge=0)
    terms_only_documents: int = Field(ge=0)
    product_explanation_documents: int = Field(ge=0)
    application_documents: int = Field(ge=0)
    unreadable_documents: int = Field(ge=0)
    pairing_conflicts: int = Field(ge=0)


class UnreadableSourceResponse(BaseModel):
    model_config = _STRICT

    document_batch_item_id: UUID
    source_kind: DocumentRole
    display_label: str = Field(min_length=1, max_length=80)
    processing_state: UnreadableProcessingState

    @classmethod
    def from_domain(cls, source: UnreadableSource) -> UnreadableSourceResponse:
        return cls(**source.__dict__)


class MemberInsuranceDocumentInventoryResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    member_id: UUID
    summary: InventorySummaryResponse
    registered_policies: tuple[RegisteredPolicyInventoryResponse, ...] = Field(max_length=256)
    unregistered_document_sets: tuple[UnregisteredDocumentSetResponse, ...] = Field(max_length=512)
    unpaired_components: tuple[InventoryComponentResponse, ...] = Field(max_length=1000)
    unreadable_sources: tuple[UnreadableSourceResponse, ...] = Field(max_length=1000)

    @classmethod
    def from_domain(
        cls,
        inventory: MemberInsuranceDocumentInventory,
    ) -> MemberInsuranceDocumentInventoryResponse:
        return cls(
            schema_version="1",
            member_id=inventory.member_id,
            summary=InventorySummaryResponse(**inventory.summary.__dict__),
            registered_policies=tuple(
                RegisteredPolicyInventoryResponse(
                    policy_id=item.policy.id,
                    insurer_display=item.policy.insurer_display,
                    product_display=item.policy.product_display,
                    status=item.policy.status,
                    rider_count=item.policy.rider_count,
                    completeness=item.completeness,
                    documents=tuple(
                        RoleDocumentSummaryResponse(
                            role=document.role,
                            source_count=document.source_count,
                            component_count=document.component_count,
                            bundled_source=document.bundled_source,
                            items=tuple(
                                InventorySetItemResponse.from_domain(set_item)
                                for set_item in document.items
                            ),
                        )
                        for document in item.documents
                    ),
                    has_product_explanation=item.has_product_explanation,
                    has_application=item.has_application,
                    missing_document_roles=item.missing_document_roles,
                    document_set_id=item.document_set_id,
                    document_set_version=item.document_set_version,
                )
                for item in inventory.registered_policies
            ),
            unregistered_document_sets=tuple(
                UnregisteredDocumentSetResponse(
                    id=item.id,
                    insurer_display=item.insurer_display,
                    product_display=item.product_display,
                    display_label=item.display_label,
                    version=item.version,
                    primary_classification=item.primary_classification,
                    enrollment_confirmed=item.enrollment_confirmed,
                    has_product_explanation=item.has_product_explanation,
                    has_application=item.has_application,
                    source_count=item.source_count,
                    component_count=item.component_count,
                    items=tuple(
                        InventorySetItemResponse.from_domain(set_item) for set_item in item.items
                    ),
                )
                for item in inventory.unregistered_document_sets
            ),
            unpaired_components=tuple(
                InventoryComponentResponse.from_domain(item)
                for item in inventory.unpaired_components
            ),
            unreadable_sources=tuple(
                UnreadableSourceResponse.from_domain(item) for item in inventory.unreadable_sources
            ),
        )


class InsuranceDocumentComponentResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    document_batch_item_id: UUID
    role: DocumentRole
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    review_state: ReviewState
    version: int = Field(ge=1)

    @classmethod
    def from_domain(
        cls,
        component: InsuranceDocumentComponentRecord,
    ) -> InsuranceDocumentComponentResponse:
        return cls(**component.__dict__)


class InsuranceDocumentSetResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    member_id: UUID
    policy_contract_id: UUID | None
    insurer_display: str | None
    product_display: str | None
    display_label: str
    version: int = Field(ge=1)

    @classmethod
    def from_domain(
        cls,
        document_set: InsuranceDocumentSetRecord,
    ) -> InsuranceDocumentSetResponse:
        return cls(**document_set.__dict__)


class InsuranceDocumentSetItemMutationResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    insurance_document_set_id: UUID
    insurance_document_component_id: UUID
    role: DocumentRole
    match_state: ReviewState
    version: int = Field(ge=1)

    @classmethod
    def from_domain(
        cls,
        item: InsuranceDocumentSetItemRecord,
    ) -> InsuranceDocumentSetItemMutationResponse:
        return cls(**item.__dict__)


__all__ = [
    "ComponentCreateRequest",
    "DocumentSetCreateRequest",
    "DocumentSetItemCreateRequest",
    "ExpectedItemVersionRequest",
    "InsuranceDocumentComponentResponse",
    "InsuranceDocumentSetItemMutationResponse",
    "InsuranceDocumentSetResponse",
    "MemberInsuranceDocumentInventoryResponse",
]

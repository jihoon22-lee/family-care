"""HTTP contract for the member insurance-document inventory."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.errors import install_error_handlers
from familycare_api.insurance_documents.domain import (
    InsuranceDocumentComponentRecord,
    InsuranceDocumentSetItemRecord,
    InsuranceDocumentSetRecord,
    InventoryComponent,
    InventoryPolicy,
    InventorySet,
    UnreadableSource,
    build_member_inventory,
)
from familycare_api.insurance_documents.router import (
    get_insurance_document_service,
    router,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

MEMBER_ID = UUID("00000000-0000-4000-8000-000000000101")
POLICY_ID = UUID("00000000-0000-4000-8000-000000000201")
COMPONENT_ID = UUID("00000000-0000-4000-8000-000000000501")
SET_ID = UUID("00000000-0000-4000-8000-000000000601")
SET_ITEM_ID = UUID("00000000-0000-4000-8000-000000000701")
UNPAIRED_ITEM_ID = UUID("00000000-0000-4000-8000-000000000951")
UNPAIRED_VERSION_ID = UUID("00000000-0000-4000-8000-000000000952")


class _InventoryService:
    def get_inventory(self, member_id: UUID):
        assert member_id == MEMBER_ID
        return build_member_inventory(
            member_id,
            policies=(
                InventoryPolicy(
                    id=POLICY_ID,
                    source_document_version_id=UUID("00000000-0000-4000-8000-000000000301"),
                    source_content_sha256="a" * 64,
                    source_evidence_page=1,
                    insurer_display="Sample Insurer",
                    product_display="Sample Policy",
                    status="unknown",
                    rider_count=2,
                ),
            ),
            document_sets=(
                InventorySet(
                    id=UUID("00000000-0000-4000-8000-000000000401"),
                    policy_contract_id=None,
                    insurer_display="Sample Insurer",
                    product_display="Sample Terms",
                    display_label="Sample Terms",
                    version=1,
                    items=(),
                ),
            ),
            unreadable_sources=(
                UnreadableSource(
                    document_batch_item_id=UUID("00000000-0000-4000-8000-000000000801"),
                    source_kind="policy",
                    display_label="보험증권 문서",
                    processing_state="PASSWORD_REQUIRED",
                ),
            ),
        )

    def create_component(self, member_id: UUID, **values: object):
        assert member_id == MEMBER_ID
        assert values == {
            "document_batch_item_id": COMPONENT_ID,
            "role": "terms",
            "page_start": 2,
            "page_end": 4,
            "evidence_id": None,
            "review_state": "USER_CONFIRMED",
        }
        return InsuranceDocumentComponentRecord(
            id=COMPONENT_ID,
            document_batch_item_id=COMPONENT_ID,
            role="terms",
            page_start=2,
            page_end=4,
            review_state="USER_CONFIRMED",
            version=1,
        )

    def create_document_set(self, member_id: UUID, **values: object):
        assert member_id == MEMBER_ID
        assert values["policy_contract_id"] == POLICY_ID
        return InsuranceDocumentSetRecord(
            id=SET_ID,
            member_id=MEMBER_ID,
            policy_contract_id=POLICY_ID,
            insurer_display="Sample Insurer",
            product_display="Sample Policy",
            display_label="Sample Policy",
            version=1,
        )

    def attach_set_item(self, document_set_id: UUID, **values: object):
        assert document_set_id == SET_ID
        assert values["insurance_document_component_id"] == COMPONENT_ID
        assert values["expected_set_version"] == 1
        return InsuranceDocumentSetItemRecord(
            id=SET_ITEM_ID,
            insurance_document_set_id=SET_ID,
            insurance_document_component_id=COMPONENT_ID,
            role="terms",
            match_state="USER_CONFIRMED",
            version=1,
        )

    def detach_set_item(self, item_id: UUID, *, expected_version: int) -> None:
        assert item_id == SET_ITEM_ID
        assert expected_version == 1

    def delete_document_set(
        self,
        document_set_id: UUID,
        *,
        expected_version: int,
    ) -> None:
        assert document_set_id == SET_ID
        assert expected_version == 3


class _SyntheticInventoryService(_InventoryService):
    def get_inventory(self, member_id: UUID):
        inventory = super().get_inventory(member_id)
        return replace(
            inventory,
            unpaired_components=(
                InventoryComponent(
                    id=None,
                    document_batch_item_id=UNPAIRED_ITEM_ID,
                    document_version_id=UNPAIRED_VERSION_ID,
                    content_sha256="b" * 64,
                    role="policy",
                    page_start=1,
                    page_end=4,
                    review_state="SUGGESTED",
                    processing_state="READY",
                    duplicate_state="SAME_MEMBER_DUPLICATE",
                ),
            ),
        )


def _client(service_type: type = _InventoryService) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_insurance_document_service] = service_type
    return TestClient(app)


def test_inventory_get_is_member_scoped_no_store_and_path_free() -> None:
    with _client() as client:
        response = client.get(f"/api/v1/family-members/{MEMBER_ID}/insurance-document-inventory")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["schema_version"] == "1"
    assert payload["member_id"] == str(MEMBER_ID)
    assert payload["summary"] == {
        "application_documents": 0,
        "certificate_and_terms": 0,
        "certificate_backed_policies": 1,
        "certificate_only": 1,
        "pairing_conflicts": 0,
        "product_explanation_documents": 0,
        "terms_only_documents": 0,
        "unreadable_documents": 1,
    }
    assert payload["registered_policies"][0]["completeness"] == "CERTIFICATE_ONLY"
    assert payload["unregistered_document_sets"][0]["enrollment_confirmed"] is False
    assert payload["unreadable_sources"] == [
        {
            "document_batch_item_id": "00000000-0000-4000-8000-000000000801",
            "source_kind": "policy",
            "display_label": "보험증권 문서",
            "processing_state": "PASSWORD_REQUIRED",
        }
    ]
    serialized = json.dumps(payload, sort_keys=True).lower()
    for forbidden in (
        "source_key",
        "absolute_path",
        "archive_key",
        "policy_number",
        "raw_text",
        "/mnt/",
    ):
        assert forbidden not in serialized
    assert '"password":' not in serialized


def test_inventory_get_serializes_synthetic_unpaired_component() -> None:
    with _client(_SyntheticInventoryService) as client:
        response = client.get(f"/api/v1/family-members/{MEMBER_ID}/insurance-document-inventory")

    assert response.status_code == 200
    assert response.json()["unpaired_components"] == [
        {
            "id": None,
            "document_batch_item_id": str(UNPAIRED_ITEM_ID),
            "role": "policy",
            "page_start": 1,
            "page_end": 4,
            "review_state": "SUGGESTED",
            "processing_state": "READY",
            "duplicate_state": "SAME_MEMBER_DUPLICATE",
        }
    ]


def test_component_request_rejects_reversed_page_range_and_extra_fields() -> None:
    from familycare_api.insurance_documents.schemas import (
        ComponentCreateRequest,
        DocumentSetCreateRequest,
    )
    from pydantic import ValidationError

    try:
        ComponentCreateRequest.model_validate(
            {
                "document_batch_item_id": "00000000-0000-4000-8000-000000000501",
                "role": "terms",
                "page_start": 4,
                "page_end": 2,
                "source_key": "synthetic/private.pdf",
            }
        )
    except ValidationError as error:
        fields = {tuple(item["loc"]) for item in error.errors()}
        assert ("page_start",) in fields or ("source_key",) in fields
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("invalid component request accepted")

    with pytest.raises(ValidationError):
        DocumentSetCreateRequest.model_validate({"display_label": "synthetic/private/source.pdf"})


def test_reviewed_component_and_set_mutations_use_bounded_metadata() -> None:
    with _client() as client:
        component = client.post(
            f"/api/v1/family-members/{MEMBER_ID}/insurance-document-components",
            json={
                "document_batch_item_id": str(COMPONENT_ID),
                "role": "terms",
                "page_start": 2,
                "page_end": 4,
                "review_state": "USER_CONFIRMED",
            },
        )
        document_set = client.post(
            f"/api/v1/family-members/{MEMBER_ID}/insurance-document-sets",
            json={
                "policy_contract_id": str(POLICY_ID),
                "insurer_display": "Ignored input label",
                "product_display": "Ignored input product",
                "display_label": "Sample Policy",
            },
        )
        item = client.post(
            f"/api/v1/insurance-document-sets/{SET_ID}/items",
            json={
                "insurance_document_component_id": str(COMPONENT_ID),
                "match_state": "USER_CONFIRMED",
                "expected_set_version": 1,
            },
        )
        detached = client.request(
            "DELETE",
            f"/api/v1/insurance-document-set-items/{SET_ITEM_ID}",
            json={"expected_version": 1},
        )
        deleted_set = client.request(
            "DELETE",
            f"/api/v1/insurance-document-sets/{SET_ID}",
            json={"expected_version": 3},
        )

    assert component.status_code == 201
    assert component.json()["role"] == "terms"
    assert document_set.status_code == 201
    assert document_set.json()["insurer_display"] == "Sample Insurer"
    assert item.status_code == 201
    assert item.json()["match_state"] == "USER_CONFIRMED"
    assert detached.status_code == 204
    assert deleted_set.status_code == 204

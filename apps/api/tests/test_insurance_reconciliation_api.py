"""HTTP contract for the integrated insurance reconciliation boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from familycare_api.errors import install_error_handlers
from familycare_api.insurance_reconciliation.domain import (
    DocumentResolutionHistory,
    KnowledgeContractSource,
    OperationalLinkHistory,
    OperationalPolicySource,
    UnresolvedDocumentSource,
    build_member_reconciliation,
)
from familycare_api.insurance_reconciliation.router import (
    get_insurance_reconciliation_service,
    router,
)
from familycare_api.insurance_reconciliation.schemas import (
    DocumentResolutionRequest,
    OperationalLinkRequest,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

MEMBER_ID = UUID("00000000-0000-4000-8000-000000002701")
RUN_ID = UUID("00000000-0000-4000-8000-000000002702")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000002703")
POLICY_ID = UUID("00000000-0000-4000-8000-000000002704")
LINK_ID = UUID("00000000-0000-4000-8000-000000002705")
FAILED_ITEM_ID = UUID("00000000-0000-4000-8000-000000002706")
REPLACEMENT_ITEM_ID = UUID("00000000-0000-4000-8000-000000002707")
RESOLUTION_ID = UUID("00000000-0000-4000-8000-000000002708")
NOW = datetime(2026, 9, 1, 1, 2, 3, tzinfo=UTC)


def _link() -> OperationalLinkHistory:
    return OperationalLinkHistory(
        id=LINK_ID,
        knowledge_contract_id=CONTRACT_ID,
        policy_contract_id=POLICY_ID,
        decision="MATCH",
        conflict=False,
        authority="USER_CONFIRMED_OPERATIONAL_IDENTITY",
        reason_code="USER_CONFIRMED_SAME_CONTRACT",
        confirmed_at=NOW,
    )


class _Service:
    def get_member(self, member_id: UUID):
        assert member_id == MEMBER_ID
        return build_member_reconciliation(
            member_id=MEMBER_ID,
            knowledge_run_id=RUN_ID,
            generated_at=NOW,
            contracts=(
                KnowledgeContractSource(
                    id=CONTRACT_ID,
                    insurer_display="Sample Insurer",
                    product_display="Sample Policy",
                    certificate_decision="MATCH",
                    current_status="unknown",
                    snapshot_policy_contract_id=None,
                    snapshot_operational_decision="UNKNOWN",
                    snapshot_operational_reason_code="NO_EXACT_BINDING",
                ),
            ),
            current_links=(_link(),),
            operational_policies=(
                OperationalPolicySource(
                    id=POLICY_ID,
                    insurer_display="Sample Insurer",
                    product_display="Operational Policy",
                    status="unknown",
                    completeness="CERTIFICATE_ONLY",
                    has_product_explanation=False,
                    has_application=False,
                ),
            ),
            unresolved_sources=(
                UnresolvedDocumentSource(
                    document_batch_item_id=FAILED_ITEM_ID,
                    source_kind="policy",
                    display_label="보험증권 문서",
                    processing_state="PASSWORD_REQUIRED",
                    current_resolution_id=None,
                ),
            ),
        )

    def confirm_operational_link(self, contract_id: UUID, **values: object):
        assert contract_id == CONTRACT_ID
        assert values == {
            "decision": "MATCH",
            "conflict": False,
            "policy_contract_id": POLICY_ID,
            "reason_code": "USER_CONFIRMED_SAME_CONTRACT",
            "expected_current_link_id": None,
        }
        return _link()

    def confirm_document_resolution(self, item_id: UUID, **values: object):
        assert item_id == FAILED_ITEM_ID
        assert values == {
            "resolution": "REPLACED",
            "replacement_item_id": REPLACEMENT_ITEM_ID,
            "reason_code": "USER_CONFIRMED_REPLACEMENT",
            "expected_current_resolution_id": None,
        }
        return DocumentResolutionHistory(
            id=RESOLUTION_ID,
            failed_item_id=FAILED_ITEM_ID,
            replacement_item_id=REPLACEMENT_ITEM_ID,
            resolution="REPLACED",
            authority="USER_CONFIRMED_DOCUMENT_RESOLUTION",
            reason_code="USER_CONFIRMED_REPLACEMENT",
            confirmed_at=NOW,
        )


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_insurance_reconciliation_service] = _Service
    return TestClient(app)


def test_get_reconciliation_is_closed_no_store_and_private_field_free() -> None:
    with _client() as client:
        response = client.get(f"/api/v1/family-members/{MEMBER_ID}/insurance-reconciliation")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["schema_version"] == "1"
    assert payload["knowledge_run_id"] == str(RUN_ID)
    assert payload["summary"] == {
        "total_contracts": 1,
        "evidence_ready_contracts": 0,
        "documents_pending_contracts": 1,
        "link_review_required_contracts": 0,
        "conflict_contracts": 0,
        "orphan_operational_contracts": 0,
        "unresolved_unreadable_sources": 1,
    }
    assert payload["contracts"][0]["reconciliation_state"] == "DOCUMENTS_PENDING"
    assert payload["contracts"][0]["operational_link"]["id"] == str(LINK_ID)
    assert payload["contracts"][0]["document_readiness"]["completeness"] == ("CERTIFICATE_ONLY")
    assert payload["unresolved_sources"][0]["current_resolution_id"] is None
    serialized = json.dumps(payload, sort_keys=True).lower()
    for forbidden in (
        "source_key",
        "absolute_path",
        "archive_key",
        "policy_number",
        "raw_text",
        "digest",
        "family_alias",
        "/mnt/",
    ):
        assert forbidden not in serialized


def test_link_and_resolution_mutations_publish_only_bounded_history_metadata() -> None:
    with _client() as client:
        link = client.post(
            f"/api/v1/private-knowledge/current/contracts/{CONTRACT_ID}/operational-link",
            json={
                "decision": "MATCH",
                "conflict": False,
                "policy_contract_id": str(POLICY_ID),
                "reason_code": "USER_CONFIRMED_SAME_CONTRACT",
                "expected_current_link_id": None,
            },
        )
        resolution = client.post(
            f"/api/v1/document-batch-items/{FAILED_ITEM_ID}/resolution",
            json={
                "resolution": "REPLACED",
                "replacement_item_id": str(REPLACEMENT_ITEM_ID),
                "reason_code": "USER_CONFIRMED_REPLACEMENT",
                "expected_current_resolution_id": None,
            },
        )

    assert link.status_code == 200
    assert link.headers["cache-control"] == "no-store"
    assert link.json() == {
        "schema_version": "1",
        "id": str(LINK_ID),
        "knowledge_contract_id": str(CONTRACT_ID),
        "policy_contract_id": str(POLICY_ID),
        "decision": "MATCH",
        "conflict": False,
        "authority": "USER_CONFIRMED_OPERATIONAL_IDENTITY",
        "reason_code": "USER_CONFIRMED_SAME_CONTRACT",
        "confirmed_at": "2026-09-01T01:02:03Z",
    }
    assert resolution.status_code == 200
    assert resolution.headers["cache-control"] == "no-store"
    assert resolution.json()["resolution"] == "REPLACED"
    assert resolution.json()["replacement_item_id"] == str(REPLACEMENT_ITEM_ID)


def test_mutation_shapes_require_expected_id_and_exact_reason_state_pairs() -> None:
    with pytest.raises(ValidationError):
        OperationalLinkRequest.model_validate(
            {
                "decision": "MATCH",
                "conflict": False,
                "policy_contract_id": str(POLICY_ID),
                "reason_code": "USER_CONFIRMED_SAME_CONTRACT",
            }
        )
    with pytest.raises(ValidationError):
        OperationalLinkRequest.model_validate(
            {
                "decision": "UNKNOWN",
                "conflict": False,
                "policy_contract_id": str(POLICY_ID),
                "reason_code": "USER_REOPENED_OPERATIONAL_REVIEW",
                "expected_current_link_id": None,
            }
        )
    with pytest.raises(ValidationError):
        OperationalLinkRequest.model_validate(
            {
                "decision": "NO_MATCH",
                "conflict": False,
                "policy_contract_id": None,
                "reason_code": "USER_CONFIRMED_SAME_CONTRACT",
                "expected_current_link_id": None,
            }
        )
    with pytest.raises(ValidationError):
        DocumentResolutionRequest.model_validate(
            {
                "resolution": "DISMISSED",
                "replacement_item_id": str(REPLACEMENT_ITEM_ID),
                "reason_code": "USER_DISMISSED_STALE_FAILURE",
                "expected_current_resolution_id": None,
            }
        )
    with pytest.raises(ValidationError):
        DocumentResolutionRequest.model_validate(
            {
                "resolution": "REOPENED",
                "replacement_item_id": None,
                "reason_code": "USER_CONFIRMED_REPLACEMENT",
                "expected_current_resolution_id": None,
            }
        )

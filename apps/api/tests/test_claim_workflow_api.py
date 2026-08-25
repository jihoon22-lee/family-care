"""HTTP boundary tests for household-scoped claim tracking."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from familycare_api.claims.errors import ClaimNotFound, InvalidClaimTransitionError
from familycare_api.claims.router import get_claim_service, medical_event_claim_router, router
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.errors import install_error_handlers
from familycare_api.policies.errors import VersionConflict
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
EVENT_ID = UUID("00000000-0000-4000-8000-000000000201")
CLAIM_ID = UUID("00000000-0000-4000-8000-000000000301")
POLICY_ID = UUID("00000000-0000-4000-8000-000000000401")
CHECKLIST_ID = UUID("00000000-0000-4000-8000-000000000501")
RULE_ID = UUID("00000000-0000-4000-8000-000000000601")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000701")
RIDER_ID = UUID("00000000-0000-4000-8000-000000000901")
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _claim(*, status: str = "preparing", version: int = 1, deleted: bool = False) -> dict[str, Any]:
    transitions = {
        "preparing": ["submitted"],
        "submitted": ["denied", "paid", "partially_paid", "supplementation_requested"],
        "supplementation_requested": ["denied", "paid", "partially_paid", "submitted"],
        "paid": ["closed"],
        "partially_paid": ["closed"],
        "denied": ["closed"],
        "closed": [],
    }
    return {
        "schema_version": "1",
        "id": str(CLAIM_ID),
        "medical_event_id": str(EVENT_ID),
        "family_member_id": "00000000-0000-4000-8000-000000000801",
        "policy_contract_id": str(POLICY_ID),
        "insurer_key": "synthetic-insurer-a",
        "status": status,
        "receipt_number": None,
        "submitted_at": None,
        "claimed_amount": None,
        "paid_amount": None,
        "currency": None,
        "outcome_reason_code": None,
        "version": version,
        "deleted": deleted,
        "allowed_transitions": transitions[status],
        "snapshot": {
            "snapshot_version": 1,
            "snapshot_sha256": "a" * 64,
            "candidate": {"candidate_ids": ["00000000-0000-4000-8000-000000000901"]},
            "rules": {"rule_version_ids": [str(RULE_ID)]},
            "policy": {"policy_contract_id": str(POLICY_ID)},
            "evidence": {"evidence_ids": [str(EVIDENCE_ID)]},
            "calculation": {"calculation_ids": []},
        },
        "checklist": [
            {
                "id": str(CHECKLIST_ID),
                "document_kind": "medical_receipt",
                "requirement_code": "RECEIPT_REQUIRED",
                "required": True,
                "conditional": False,
                "prepared": False,
                "note_code": None,
                "source_rule_version_id": str(RULE_ID),
                "source_evidence_id": str(EVIDENCE_ID),
                "version": 1,
            }
        ],
        "status_events": [
            {
                "from_status": None,
                "to_status": "preparing",
                "occurred_at": NOW.isoformat(),
                "reason_code": "CLAIM_CREATED",
            }
        ],
    }


class _FakeClaimService:
    def __init__(self) -> None:
        self.version = 1
        self.status = "preparing"
        self.deleted = False

    def create_claim_case(self, event_id: UUID, request: object) -> dict[str, Any]:
        assert event_id == EVENT_ID
        assert request.rider_id == RIDER_ID  # type: ignore[attr-defined]
        return _claim()

    def list_claim_cases(self, **filters: object) -> dict[str, object]:
        assert filters["event_id"] in {None, EVENT_ID}
        return {"schema_version": "1", "items": [_claim()], "next_cursor": None}

    def get_claim_case(self, claim_id: UUID, *, deleted_only: bool = False) -> dict[str, Any]:
        if claim_id != CLAIM_ID or deleted_only != self.deleted:
            raise ClaimNotFound
        return _claim(status=self.status, version=self.version, deleted=self.deleted)

    def update_claim_case(self, claim_id: UUID, request: object) -> dict[str, Any]:
        assert claim_id == CLAIM_ID
        expected = request.expected_version  # type: ignore[attr-defined]
        if expected != self.version:
            raise VersionConflict
        self.version += 1
        value = _claim(status=self.status, version=self.version)
        value["receipt_number"] = getattr(request, "receipt_number", None)
        return value

    def transition_claim(self, claim_id: UUID, request: object) -> dict[str, Any]:
        assert claim_id == CLAIM_ID
        if request.expected_version != self.version:  # type: ignore[attr-defined]
            raise VersionConflict
        target = request.target_status  # type: ignore[attr-defined]
        if self.status == "preparing" and target != "submitted":
            raise InvalidClaimTransitionError
        self.status = target
        self.version += 1
        return _claim(status=self.status, version=self.version)

    def update_checklist_item(
        self, claim_id: UUID, item_id: UUID, request: object
    ) -> dict[str, Any]:
        assert (claim_id, item_id) == (CLAIM_ID, CHECKLIST_ID)
        value = _claim()
        value["checklist"][0]["prepared"] = request.prepared  # type: ignore[index, attr-defined]
        value["checklist"][0]["version"] = 2  # type: ignore[index]
        return value

    def delete_claim_case(self, claim_id: UUID, *, expected_version: int) -> None:
        if claim_id != CLAIM_ID or expected_version != self.version:
            raise VersionConflict
        self.deleted = True
        self.version += 1

    def restore_claim_case(self, claim_id: UUID, *, expected_version: int) -> dict[str, Any]:
        if claim_id != CLAIM_ID or expected_version != self.version:
            raise VersionConflict
        self.deleted = False
        self.version += 1
        return _claim(status=self.status, version=self.version)


@pytest.fixture()
def service() -> _FakeClaimService:
    return _FakeClaimService()


@pytest.fixture()
def client(service: _FakeClaimService) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(medical_event_claim_router)
    app.include_router(router)
    app.dependency_overrides[get_claim_service] = lambda: service
    app.dependency_overrides[resolve_household_scope] = lambda: SCOPE
    with TestClient(app) as test_client:
        yield test_client


def test_create_and_list_claims_without_client_scope(client: TestClient) -> None:
    created = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/claims",
        json={"rider_id": str(RIDER_ID)},
    )
    listed = client.get(f"/api/v1/claims?event_id={EVENT_ID}&status=preparing")

    assert created.status_code == 201
    assert created.json()["status"] == "preparing"
    assert created.json()["allowed_transitions"] == ["submitted"]
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == str(CLAIM_ID)
    assert "household_space_id" not in created.text + listed.text
    assert created.headers["cache-control"] == listed.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "field",
    ["status", "snapshot", "file_path", "document_text", "household_space_id"],
)
def test_claim_metadata_patch_rejects_status_snapshot_files_and_scope(
    client: TestClient, field: str
) -> None:
    response = client.patch(
        f"/api/v1/claims/{CLAIM_ID}",
        json={"expected_version": 1, field: "SYNTHETIC_PRIVATE_VALUE"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert "SYNTHETIC_PRIVATE_VALUE" not in response.text


def test_transition_requires_expected_version_and_allowed_target(client: TestClient) -> None:
    denied = client.post(
        f"/api/v1/claims/{CLAIM_ID}/transitions",
        json={
            "target_status": "paid",
            "expected_version": 1,
            "occurred_at": NOW.isoformat(),
            "metadata": {"amount": "1000.00", "currency": "KRW", "payment_date": "2026-08-26"},
        },
    )
    submitted = client.post(
        f"/api/v1/claims/{CLAIM_ID}/transitions",
        json={
            "target_status": "submitted",
            "expected_version": 1,
            "occurred_at": NOW.isoformat(),
            "metadata": {},
        },
    )
    stale = client.post(
        f"/api/v1/claims/{CLAIM_ID}/transitions",
        json={
            "target_status": "denied",
            "expected_version": 1,
            "occurred_at": NOW.isoformat(),
            "metadata": {"reason_code": "SYNTHETIC_DENIAL"},
        },
    )

    assert denied.status_code == 409
    assert denied.json()["error_code"] == "INVALID_CLAIM_TRANSITION"
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "VERSION_CONFLICT"


def test_transition_rejects_timestamp_without_timezone(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/claims/{CLAIM_ID}/transitions",
        json={
            "target_status": "submitted",
            "expected_version": 1,
            "occurred_at": "2026-08-26T09:00:00",
            "metadata": {},
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("target", ["paid", "partially_paid"])
def test_payment_transition_requires_decimal_amount_currency_and_date(
    client: TestClient, service: _FakeClaimService, target: str
) -> None:
    service.status = "submitted"
    missing = client.post(
        f"/api/v1/claims/{CLAIM_ID}/transitions",
        json={
            "target_status": target,
            "expected_version": 1,
            "occurred_at": NOW.isoformat(),
            "metadata": {},
        },
    )
    negative = client.post(
        f"/api/v1/claims/{CLAIM_ID}/transitions",
        json={
            "target_status": target,
            "expected_version": 1,
            "occurred_at": NOW.isoformat(),
            "metadata": {"amount": "-1.00", "currency": "KRW", "payment_date": "2026-08-26"},
        },
    )

    assert missing.status_code == negative.status_code == 422
    assert "-1.00" not in negative.text


def test_checklist_payload_is_metadata_only(client: TestClient) -> None:
    updated = client.patch(
        f"/api/v1/claims/{CLAIM_ID}/checklist/{CHECKLIST_ID}",
        json={"expected_version": 1, "prepared": True, "note_code": "USER_PREPARED"},
    )
    rejected = client.patch(
        f"/api/v1/claims/{CLAIM_ID}/checklist/{CHECKLIST_ID}",
        json={"expected_version": 1, "prepared": True, "file_path": "/synthetic/private"},
    )

    assert updated.status_code == 200
    assert updated.json()["checklist"][0]["prepared"] is True
    assert rejected.status_code == 422
    assert "/synthetic/private" not in rejected.text


def test_soft_delete_and_restore_use_optimistic_version(
    client: TestClient, service: _FakeClaimService
) -> None:
    deleted = client.request("DELETE", f"/api/v1/claims/{CLAIM_ID}", json={"expected_version": 1})
    restored = client.post(f"/api/v1/claims/{CLAIM_ID}/restore", json={"expected_version": 2})

    assert deleted.status_code == 204
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False
    assert service.deleted is False

"""HTTP contract tests for optional MedicalEvent structuring jobs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.errors import MedicalEventNotFound
from familycare_api.decisions.router import (
    get_event_structuring_service,
    router,
    structuring_job_router,
)
from familycare_api.errors import install_error_handlers
from familycare_api.policies.errors import VersionConflict
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
EVENT_ID = UUID("00000000-0000-4000-8000-000000000201")
JOB_ID = UUID("00000000-0000-4000-8000-000000000301")
FACT_ID = UUID("00000000-0000-4000-8000-000000000401")


def _job_payload(*, state: str = "queued", error_code: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "job_id": str(JOB_ID),
        "state": state,
        "attempts": 0 if state == "queued" else 1,
        "facts": []
        if state != "succeeded"
        else [
            {
                "fact_id": str(FACT_ID),
                "field_id": "condition_class",
                "value": "synthetic-condition",
                "source": "ai",
                "state": "confirmed",
                "confidence": "medium",
                "evidence_ids": [],
            }
        ],
        "questions": []
        if state != "succeeded"
        else [{"question_code": "admission", "field_id": "admission"}],
        "issues": [],
        "error_code": error_code,
    }


class _FakeStructuringService:
    def __init__(self) -> None:
        self.visible = True
        self.version = 1
        self.enqueued: list[tuple[UUID, int]] = []
        self.jobs: dict[UUID, dict[str, Any]] = {JOB_ID: _job_payload()}

    def enqueue(self, event_id: UUID, *, expected_version: int) -> dict[str, Any]:
        if not self.visible:
            raise MedicalEventNotFound
        if expected_version != self.version:
            raise VersionConflict
        self.enqueued.append((event_id, expected_version))
        return {
            "schema_version": "1",
            "job_id": str(JOB_ID),
            "state": "queued",
            "status_url": f"/api/v1/medical-event-structuring-jobs/{JOB_ID}",
        }

    def get_job(self, job_id: UUID) -> dict[str, Any]:
        if not self.visible or job_id not in self.jobs:
            raise MedicalEventNotFound
        return self.jobs[job_id]


@pytest.fixture()
def service() -> _FakeStructuringService:
    return _FakeStructuringService()


@pytest.fixture()
def client(service: _FakeStructuringService) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(structuring_job_router)
    app.dependency_overrides[get_event_structuring_service] = lambda: service
    app.dependency_overrides[resolve_household_scope] = lambda: SCOPE
    with TestClient(app) as test_client:
        yield test_client


def test_structure_enqueues_scoped_job_with_expected_version(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/structure",
        json={"expected_version": 1},
    )

    assert response.status_code == 202
    assert response.json() == {
        "schema_version": "1",
        "job_id": str(JOB_ID),
        "state": "queued",
        "status_url": f"/api/v1/medical-event-structuring-jobs/{JOB_ID}",
    }
    assert response.headers["cache-control"] == "no-store"


def test_structure_rejects_stale_or_cross_scope_event_without_values(
    client: TestClient,
    service: _FakeStructuringService,
) -> None:
    stale = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/structure",
        json={"expected_version": 2},
    )
    service.visible = False
    hidden = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/structure",
        json={"expected_version": 1},
    )

    assert stale.status_code == 409
    assert hidden.status_code == 404
    assert "synthetic" not in stale.text.lower() + hidden.text.lower()
    assert stale.headers["cache-control"] == hidden.headers["cache-control"] == "no-store"


def test_get_structuring_job_returns_only_validated_candidates(client: TestClient) -> None:
    service = client.app.dependency_overrides[get_event_structuring_service]()
    service.jobs[JOB_ID] = _job_payload(state="succeeded")

    response = client.get(f"/api/v1/medical-event-structuring-jobs/{JOB_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["facts"][0]["field_id"] == "condition_class"
    assert body["questions"] == [{"question_code": "admission", "field_id": "admission"}]
    serialized = response.text.lower()
    for forbidden in (
        "situation",
        "provider_request_id",
        "household_space_id",
        "decision",
        "tri_state",
        "amount",
        "payment",
        "password",
        "/mnt/",
    ):
        assert forbidden not in serialized
    assert response.headers["cache-control"] == "no-store"


def test_failed_structuring_job_is_safe_and_does_not_claim_decision_authority(
    client: TestClient,
) -> None:
    service = client.app.dependency_overrides[get_event_structuring_service]()
    service.jobs[JOB_ID] = _job_payload(
        state="retryable_failed",
        error_code="STRUCTURING_PROVIDER_TIMEOUT",
    )

    response = client.get(f"/api/v1/medical-event-structuring-jobs/{JOB_ID}")

    assert response.status_code == 200
    assert response.json()["state"] == "retryable_failed"
    assert response.json()["facts"] == []
    assert response.json()["error_code"] == "STRUCTURING_PROVIDER_TIMEOUT"
    assert "traceback" not in response.text.lower()


def test_structuring_job_scope_denial_is_not_found(
    client: TestClient,
    service: _FakeStructuringService,
) -> None:
    service.visible = False

    response = client.get(f"/api/v1/medical-event-structuring-jobs/{JOB_ID}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"

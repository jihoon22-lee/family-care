from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.router import get_decision_service, router
from familycare_api.errors import ApiBoundaryError, install_error_handlers
from familycare_api.policies.errors import VersionConflict
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCOPE_A = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
EVENT_ID = UUID("00000000-0000-4000-8000-000000000201")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000202")
RUN_ONE_ID = UUID("00000000-0000-4000-8000-000000000301")
RUN_TWO_ID = UUID("00000000-0000-4000-8000-000000000302")
RIDER_ID = UUID("00000000-0000-4000-8000-000000000401")
RULE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000501")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000601")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000602")
RAW_SENTINEL = "SYNTHETIC_RAW_EVENT_DESCRIPTION"


class MedicalEventNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "MEDICAL_EVENT_NOT_FOUND"
    public_message = "medical event not found"


def _event_request(
    mode: str = "post_treatment",
    *,
    event_date: str | None = "2026-08-25",
    visit_date: str | None = "2026-08-25",
) -> dict[str, Any]:
    return {
        "family_member_id": str(MEMBER_ID),
        "mode": mode,
        "event_date": event_date,
        "visit_date": visit_date,
        "facts": {
            "MedicalEvent.classification": {
                "value": "injury",
                "confirmation": "user",
            }
        },
    }


def _event_payload(*, version: int = 1, deleted: bool = False) -> dict[str, Any]:
    return {
        "id": str(EVENT_ID),
        "family_member_id": str(MEMBER_ID),
        "mode": "post_treatment",
        "event_date": "2026-08-25",
        "visit_date": "2026-08-25",
        "facts": {
            "MedicalEvent.classification": {
                "value": "injury",
                "confirmation": "user",
            }
        },
        "version": version,
        "deleted": deleted,
    }


def _decision_payload(*, run_id: UUID, event_version: int) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "run_id": str(run_id),
        "medical_event_id": str(EVENT_ID),
        "event_version": event_version,
        "engine_version": "decision-engine-v1",
        "rule_set_version": "coverage-rules-v1",
        "policy_snapshot_at": "2026-08-25T09:00:00Z",
        "stale": False,
        "candidates": [
            {
                "candidate_id": str(UUID("00000000-0000-4000-8000-000000000701")),
                "rider_id": str(RIDER_ID),
                "rider_type": "fixed",
                "aggregate_result": "UNKNOWN",
                "required_match_count": 0,
                "required_unknown_count": 1,
                "required_no_match_count": 0,
                "questions": [
                    {
                        "field_path": "MedicalEvent.classification",
                        "reason_code": "MISSING_OR_CONFLICTING_FACT",
                    }
                ],
                "hold_reason_codes": ["MISSING_OR_CONFLICTING_FACT"],
            }
        ],
        "evaluations": [
            {
                "evaluation_id": str(UUID("00000000-0000-4000-8000-000000000702")),
                "rider_id": str(RIDER_ID),
                "rule_version_id": str(RULE_VERSION_ID),
                "result": "UNKNOWN",
                "required": True,
                "reason_code": "MISSING_OR_CONFLICTING_FACT",
                "fact_paths": ["MedicalEvent.classification"],
                "missing_fields": ["MedicalEvent.classification"],
                "conflicting_fields": [],
                "evidence": [
                    {
                        "evidence_id": str(EVIDENCE_ID),
                        "document_version_id": str(DOCUMENT_VERSION_ID),
                        "extraction_id": str(UUID("00000000-0000-4000-8000-000000000703")),
                        "content_sha256": "a" * 64,
                        "physical_page": 1,
                        "bbox": None,
                        "review_state": "AI_VERIFIED",
                    }
                ],
                "engine_version": "decision-engine-v1",
            }
        ],
    }


class _FakeDecisionService:
    """Dependency-isolated service contract used by the HTTP RED tests."""

    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self.version = 1
        self.deleted = False
        self.created_requests: list[object] = []
        self.updated_requests: list[tuple[UUID, object]] = []
        self.deleted_requests: list[tuple[UUID, int]] = []
        self.restored_requests: list[tuple[UUID, int]] = []
        self.analysis_calls: list[UUID] = []
        self.results: dict[int, dict[str, Any]] = {}
        self.analysis_version = 0

    @staticmethod
    def _request_data(request: object) -> Mapping[str, object]:
        if hasattr(request, "model_dump"):
            dumped = request.model_dump(mode="python")  # type: ignore[attr-defined]
            if isinstance(dumped, Mapping):
                return dumped
        if isinstance(request, Mapping):
            return request
        return {}

    def create_medical_event(self, request: object) -> dict[str, Any]:
        self.created_requests.append(request)
        data = self._request_data(request)
        mode = data.get("mode", "post_treatment")
        event_date = data.get("event_date", "2026-08-25")
        visit_date = data.get("visit_date", "2026-08-25")
        response = _event_payload(version=1)
        response.update(
            {
                "mode": mode,
                "event_date": event_date,
                "visit_date": visit_date,
            }
        )
        return response

    def update_medical_event(self, event_id: UUID, request: object) -> dict[str, Any]:
        self._require_visible()
        self.updated_requests.append((event_id, request))
        data = self._request_data(request)
        expected_version = data.get("expected_version")
        if expected_version != self.version:
            raise VersionConflict
        self.version += 1
        return _event_payload(version=self.version)

    def analyze_medical_event(self, event_id: UUID) -> dict[str, Any]:
        self._require_visible()
        self.analysis_calls.append(event_id)
        self.analysis_version += 1
        run_id = RUN_ONE_ID if self.analysis_version == 1 else RUN_TWO_ID
        result = _decision_payload(run_id=run_id, event_version=self.version)
        self.results[self.analysis_version] = result
        return result

    def get_decision_result(self, event_id: UUID, version: int) -> dict[str, Any]:
        self._require_visible()
        result = self.results.get(version)
        if result is None:
            raise MedicalEventNotFound
        return result

    def delete_medical_event(self, event_id: UUID, *, expected_version: int) -> None:
        self._require_visible()
        if expected_version != self.version:
            raise VersionConflict
        self.deleted_requests.append((event_id, expected_version))
        self.deleted = True
        self.version += 1

    def list_deleted_medical_events(self) -> list[dict[str, Any]]:
        return [_event_payload(version=self.version, deleted=True)] if self.deleted else []

    def restore_medical_event(
        self,
        event_id: UUID,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        if not self.visible or not self.deleted:
            raise MedicalEventNotFound
        if expected_version != self.version:
            raise VersionConflict
        self.restored_requests.append((event_id, expected_version))
        self.deleted = False
        self.version += 1
        return _event_payload(version=self.version, deleted=False)

    def _require_visible(self) -> None:
        if not self.visible or self.deleted:
            raise MedicalEventNotFound


@pytest.fixture()
def service() -> _FakeDecisionService:
    return _FakeDecisionService()


@pytest.fixture()
def client(service: _FakeDecisionService) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_decision_service] = lambda: service
    app.dependency_overrides[resolve_household_scope] = lambda: SCOPE_A
    with TestClient(app) as test_client:
        yield test_client


def _assert_no_store(response: Any) -> None:
    assert response.headers.get("cache-control") == "no-store"


def _assert_value_free_error(response: Any, *, forbidden: str | None = None) -> None:
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert "detail" not in response.json()
    if forbidden is not None:
        assert forbidden not in response.text
    _assert_no_store(response)


def _assert_safe_result(response: Any) -> None:
    serialized = response.text.lower()
    for forbidden in (
        "amount",
        "guarantee",
        "guaranteed",
        "payment guaranteed",
        "지급 보장",
        "document_text",
        "/mnt/",
        "c:\\",
        RAW_SENTINEL.lower(),
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("mode", "event_date", "visit_date"),
    [
        ("pre_visit", None, None),
        ("post_treatment", "2026-08-25", "2026-08-25"),
    ],
)
def test_create_medical_event_supports_pre_and_post_modes(
    client: TestClient,
    service: _FakeDecisionService,
    mode: str,
    event_date: str | None,
    visit_date: str | None,
) -> None:
    response = client.post(
        "/api/v1/medical-events",
        json=_event_request(mode, event_date=event_date, visit_date=visit_date),
    )

    assert response.status_code == 201
    assert response.json()["mode"] == mode
    assert response.json()["version"] == 1
    assert "household_space_id" not in response.json()
    assert len(service.created_requests) == 1
    _assert_no_store(response)
    _assert_safe_result(response)


@pytest.mark.parametrize(
    "body",
    [
        {**_event_request(), "description": RAW_SENTINEL},
        {**_event_request(), "result": "MATCH"},
        {**_event_request(), "amount": 1000},
        {**_event_request(), "household_space_id": str(SCOPE_A.household_space_id)},
        {
            **_event_request(),
            "facts": {
                "MedicalEvent.classification": {
                    "value": "injury",
                    "confirmation": "user",
                    "evidence_ids": [],
                }
            },
        },
        {
            **_event_request(),
            "facts": {
                "MedicalEvent.event_date": {
                    "value": "2026-08-25",
                    "confirmation": "user",
                }
            },
        },
        {
            **_event_request(),
            "facts": {
                "MedicalEvent.classification": {
                    "value": "x" * 161,
                    "confirmation": "user",
                }
            },
        },
    ],
)
def test_create_rejects_raw_description_tri_state_amount_and_client_scope(
    client: TestClient,
    body: dict[str, Any],
) -> None:
    response = client.post("/api/v1/medical-events", json=body)

    _assert_value_free_error(response, forbidden=RAW_SENTINEL)


def test_patch_requires_expected_version_and_returns_conflict_for_stale_write(
    client: TestClient,
    service: _FakeDecisionService,
) -> None:
    updated = client.patch(
        f"/api/v1/medical-events/{EVENT_ID}",
        json={
            "expected_version": 1,
            "facts": _event_request()["facts"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    _assert_no_store(updated)

    stale = client.patch(
        f"/api/v1/medical-events/{EVENT_ID}",
        json={
            "expected_version": 1,
            "facts": _event_request()["facts"],
        },
    )
    assert stale.status_code == 409
    assert stale.json() == {"error_code": "VERSION_CONFLICT", "message": "version conflict"}
    assert service.updated_requests
    _assert_no_store(stale)


@pytest.mark.parametrize("field", ["facts", "mode"])
def test_patch_rejects_explicit_null_for_non_nullable_fields(
    client: TestClient,
    field: str,
) -> None:
    response = client.patch(
        f"/api/v1/medical-events/{EVENT_ID}",
        json={"expected_version": 1, field: None},
    )

    _assert_value_free_error(response)


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/medical-events/{EVENT_ID}",
        f"/api/v1/medical-events/{EVENT_ID}/analyze",
        f"/api/v1/medical-events/{EVENT_ID}/results/1",
    ],
)
def test_missing_or_cross_scope_event_is_404(
    client: TestClient,
    service: _FakeDecisionService,
    path: str,
) -> None:
    service.visible = False
    if path.endswith("/analyze"):
        response = client.post(path)
    elif "/results/" in path:
        response = client.get(path)
    else:
        response = client.patch(
            path,
            json={"expected_version": 1, "facts": _event_request()["facts"]},
        )

    assert response.status_code == 404
    assert "medical event" not in response.text.lower() or "not found" in response.text.lower()
    _assert_no_store(response)


def test_analyze_returns_unknown_normally_with_exact_versions_and_evidence(
    client: TestClient,
    service: _FakeDecisionService,
) -> None:
    response = client.post(f"/api/v1/medical-events/{EVENT_ID}/analyze")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == str(RUN_ONE_ID)
    assert body["event_version"] == 1
    assert body["engine_version"] == "decision-engine-v1"
    assert body["rule_set_version"] == "coverage-rules-v1"
    assert body["policy_snapshot_at"] == "2026-08-25T09:00:00Z"
    assert body["candidates"][0]["aggregate_result"] == "UNKNOWN"
    assert "evaluations" not in body["candidates"][0]
    evaluation = body["evaluations"][0]
    assert evaluation["rule_version_id"] == str(RULE_VERSION_ID)
    assert evaluation["result"] == "UNKNOWN"
    assert evaluation["evidence"][0]["evidence_id"] == str(EVIDENCE_ID)
    assert evaluation["evidence"][0]["extraction_id"]
    assert evaluation["evidence"][0]["content_sha256"] == "a" * 64
    assert evaluation["evidence"][0]["bbox"] is None
    assert service.analysis_calls == [EVENT_ID]
    _assert_no_store(response)
    _assert_safe_result(response)


def test_get_result_by_version_preserves_previous_run_after_reanalysis(
    client: TestClient,
) -> None:
    first = client.post(f"/api/v1/medical-events/{EVENT_ID}/analyze")
    updated = client.patch(
        f"/api/v1/medical-events/{EVENT_ID}",
        json={"expected_version": 1, "facts": _event_request()["facts"]},
    )
    second = client.post(f"/api/v1/medical-events/{EVENT_ID}/analyze")
    old = client.get(f"/api/v1/medical-events/{EVENT_ID}/results/1")
    current = client.get(f"/api/v1/medical-events/{EVENT_ID}/results/2")

    assert first.status_code == updated.status_code == second.status_code == 200
    assert old.status_code == current.status_code == 200
    assert old.json()["run_id"] == first.json()["run_id"]
    assert old.json()["event_version"] == 1
    assert current.json()["run_id"] == second.json()["run_id"]
    assert current.json()["event_version"] == 2
    _assert_no_store(old)
    _assert_no_store(current)
    _assert_safe_result(old)
    _assert_safe_result(current)


def test_get_missing_result_version_is_404(
    client: TestClient, service: _FakeDecisionService
) -> None:
    service.results.clear()
    response = client.get(f"/api/v1/medical-events/{EVENT_ID}/results/999")

    assert response.status_code == 404
    _assert_no_store(response)


def test_result_version_must_be_positive(client: TestClient) -> None:
    response = client.get(f"/api/v1/medical-events/{EVENT_ID}/results/0")

    _assert_value_free_error(response)


def test_medical_event_delete_trash_and_restore_are_explicitly_versioned(
    client: TestClient,
    service: _FakeDecisionService,
) -> None:
    deleted = client.request(
        "DELETE",
        f"/api/v1/medical-events/{EVENT_ID}",
        json={"expected_version": 1},
    )
    assert deleted.status_code == 204
    assert service.deleted_requests == [(EVENT_ID, 1)]
    _assert_no_store(deleted)

    trash = client.get("/api/v1/medical-events/trash")
    assert trash.status_code == 200
    assert trash.json()[0]["id"] == str(EVENT_ID)
    assert trash.json()[0]["version"] == 2
    assert trash.json()[0]["deleted"] is True
    _assert_no_store(trash)

    restored = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/restore",
        json={"expected_version": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
    assert restored.json()["deleted"] is False
    assert service.restored_requests == [(EVENT_ID, 2)]
    _assert_no_store(restored)


def test_delete_requires_json_expected_version(client: TestClient) -> None:
    response = client.request("DELETE", f"/api/v1/medical-events/{EVENT_ID}")

    _assert_value_free_error(response)

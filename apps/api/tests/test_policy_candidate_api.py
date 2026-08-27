"""HTTP contract and use-case tests for policy candidate review.

These tests deliberately use an in-memory service.  They exercise the HTTP
boundary, strict request/response models, and the service call contract
without requiring PostgreSQL or a real document.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from copy import deepcopy
from typing import Annotated, Any
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.errors import ApiBoundaryError, install_error_handlers
from familycare_api.policies.candidate_models import (
    CandidateConfirmationRequest,
    CandidateCorrectionRequest,
    CandidateRejectionRequest,
    PolicyReviewItem,
)
from familycare_api.policies.candidate_router import (
    get_candidate_review_service,
    router,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

SCOPE_A = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
SCOPE_B = HouseholdScope(UUID("00000000-0000-4000-8000-000000000102"))
_POLICY_ID = UUID("00000000-0000-4000-8000-000000000201")
_TERMS_POLICY_ID = UUID("00000000-0000-4000-8000-000000000202")
_REVIEW_ITEM_ID = UUID("00000000-0000-4000-8000-000000000301")
_TERMS_REVIEW_ITEM_ID = UUID("00000000-0000-4000-8000-000000000302")
_PARENT_CANDIDATE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000401")
_TERMS_CANDIDATE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000402")
_CHILD_CANDIDATE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000403")
_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000501")
_TERMS_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000502")
_POLICY_DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000601")
_TERMS_DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000602")
_ACTOR_ID = UUID("00000000-0000-4000-8000-000000000701")
_UNKNOWN_ID = UUID("00000000-0000-4000-8000-000000000799")
_MEMBER_A_ID = UUID("00000000-0000-4000-8000-000000000801")
_MEMBER_B_ID = UUID("00000000-0000-4000-8000-000000000802")

_PRIVATE_MARKERS = (
    "synthetic-private-password",
    "/synthetic/private/policy.pdf",
    "synthetic-policy-number-private",
    "synthetic document body private",
    "synthetic raw provider response private",
)
_FORBIDDEN_RESPONSE_FIELDS = {
    "absolute_path",
    "archive_key",
    "document_text",
    "household_space_id",
    "password",
    "policy_number",
    "prompt",
    "raw_pdf",
    "raw_provider_response",
    "source_path",
}
ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


class _ReviewItemNotFound(ApiBoundaryError):
    status_code = 404
    error_code: Any = "REVIEW_ITEM_NOT_FOUND"
    public_message = "review item not found"


class _CandidateVersionConflict(ApiBoundaryError):
    status_code = 409
    error_code = "VERSION_CONFLICT"
    public_message = "version conflict"


class _InvalidCandidateCorrection(ApiBoundaryError):
    status_code = 422
    error_code: Any = "INVALID_CANDIDATE_CORRECTION"
    public_message = "candidate correction is invalid"


def _review_item(
    *,
    review_item_id: UUID,
    candidate_version_id: UUID,
    aggregate_id: UUID,
    document_version_id: UUID,
    evidence_id: UUID,
    document_label: str,
    status: str = "NEEDS_REVIEW",
    issue_code: str | None = None,
) -> PolicyReviewItem:
    issues: list[dict[str, str | None]] = []
    if issue_code is not None:
        issues.append({"code": issue_code, "field_id": "rider_name"})
    return PolicyReviewItem.model_validate(
        {
            "review_item_id": str(review_item_id),
            "candidate_version_id": str(candidate_version_id),
            "aggregate_id": str(aggregate_id),
            "candidate_kind": "rider",
            "status": status,
            "fields": [
                {
                    "field_id": "rider_name",
                    "value": "Sample Hospital Benefit",
                    "evidence_ids": [str(evidence_id)],
                },
                {
                    "field_id": "rider_status",
                    "value": "active",
                    "evidence_ids": [str(evidence_id)],
                },
            ],
            "evidence": [
                {
                    "evidence_id": str(evidence_id),
                    "document_version_id": str(document_version_id),
                    "document_label": document_label,
                    "page": 1,
                    "bbox": [72, 120, 480, 180],
                    "bounded_excerpt": (
                        "Sample Hospital Benefit is described in this synthetic record."
                    ),
                }
            ],
            "issues": issues,
            "expected_version": 1,
        }
    )


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in _FORBIDDEN_RESPONSE_FIELDS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


class _FakeCandidateReviewService:
    """Small stateful fake exposing the service contract to the router."""

    def __init__(self) -> None:
        parent = _review_item(
            review_item_id=_REVIEW_ITEM_ID,
            candidate_version_id=_PARENT_CANDIDATE_VERSION_ID,
            aggregate_id=_POLICY_ID,
            document_version_id=_POLICY_DOCUMENT_VERSION_ID,
            evidence_id=_EVIDENCE_ID,
            document_label="Sample Policy",
        )
        terms_only = _review_item(
            review_item_id=_TERMS_REVIEW_ITEM_ID,
            candidate_version_id=_TERMS_CANDIDATE_VERSION_ID,
            aggregate_id=_TERMS_POLICY_ID,
            document_version_id=_TERMS_DOCUMENT_VERSION_ID,
            evidence_id=_TERMS_EVIDENCE_ID,
            document_label="Sample Terms",
            issue_code="TERMS_ONLY_RIDER",
        )
        self.items: dict[UUID, PolicyReviewItem] = {
            _REVIEW_ITEM_ID: parent,
            _TERMS_REVIEW_ITEM_ID: terms_only,
        }
        self.parent_versions: dict[UUID, PolicyReviewItem] = {_PARENT_CANDIDATE_VERSION_ID: parent}
        self.seen_scopes: list[HouseholdScope] = []
        self.list_calls: list[tuple[HouseholdScope, str]] = []
        self.list_member_ids: list[UUID | None] = []
        self.get_calls: list[tuple[HouseholdScope, UUID]] = []
        self.correction_targets: list[UUID] = []
        self.confirm_calls: list[tuple[HouseholdScope, UUID]] = []
        self.reject_calls: list[tuple[HouseholdScope, UUID]] = []
        self.published_rider_ids: list[UUID] = []
        self.informational_confirmations: list[UUID] = []

    def _scope(self, scope: HouseholdScope) -> None:
        self.seen_scopes.append(scope)

    def _item(self, scope: HouseholdScope, review_item_id: UUID) -> PolicyReviewItem:
        self._scope(scope)
        item = self.items.get(review_item_id)
        if scope != SCOPE_A or item is None:
            raise _ReviewItemNotFound
        return item

    def _correction_item(self, identifier: UUID) -> tuple[UUID, PolicyReviewItem]:
        # The documented PATCH path uses policy_id, while the shared service
        # signature names the lookup review_item_id.  The fake accepts either
        # identifier so the route test keeps this boundary visible.
        review_item_id = _REVIEW_ITEM_ID if identifier == _POLICY_ID else identifier
        item = self.items.get(review_item_id)
        if item is None:
            raise _ReviewItemNotFound
        return review_item_id, item

    def list_review_items(
        self,
        *,
        scope: HouseholdScope,
        status: str = "NEEDS_REVIEW",
        family_member_id: UUID | None = None,
        **_: Any,
    ) -> list[PolicyReviewItem]:
        self._scope(scope)
        self.list_calls.append((scope, status))
        self.list_member_ids.append(family_member_id)
        if scope != SCOPE_A:
            return []
        items = [item for item in self.items.values() if item.status == status]
        if family_member_id == _MEMBER_A_ID:
            return [items[0]]
        if family_member_id == _MEMBER_B_ID:
            return [items[1]]
        return items

    def get_review_item(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
        **_: Any,
    ) -> PolicyReviewItem:
        self.get_calls.append((scope, review_item_id))
        return self._item(scope, review_item_id)

    def correct_field(
        self,
        *,
        scope: HouseholdScope,
        request: CandidateCorrectionRequest,
        review_item_id: UUID | None = None,
        policy_id: UUID | None = None,
        **_: Any,
    ) -> PolicyReviewItem:
        self._scope(scope)
        identifier = policy_id or review_item_id
        if identifier is None or scope != SCOPE_A:
            raise _ReviewItemNotFound
        self.correction_targets.append(identifier)
        resolved_review_item_id, item = self._correction_item(identifier)
        if request.expected_version != item.expected_version:
            raise _CandidateVersionConflict
        evidence_ids = {evidence.evidence_id for evidence in item.evidence}
        if request.evidence_id not in evidence_ids:
            raise _InvalidCandidateCorrection

        fields = [field.model_dump() for field in item.fields]
        for field in fields:
            if field["field_id"] == request.field_id:
                field["value"] = request.value
                field["evidence_ids"] = [str(request.evidence_id)]
                break
        else:
            raise _InvalidCandidateCorrection

        child = PolicyReviewItem.model_validate(
            {
                **item.model_dump(mode="json"),
                "candidate_version_id": str(_CHILD_CANDIDATE_VERSION_ID),
                "fields": fields,
                "expected_version": item.expected_version + 1,
            }
        )
        self.items[resolved_review_item_id] = child
        return child

    def confirm(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
        request: CandidateConfirmationRequest,
        **_: Any,
    ) -> PolicyReviewItem:
        self.confirm_calls.append((scope, review_item_id))
        item = self._item(scope, review_item_id)
        if request.expected_version != item.expected_version:
            raise _CandidateVersionConflict
        confirmed = item.model_copy(
            deep=True,
            update={"status": "USER_CONFIRMED", "expected_version": item.expected_version + 1},
        )
        self.items[review_item_id] = confirmed
        if any(issue.code == "TERMS_ONLY_RIDER" for issue in item.issues):
            self.informational_confirmations.append(review_item_id)
        elif item.candidate_kind == "rider":
            self.published_rider_ids.append(item.aggregate_id)
        return confirmed

    def reject(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
        request: CandidateRejectionRequest,
        **_: Any,
    ) -> PolicyReviewItem:
        self.reject_calls.append((scope, review_item_id))
        item = self._item(scope, review_item_id)
        if request.expected_version != item.expected_version:
            raise _CandidateVersionConflict
        rejected = item.model_copy(
            deep=True,
            update={"status": "rejected", "expected_version": item.expected_version + 1},
        )
        self.items[review_item_id] = rejected
        return rejected


@pytest.fixture()
def fake_service() -> _FakeCandidateReviewService:
    return _FakeCandidateReviewService()


@pytest.fixture()
def app(fake_service: _FakeCandidateReviewService) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.household_scope = SCOPE_A

    def resolve_scope() -> HouseholdScope:
        return application.state.household_scope

    def provide_service(scope: ScopeDependency) -> _FakeCandidateReviewService:
        fake_service.seen_scopes.append(scope)
        return fake_service

    application.include_router(router)
    application.dependency_overrides[resolve_household_scope] = resolve_scope
    application.dependency_overrides[get_candidate_review_service] = provide_service
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _assert_no_store(response: Any) -> None:
    assert "no-store" in response.headers.get("cache-control", "").lower()


def test_review_routes_list_and_get_are_scoped_and_value_free(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    listed = client.get(
        "/api/v1/review-items",
        params={
            "domain": "policy",
            "status": "NEEDS_REVIEW",
            "household_space_id": str(SCOPE_B.household_space_id),
        },
    )
    assert listed.status_code == 200
    _assert_no_store(listed)
    assert [item["review_item_id"] for item in listed.json()] == [
        str(_REVIEW_ITEM_ID),
        str(_TERMS_REVIEW_ITEM_ID),
    ]
    assert fake_service.list_calls[-1] == (SCOPE_A, "NEEDS_REVIEW")

    fetched = client.get(f"/api/v1/review-items/{_REVIEW_ITEM_ID}")
    assert fetched.status_code == 200
    _assert_no_store(fetched)
    assert fetched.json()["review_item_id"] == str(_REVIEW_ITEM_ID)
    assert fetched.json()["aggregate_id"] == str(_POLICY_ID)
    assert not _contains_forbidden_key(fetched.json())
    assert all(scope == SCOPE_A for scope in fake_service.seen_scopes)


def test_review_route_filters_policy_candidates_by_family_member(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    member_a = client.get(
        "/api/v1/review-items",
        params={
            "domain": "policy",
            "status": "NEEDS_REVIEW",
            "family_member_id": str(_MEMBER_A_ID),
        },
    )
    member_b = client.get(
        "/api/v1/review-items",
        params={
            "domain": "policy",
            "status": "NEEDS_REVIEW",
            "family_member_id": str(_MEMBER_B_ID),
        },
    )

    assert member_a.status_code == member_b.status_code == 200
    assert [item["review_item_id"] for item in member_a.json()] == [str(_REVIEW_ITEM_ID)]
    assert [item["review_item_id"] for item in member_b.json()] == [str(_TERMS_REVIEW_ITEM_ID)]
    assert fake_service.list_member_ids[-2:] == [_MEMBER_A_ID, _MEMBER_B_ID]
    assert all(scope == SCOPE_A for scope in fake_service.seen_scopes)


def test_patch_correction_uses_path_field_and_creates_child_version(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    parent = deepcopy(
        fake_service.parent_versions[_PARENT_CANDIDATE_VERSION_ID].model_dump(mode="json")
    )
    response = client.patch(
        f"/api/v1/policies/{_POLICY_ID}/candidate-fields/rider_name",
        json={
            "expected_version": 1,
            "field_id": "rider_name",
            "value": "Sample Rider Corrected",
            "evidence_id": str(_EVIDENCE_ID),
        },
    )

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    assert body["expected_version"] == 2
    assert body["candidate_version_id"] == str(_CHILD_CANDIDATE_VERSION_ID)
    assert (
        next(field for field in body["fields"] if field["field_id"] == "rider_name")["value"]
        == "Sample Rider Corrected"
    )
    assert fake_service.correction_targets == [_POLICY_ID]
    assert (
        fake_service.parent_versions[_PARENT_CANDIDATE_VERSION_ID].model_dump(mode="json") == parent
    )
    assert not _contains_forbidden_key(body)


def test_review_item_patch_targets_one_candidate_even_when_policy_is_shared(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    response = client.patch(
        f"/api/v1/review-items/{_REVIEW_ITEM_ID}/candidate-fields/rider_name",
        json={
            "expected_version": 1,
            "field_id": "rider_name",
            "value": "Sample Rider Corrected",
            "evidence_id": str(_EVIDENCE_ID),
        },
    )

    assert response.status_code == 200
    _assert_no_store(response)
    assert fake_service.correction_targets == [_REVIEW_ITEM_ID]


def test_patch_rejects_field_path_body_mismatch_without_calling_service(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    response = client.patch(
        f"/api/v1/policies/{_POLICY_ID}/candidate-fields/rider_name",
        json={
            "expected_version": 1,
            "field_id": "rider_status",
            "value": "active",
            "evidence_id": str(_EVIDENCE_ID),
        },
    )

    assert response.status_code == 422
    _assert_no_store(response)
    assert response.json() == {
        "error_code": "INVALID_CANDIDATE_CORRECTION",
        "message": "candidate correction is invalid",
    }
    assert fake_service.correction_targets == []


@pytest.mark.parametrize(
    "value",
    [
        ["not", "a", "scalar"],
        {"raw": "synthetic document body private"},
    ],
)
def test_correction_accepts_only_scalar_values_and_known_evidence(
    client: TestClient,
    value: Any,
) -> None:
    response = client.patch(
        f"/api/v1/policies/{_POLICY_ID}/candidate-fields/rider_name",
        json={
            "expected_version": 1,
            "field_id": "rider_name",
            "value": value,
            "evidence_id": str(_EVIDENCE_ID),
        },
    )

    assert response.status_code == 422
    _assert_no_store(response)
    assert response.json()["error_code"] in {
        "INVALID_REQUEST",
        "INVALID_CANDIDATE_CORRECTION",
    }


def test_correction_rejects_unknown_evidence_without_echoing_private_values(
    client: TestClient,
) -> None:
    response = client.patch(
        f"/api/v1/policies/{_POLICY_ID}/candidate-fields/rider_name",
        json={
            "expected_version": 1,
            "field_id": "rider_name",
            "value": "Sample Rider Corrected",
            "evidence_id": str(_UNKNOWN_ID),
        },
    )

    assert response.status_code == 422
    _assert_no_store(response)
    assert response.json() == {
        "error_code": "INVALID_CANDIDATE_CORRECTION",
        "message": "candidate correction is invalid",
    }


def test_confirm_and_reject_require_expected_version_and_are_no_store(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    confirmed = client.post(
        f"/api/v1/review-items/{_REVIEW_ITEM_ID}/confirm",
        json={"expected_version": 1},
    )
    assert confirmed.status_code == 200
    _assert_no_store(confirmed)
    assert confirmed.json()["status"] == "USER_CONFIRMED"
    assert fake_service.published_rider_ids == [_POLICY_ID]

    rejected = client.post(
        f"/api/v1/review-items/{_TERMS_REVIEW_ITEM_ID}/reject",
        json={"expected_version": 1, "reason_code": "TERMS_ONLY_RIDER"},
    )
    assert rejected.status_code == 200
    _assert_no_store(rejected)
    assert rejected.json()["status"] == "rejected"


def test_terms_only_rider_can_be_confirmed_informationally_but_never_published(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    response = client.post(
        f"/api/v1/review-items/{_TERMS_REVIEW_ITEM_ID}/confirm",
        json={"expected_version": 1},
    )

    assert response.status_code == 200
    _assert_no_store(response)
    assert response.json()["status"] == "USER_CONFIRMED"
    assert response.json()["candidate_kind"] == "rider"
    assert {issue["code"] for issue in response.json()["issues"]} == {"TERMS_ONLY_RIDER"}
    assert fake_service.informational_confirmations == [_TERMS_REVIEW_ITEM_ID]
    assert fake_service.published_rider_ids == []


def test_stale_write_is_value_free_optimistic_conflict(
    client: TestClient,
    fake_service: _FakeCandidateReviewService,
) -> None:
    response = client.post(
        f"/api/v1/review-items/{_REVIEW_ITEM_ID}/confirm",
        json={"expected_version": 99},
    )

    assert response.status_code == 409
    _assert_no_store(response)
    assert response.json() == {
        "error_code": "VERSION_CONFLICT",
        "message": "version conflict",
    }
    assert fake_service.items[_REVIEW_ITEM_ID].expected_version == 1


def test_missing_and_cross_household_review_items_have_same_scoped_not_found(
    app: FastAPI,
    client: TestClient,
) -> None:
    missing = client.get(f"/api/v1/review-items/{_UNKNOWN_ID}")
    assert missing.status_code == 404
    _assert_no_store(missing)

    app.state.household_scope = SCOPE_B
    cross_household = client.get(f"/api/v1/review-items/{_REVIEW_ITEM_ID}")
    assert cross_household.status_code == 404
    _assert_no_store(cross_household)
    assert (
        cross_household.json()
        == missing.json()
        == {
            "error_code": "REVIEW_ITEM_NOT_FOUND",
            "message": "review item not found",
        }
    )


def test_request_models_are_strict_and_correction_does_not_accept_private_fields() -> None:
    valid_correction = {
        "expected_version": 1,
        "field_id": "rider_name",
        "value": "Sample Rider Corrected",
        "evidence_id": str(_EVIDENCE_ID),
    }
    assert CandidateCorrectionRequest.model_validate(valid_correction).field_id == "rider_name"
    assert (
        CandidateConfirmationRequest.model_validate({"expected_version": 1}).expected_version == 1
    )
    assert (
        CandidateRejectionRequest.model_validate(
            {"expected_version": 1, "reason_code": "NOT_ENROLLED"}
        ).reason_code
        == "NOT_ENROLLED"
    )

    with pytest.raises(ValidationError):
        CandidateCorrectionRequest.model_validate(
            {**valid_correction, "value": {"raw": _PRIVATE_MARKERS[3]}}
        )
    with pytest.raises(ValidationError):
        CandidateCorrectionRequest.model_validate(
            {**valid_correction, "household_space_id": str(SCOPE_A.household_space_id)}
        )
    with pytest.raises(ValidationError):
        CandidateCorrectionRequest.model_validate(
            {**valid_correction, "source_path": _PRIVATE_MARKERS[1]}
        )
    with pytest.raises(ValidationError):
        CandidateCorrectionRequest.model_validate(
            {**valid_correction, "field_id": "coverage_start", "value": "2026-02-31"}
        )
    with pytest.raises(ValidationError):
        CandidateCorrectionRequest.model_validate({**valid_correction, "value": ""})


def test_validation_and_error_responses_do_not_echo_private_values_or_log_them(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    response = client.patch(
        f"/api/v1/policies/{_POLICY_ID}/candidate-fields/rider_name",
        json={
            "expected_version": 1,
            "field_id": "rider_name",
            "value": _PRIVATE_MARKERS[3],
            "evidence_id": str(_EVIDENCE_ID),
            "password": _PRIVATE_MARKERS[0],
            "source_path": _PRIVATE_MARKERS[1],
            "policy_number": _PRIVATE_MARKERS[2],
            "document_text": _PRIVATE_MARKERS[3],
        },
    )

    assert response.status_code == 422
    _assert_no_store(response)
    serialized = f"{response.text}\n{caplog.text}".lower()
    assert all(marker.lower() not in serialized for marker in _PRIVATE_MARKERS)
    assert not _contains_forbidden_key(response.json())

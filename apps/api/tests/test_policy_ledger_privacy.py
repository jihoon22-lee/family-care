"""Leakage regressions for policy-ledger responses and errors."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import pytest
from familycare_api.main import create_app
from familycare_api.policies.errors import FamilyMemberNotFound, VersionConflict
from familycare_api.policies.router import get_policy_ledger_service
from fastapi.testclient import TestClient

_MEMBER_ID = UUID("00000000-0000-4000-8000-000000000701")
_PRIVATE_MARKERS = (
    "synthetic-private-password",
    "/synthetic/private/policy.pdf",
    "synthetic-policy-number-private",
    "synthetic document body private",
)


class _ErrorService:
    def get_family_member(self, member_id: UUID) -> None:
        del member_id
        raise FamilyMemberNotFound

    def update_family_member(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise VersionConflict


@pytest.fixture()
def client() -> TestClient:
    app = create_app(enable_synthetic_ingestion=False)
    app.dependency_overrides[get_policy_ledger_service] = _ErrorService
    return TestClient(app)


def test_validation_error_does_not_echo_private_extra_values(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    response = client.post(
        "/api/v1/family-members",
        json={
            "display_name": "Family Member A",
            "internal_alias": "member-a",
            "password": _PRIVATE_MARKERS[0],
            "source_path": _PRIVATE_MARKERS[1],
            "policy_number": _PRIVATE_MARKERS[2],
            "document_text": _PRIVATE_MARKERS[3],
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    serialized = (response.text + caplog.text).lower()
    assert all(marker.lower() not in serialized for marker in _PRIVATE_MARKERS)


def test_scoped_not_found_and_version_errors_are_value_free(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    missing = client.get(f"/api/v1/family-members/{_MEMBER_ID}")
    stale = client.patch(
        f"/api/v1/family-members/{_MEMBER_ID}",
        json={
            "expected_version": 1,
            "display_name": "Family Member A",
        },
    )

    assert missing.status_code == 404
    assert missing.json() == {
        "error_code": "FAMILY_MEMBER_NOT_FOUND",
        "message": "family member not found",
    }
    assert stale.status_code == 409
    assert stale.json() == {
        "error_code": "VERSION_CONFLICT",
        "message": "version conflict",
    }
    serialized = (missing.text + stale.text + caplog.text).lower()
    assert all(marker.lower() not in serialized for marker in _PRIVATE_MARKERS)

"""Household and object-scope regressions for encrypted document batches."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from uuid import UUID

import pytest
from familycare_api.documents import batch_router
from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.context import AuthContext, get_session_service
from familycare_api.main import create_app
from fastapi.testclient import TestClient

USER_ID = UUID("00000000-0000-4000-8000-000000000011")
HOUSEHOLD_A = UUID("00000000-0000-4000-8000-000000000001")
HOUSEHOLD_B = UUID("00000000-0000-4000-8000-000000000002")
MEMBER_A = UUID("00000000-0000-4000-8000-000000000004")
MEMBER_B = UUID("00000000-0000-4000-8000-000000000014")
BATCH_A = UUID("00000000-0000-4000-8000-000000000005")
BATCH_B = UUID("00000000-0000-4000-8000-000000000015")
UNKNOWN_BATCH = UUID("00000000-0000-4000-8000-000000000099")
SOURCE_ID_A = "a" * 64
SOURCE_ID_B = "b" * 64
RAW_SESSION = "synthetic-session-token-that-is-long-enough-a"
CSRF_TOKEN = "synthetic-csrf-token-that-is-long-enough-a"


class _ScopedNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "DOCUMENT_NOT_FOUND"
    public_message = "document not found"


class _FakeSessions:
    def resolve(self, raw_token: str, now: datetime) -> AuthContext | None:
        del now
        if raw_token != RAW_SESSION:
            return None
        return AuthContext(
            user_id=USER_ID,
            household_space_id=HOUSEHOLD_A,
            session_id=UUID("00000000-0000-4000-8000-000000000021"),
            needs_reauthentication=False,
        )

    def validate_csrf(self, session_id: UUID, raw_token: str) -> bool:
        return session_id.int != 0 and raw_token == CSRF_TOKEN


class _ScopedBatchService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AuthContext, UUID]] = []
        self.member_households = {MEMBER_A: HOUSEHOLD_A, MEMBER_B: HOUSEHOLD_B}
        self.batch_households = {BATCH_A: HOUSEHOLD_A, BATCH_B: HOUSEHOLD_B}
        self.persisted: list[dict[str, object]] = []

    @staticmethod
    def _context(args: tuple[object, ...], kwargs: dict[str, object]) -> AuthContext:
        candidate = kwargs.get("context")
        if isinstance(candidate, AuthContext):
            return candidate
        for value in args:
            if isinstance(value, AuthContext):
                return value
        raise AssertionError("scope was not supplied by authenticated server context")

    @staticmethod
    def _value(
        args: tuple[object, ...], kwargs: dict[str, object], name: str, position: int
    ) -> object:
        if name in kwargs:
            return kwargs[name]
        return args[position]

    def create(self, *args: object, **kwargs: object) -> dict[str, object]:
        context = self._context(args, kwargs)
        member = self._value(args, kwargs, "family_member_id", 1)
        assert isinstance(member, UUID)
        self.calls.append(("create", context, member))
        if self.member_households.get(member) != context.household_space_id:
            raise _ScopedNotFound
        self.persisted.append(
            {
                "household_space_id": str(context.household_space_id),
                "family_member_id": str(member),
            }
        )
        return {
            "schema_version": "1",
            "batch_id": str(BATCH_A),
            "family_member_id": str(member),
            "state": "created",
            "items": [
                {
                    "source_id": SOURCE_ID_A,
                    "display_label": "Sample Policy A.pdf",
                    "state": "queued",
                    "error_code": None,
                    "attempts": 0,
                }
            ],
        }

    def get_status(self, *args: object, **kwargs: object) -> dict[str, object]:
        context = self._context(args, kwargs)
        value = self._value(args, kwargs, "batch_id", 1)
        assert isinstance(value, UUID)
        self.calls.append(("status", context, value))
        if self.batch_households.get(value) != context.household_space_id:
            raise _ScopedNotFound
        return {
            "schema_version": "1",
            "batch_id": str(value),
            "family_member_id": str(MEMBER_A),
            "state": "running",
            "items": [
                {
                    "source_id": SOURCE_ID_A,
                    "display_label": "Sample Policy A.pdf",
                    "state": "running",
                    "error_code": None,
                    "attempts": 1,
                }
            ],
        }


class _FakeCatalog:
    def list(self, context: object) -> tuple[dict[str, object], ...]:
        assert isinstance(context, AuthContext)
        assert context.household_space_id == HOUSEHOLD_A
        return ()


def _client() -> Iterator[TestClient]:
    service = _ScopedBatchService()
    app = create_app()
    app.dependency_overrides[batch_router.get_batch_service] = lambda: service
    app.dependency_overrides[batch_router.get_import_source_catalog] = _FakeCatalog
    app.dependency_overrides[get_session_service] = _FakeSessions
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("familycare_session", RAW_SESSION)
        yield client


@pytest.fixture()
def client() -> Iterator[TestClient]:
    yield from _client()


def _headers() -> dict[str, str]:
    return {"Origin": "https://testserver", "X-CSRF-Token": CSRF_TOKEN}


def _create(member_id: UUID, **extra: object) -> dict[str, object]:
    return {
        "schema_version": "1",
        "family_member_id": str(member_id),
        "source_ids": [SOURCE_ID_A],
        **extra,
    }


def test_household_is_derived_from_auth_and_client_scope_is_rejected(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/document-batches",
        headers=_headers(),
        json=_create(MEMBER_A, household_space_id=str(HOUSEHOLD_B)),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert "household_space_id" in response.json().get("fields", [])
    assert str(HOUSEHOLD_B) not in response.text


def test_cross_household_member_is_indistinguishable_from_missing_member(
    client: TestClient,
) -> None:
    missing = client.post(
        "/api/v1/document-batches",
        headers=_headers(),
        json=_create(UUID("00000000-0000-4000-8000-000000000099")),
    )
    cross_household = client.post(
        "/api/v1/document-batches",
        headers=_headers(),
        json=_create(MEMBER_B),
    )

    assert missing.status_code == cross_household.status_code == 404
    assert (
        missing.json()
        == cross_household.json()
        == {
            "error_code": "DOCUMENT_NOT_FOUND",
            "message": "document not found",
        }
    )
    assert str(MEMBER_B) not in cross_household.text


def test_cross_household_batch_is_indistinguishable_from_unknown_batch(
    client: TestClient,
) -> None:
    missing = client.get(f"/api/v1/document-batches/{UNKNOWN_BATCH}")
    cross_household = client.get(f"/api/v1/document-batches/{BATCH_B}")

    assert missing.status_code == cross_household.status_code == 404
    assert (
        missing.json()
        == cross_household.json()
        == {
            "error_code": "DOCUMENT_NOT_FOUND",
            "message": "document not found",
        }
    )
    assert str(BATCH_B) not in cross_household.text


def test_status_projection_contains_no_client_scope_or_internal_source_path(
    client: TestClient,
) -> None:
    response = client.get(f"/api/v1/document-batches/{BATCH_A}")

    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == str(BATCH_A)
    assert body["family_member_id"] == str(MEMBER_A)
    assert "household_space_id" not in body
    assert "source_key" not in response.text
    assert "/synthetic/" not in response.text

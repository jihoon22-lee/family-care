"""Authenticated HTTP contracts for encrypted document batches."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import pytest
from familycare_api.documents import batch_router
from familycare_api.documents.batch_repository import BatchItemRecord, BatchRecord
from familycare_api.documents.batch_router import BatchItemResponse
from familycare_api.documents.batch_service import _projection
from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.context import AuthContext, get_session_service
from familycare_api.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError

USER_ID = UUID("00000000-0000-4000-8000-000000000011")
HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000001")
FAMILY_MEMBER_ID = UUID("00000000-0000-4000-8000-000000000004")
BATCH_ID = UUID("00000000-0000-4000-8000-000000000005")
SESSION_ID = UUID("00000000-0000-4000-8000-000000000021")
SOURCE_ID_A = "a" * 64
SOURCE_ID_B = "b" * 64
RAW_SESSION = "synthetic-session-token-that-is-long-enough-a"
CSRF_TOKEN = "synthetic-csrf-token-that-is-long-enough-a"
PASSWORD = "synthetic-batch-password"


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
            household_space_id=HOUSEHOLD_ID,
            session_id=SESSION_ID,
            needs_reauthentication=False,
        )

    def validate_csrf(self, session_id: UUID, raw_token: str) -> bool:
        return session_id == SESSION_ID and raw_token == CSRF_TOKEN


class _Handoff(Protocol):
    password: str


@dataclass(frozen=True)
class _FakeSocket:
    sent_passwords: list[str]

    def send_once(self, handoff: _Handoff) -> None:
        self.sent_passwords.append(handoff.password)


class _FakeRepository:
    """Record only metadata so tests can prove the password never persists."""

    def __init__(self) -> None:
        self.persisted: list[object] = []

    def record(self, value: object) -> None:
        self.persisted.append(value)


class _FakeCatalog:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.entries: tuple[dict[str, object], ...] = (
            {
                "source_id": SOURCE_ID_A,
                "display_label": "synthetic/import-root/Family Member A/Sample Policy A.pdf\n",
                "size_bytes": 128,
                "encrypted": True,
                "absolute_path": "/synthetic/import-root/Family Member A/Sample Policy A.pdf",
            },
            {
                "source_id": SOURCE_ID_B,
                "display_label": "nested\\Sample Policy B.pdf",
                "size_bytes": 256,
                "encrypted": False,
                "relative_path": "nested/Sample Policy B.pdf",
            },
        )

    def list(self, context: object) -> tuple[dict[str, object], ...]:
        self.calls.append(context)
        return self.entries


def _item(
    source_id: str,
    label: str,
    *,
    state: str = "queued",
    error_code: str | None = None,
    attempts: int = 0,
    ocr_state: str = "pending",
    ocr_pages_processed: int = 0,
    ocr_warning_codes: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "display_label": label,
        "state": state,
        "error_code": error_code,
        "attempts": attempts,
        "ocr_state": ocr_state,
        "ocr_pages_processed": ocr_pages_processed,
        "ocr_warning_codes": list(ocr_warning_codes),
    }


def _batch(
    *,
    state: str = "created",
    items: Sequence[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "batch_id": str(BATCH_ID),
        "family_member_id": str(FAMILY_MEMBER_ID),
        "state": state,
        "items": list(
            items
            or (
                _item(SOURCE_ID_A, "Sample Policy A.pdf"),
                _item(SOURCE_ID_B, "Sample Policy B.pdf"),
            )
        ),
    }


class _FakeBatchService:
    def __init__(self, repository: _FakeRepository, socket: _FakeSocket) -> None:
        self.repository = repository
        self.socket = socket
        self.create_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.status_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.password_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.cancel_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.status = _batch()
        self.active = True

    @staticmethod
    def _context(args: tuple[object, ...], kwargs: dict[str, object]) -> AuthContext:
        candidate = kwargs.get("context")
        if isinstance(candidate, AuthContext):
            return candidate
        for value in args:
            if isinstance(value, AuthContext):
                return value
        raise AssertionError("batch service did not receive authenticated context")

    def create(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.create_calls.append((args, kwargs))
        context = self._context(args, kwargs)
        member = kwargs.get("family_member_id")
        if member is None and len(args) >= 2:
            member = args[1]
        source_ids = kwargs.get("source_ids")
        if source_ids is None and len(args) >= 3:
            source_ids = args[2]
        if context.household_space_id != HOUSEHOLD_ID or member != FAMILY_MEMBER_ID:
            raise _ScopedNotFound
        if tuple(source_ids or ()) != (SOURCE_ID_A, SOURCE_ID_B):
            raise _ScopedNotFound
        self.repository.record({"family_member_id": str(member), "source_ids": list(source_ids)})
        return self.status

    def get_status(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.status_calls.append((args, kwargs))
        self._context(args, kwargs)
        batch_id = kwargs.get("batch_id")
        if batch_id is None and len(args) >= 2:
            batch_id = args[1]
        if batch_id != BATCH_ID or not self.active:
            raise _ScopedNotFound
        return self.status

    def handoff_password(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.password_calls.append((args, kwargs))
        self._context(args, kwargs)
        batch_id = kwargs.get("batch_id")
        if batch_id is None and len(args) >= 2:
            batch_id = args[1]
        if batch_id != BATCH_ID or not self.active:
            raise _ScopedNotFound
        password = kwargs.get("password")
        if password is None and len(args) >= 3:
            password = args[2]
        assert isinstance(password, str)
        self.socket.sent_passwords.append(password)
        self.repository.record({"batch_id": str(BATCH_ID), "state": "running"})
        return self.status

    def cancel(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.cancel_calls.append((args, kwargs))
        self._context(args, kwargs)
        batch_id = kwargs.get("batch_id")
        if batch_id is None and len(args) >= 2:
            batch_id = args[1]
        if batch_id != BATCH_ID or not self.active:
            raise _ScopedNotFound
        self.active = False
        self.status = _batch(
            state="cancelled",
            items=[
                _item(SOURCE_ID_A, "Sample Policy A.pdf", state="cancelled"),
                _item(SOURCE_ID_B, "Sample Policy B.pdf", state="cancelled"),
            ],
        )
        return self.status


@pytest.fixture()
def dependencies() -> tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket]:
    repository = _FakeRepository()
    socket = _FakeSocket([])
    service = _FakeBatchService(repository, socket)
    return service, _FakeCatalog(), repository, socket


@pytest.fixture()
def client(
    dependencies: tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket],
) -> Iterator[TestClient]:
    service, catalog, _repository, _socket = dependencies
    app = create_app()
    app.dependency_overrides[batch_router.get_batch_service] = lambda: service
    app.dependency_overrides[batch_router.get_import_source_catalog] = lambda: catalog
    app.dependency_overrides[get_session_service] = _FakeSessions
    with TestClient(app, base_url="https://testserver") as test_client:
        test_client.cookies.set("familycare_session", RAW_SESSION)
        yield test_client


def _write_headers(*, csrf: bool = True, origin: str = "https://testserver") -> dict[str, str]:
    headers = {"Origin": origin}
    if csrf:
        headers["X-CSRF-Token"] = CSRF_TOKEN
    return headers


def _create_payload(**extra: object) -> dict[str, object]:
    return {
        "schema_version": "1",
        "family_member_id": str(FAMILY_MEMBER_ID),
        "source_ids": [SOURCE_ID_A, SOURCE_ID_B],
        **extra,
    }


def test_batch_routes_require_authentication_and_are_no_store() -> None:
    app = create_app()
    with TestClient(app, base_url="https://testserver") as client:
        requests = (
            client.get("/api/v1/document-import-sources"),
            client.post("/api/v1/document-batches", json=_create_payload()),
            client.get(f"/api/v1/document-batches/{BATCH_ID}"),
            client.post(
                f"/api/v1/document-batches/{BATCH_ID}/password",
                json={"password": PASSWORD},
            ),
            client.post(f"/api/v1/document-batches/{BATCH_ID}/cancel"),
        )

    assert all(response.status_code == 401 for response in requests)
    assert all(response.headers["cache-control"] == "no-store" for response in requests)


def test_source_list_is_authenticated_no_store_and_path_free(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/document-import-sources")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == [
        {
            "source_id": SOURCE_ID_A,
            "display_label": "Sample Policy A.pdf",
            "size_bytes": 128,
            "encrypted": True,
        },
        {
            "source_id": SOURCE_ID_B,
            "display_label": "Sample Policy B.pdf",
            "size_bytes": 256,
            "encrypted": False,
        },
    ]
    assert "/synthetic/import-root" not in response.text
    assert "relative_path" not in response.text
    assert "absolute_path" not in response.text


def test_create_status_password_and_cancel_are_authenticated_no_store(
    client: TestClient,
    dependencies: tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket],
) -> None:
    service, _catalog, _repository, socket = dependencies
    created = client.post(
        "/api/v1/document-batches",
        headers=_write_headers(),
        json=_create_payload(),
    )
    status_response = client.get(f"/api/v1/document-batches/{BATCH_ID}")
    handed_off = client.post(
        f"/api/v1/document-batches/{BATCH_ID}/password",
        headers=_write_headers(),
        json={"password": PASSWORD},
    )
    cancelled = client.post(
        f"/api/v1/document-batches/{BATCH_ID}/cancel",
        headers=_write_headers(),
    )

    assert created.status_code == handed_off.status_code == cancelled.status_code == 202
    assert status_response.status_code == 200
    assert cancelled.status_code == 202
    assert all(
        response.headers["cache-control"] == "no-store"
        for response in (created, status_response, handed_off, cancelled)
    )
    assert created.json()["batch_id"] == str(BATCH_ID)
    assert status_response.json()["family_member_id"] == str(FAMILY_MEMBER_ID)
    assert handed_off.json()["state"] == "created"
    assert cancelled.json()["state"] == "cancelled"
    assert socket.sent_passwords == [PASSWORD]
    assert service.active is False


def test_authenticated_writes_require_csrf_and_same_origin(
    client: TestClient,
) -> None:
    missing = client.post("/api/v1/document-batches", json=_create_payload())
    cross_origin = client.post(
        "/api/v1/document-batches",
        headers=_write_headers(origin="https://synthetic.invalid"),
        json=_create_payload(),
    )
    accepted = client.post(
        "/api/v1/document-batches",
        headers=_write_headers(),
        json=_create_payload(),
    )

    assert missing.status_code == 403
    assert missing.json()["error_code"] == "CSRF_REQUIRED"
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error_code"] == "ORIGIN_REQUIRED"
    assert accepted.status_code == 202


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_ids", ["not-a-source-id"]),
        ("source_ids", ["A" * 64]),
        ("source_ids", ["a" * 63]),
        ("source_ids", ["a" * 65]),
        ("source_ids", ["synthetic/import-root/policy.pdf"]),
        ("household_space_id", str(HOUSEHOLD_ID)),
        ("source_key", "synthetic/policy-001.pdf"),
        ("absolute_path", "/synthetic/import-root/policy-001.pdf"),
        ("password", PASSWORD),
    ],
)
def test_create_rejects_strict_source_ids_paths_and_client_scope_without_echo(
    client: TestClient,
    field: str,
    value: object,
) -> None:
    response = client.post(
        "/api/v1/document-batches",
        headers=_write_headers(),
        json=_create_payload(**{field: value}),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert field in response.json().get("fields", []) or field == "source_ids"
    assert str(value) not in response.text


@pytest.mark.parametrize(
    ("state", "items"),
    [
        (
            "partial",
            [
                _item(SOURCE_ID_A, "Sample Policy A.pdf", state="succeeded", attempts=1),
                _item(
                    SOURCE_ID_B,
                    "Sample Policy B.pdf",
                    state="password_required",
                    error_code="PASSWORD_REQUIRED",
                    attempts=1,
                ),
            ],
        ),
        (
            "succeeded",
            [
                _item(SOURCE_ID_A, "Sample Policy A.pdf", state="succeeded", attempts=1),
                _item(SOURCE_ID_B, "Sample Policy B.pdf", state="succeeded", attempts=1),
            ],
        ),
    ],
)
def test_status_projects_partial_and_success_without_source_paths(
    client: TestClient,
    dependencies: tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket],
    state: str,
    items: list[dict[str, object]],
) -> None:
    service, _catalog, _repository, _socket = dependencies
    service.status = _batch(state=state, items=items)

    response = client.get(f"/api/v1/document-batches/{BATCH_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == state
    assert body["items"] == items
    serialized = response.text.lower()
    for forbidden in (
        "source_key",
        "absolute_path",
        "relative_path",
        '"password":',
        "/mnt/",
    ):
        assert forbidden not in serialized


def test_status_projects_bounded_ocr_progress_without_ocr_payload(
    client: TestClient,
    dependencies: tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket],
) -> None:
    service, _catalog, _repository, _socket = dependencies
    service.status = _batch(
        state="partial",
        items=[
            _item(
                SOURCE_ID_A,
                "Sample Policy A.pdf",
                state="succeeded",
                attempts=1,
                ocr_state="completed",
                ocr_pages_processed=3,
            ),
            _item(
                SOURCE_ID_B,
                "Sample Policy B.pdf",
                state="running",
                attempts=1,
                ocr_state="warning",
                ocr_pages_processed=2,
                ocr_warning_codes=("NO_TEXT_DETECTED",),
            ),
        ],
    )

    response = client.get(f"/api/v1/document-batches/{BATCH_ID}")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "source_id": SOURCE_ID_A,
            "display_label": "Sample Policy A.pdf",
            "state": "succeeded",
            "error_code": None,
            "attempts": 1,
            "ocr_state": "completed",
            "ocr_pages_processed": 3,
            "ocr_warning_codes": [],
        },
        {
            "source_id": SOURCE_ID_B,
            "display_label": "Sample Policy B.pdf",
            "state": "running",
            "error_code": None,
            "attempts": 1,
            "ocr_state": "warning",
            "ocr_pages_processed": 2,
            "ocr_warning_codes": ["NO_TEXT_DETECTED"],
        },
    ]
    serialized = response.text.lower()
    for forbidden in ("ocr_text", "bbox", "coordinates", "stderr", "raw_error", "image_path"):
        assert forbidden not in serialized


def test_batch_item_rejects_duplicate_or_oversized_ocr_warning_codes() -> None:
    with pytest.raises(ValidationError):
        BatchItemResponse(
            **_item(
                SOURCE_ID_A,
                "Sample Policy A.pdf",
                ocr_warning_codes=("LOW_CONFIDENCE", "LOW_CONFIDENCE"),
            )
        )
    with pytest.raises(ValidationError):
        BatchItemResponse(
            **_item(
                SOURCE_ID_A,
                "Sample Policy A.pdf",
                ocr_warning_codes=("LOW_CONFIDENCE",) * 9,
            )
        )


def test_service_projection_allowlists_ocr_progress_metadata() -> None:
    value = _projection(
        BatchRecord(
            batch_id=BATCH_ID,
            family_member_id=FAMILY_MEMBER_ID,
            state="partial",
            items=(
                BatchItemRecord(
                    source_id=SOURCE_ID_A,
                    display_label="Sample Policy A.pdf",
                    state="succeeded",
                    error_code=None,
                    attempts=1,
                    ocr_state="warning",
                    ocr_pages_processed=2,
                    ocr_warning_codes=("NO_TEXT_DETECTED",),
                ),
            ),
        )
    )

    assert value["items"] == [
        {
            "source_id": SOURCE_ID_A,
            "display_label": "Sample Policy A.pdf",
            "state": "succeeded",
            "error_code": None,
            "attempts": 1,
            "ocr_state": "warning",
            "ocr_pages_processed": 2,
            "ocr_warning_codes": ["NO_TEXT_DETECTED"],
        }
    ]
    serialized = str(value).lower()
    for forbidden in ("ocr_text", "bbox", "coordinates", "image_path", "stderr", "raw_error"):
        assert forbidden not in serialized


def test_password_is_absent_from_response_logs_and_persistence(
    client: TestClient,
    dependencies: tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _service, _catalog, repository, socket = dependencies
    caplog.set_level(logging.DEBUG)

    response = client.post(
        f"/api/v1/document-batches/{BATCH_ID}/password",
        headers=_write_headers(),
        json={"password": PASSWORD},
    )

    assert response.status_code == 202
    assert PASSWORD not in response.text
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr(repository.persisted)
    assert socket.sent_passwords == [PASSWORD]


def test_password_requires_a_recent_active_batch(
    client: TestClient,
    dependencies: tuple[_FakeBatchService, _FakeCatalog, _FakeRepository, _FakeSocket],
) -> None:
    service, _catalog, _repository, _socket = dependencies
    service.active = False

    response = client.post(
        f"/api/v1/document-batches/{BATCH_ID}/password",
        headers=_write_headers(),
        json={"password": PASSWORD},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "DOCUMENT_NOT_FOUND",
        "message": "document not found",
    }
    assert PASSWORD not in response.text

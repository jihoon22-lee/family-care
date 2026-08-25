"""Hash-only session, expiry, and CSRF domain tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from familycare_api.identity.sessions import (
    SessionRecord,
    SessionService,
    SessionStore,
)

USER_ID = UUID("00000000-0000-4000-8000-000000000011")
HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000001")


class _MemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self.records: dict[UUID, SessionRecord] = {}
        self.token_hashes: list[str] = []
        self.csrf_hashes: list[str] = []

    def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        csrf_token_hash: str,
        device_label: str,
        created_at: datetime,
        expires_at: datetime,
        absolute_expires_at: datetime,
    ) -> SessionRecord:
        record = SessionRecord(
            id=uuid4(),
            user_id=user_id,
            household_space_id=HOUSEHOLD_ID,
            username="admin-a",
            display_name="Admin A",
            is_active=True,
            token_hash=token_hash,
            csrf_token_hash=csrf_token_hash,
            device_label=device_label,
            created_at=created_at,
            last_seen_at=created_at,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
            reauthenticated_at=created_at,
            revoked_at=None,
        )
        self.records[record.id] = record
        self.token_hashes.append(token_hash)
        self.csrf_hashes.append(csrf_token_hash)
        return record

    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        return next(
            (row for row in self.records.values() if row.token_hash == token_hash),
            None,
        )

    def get_by_id(self, session_id: UUID) -> SessionRecord | None:
        return self.records.get(session_id)

    def get_for_user(self, session_id: UUID, user_id: UUID) -> SessionRecord | None:
        row = self.records.get(session_id)
        return row if row is not None and row.user_id == user_id else None

    def touch(self, session_id: UUID, *, seen_at: datetime, expires_at: datetime) -> None:
        self.records[session_id] = replace(
            self.records[session_id],
            last_seen_at=seen_at,
            expires_at=expires_at,
        )

    def revoke(self, session_id: UUID, *, revoked_at: datetime) -> None:
        self.records[session_id] = replace(self.records[session_id], revoked_at=revoked_at)

    def revoke_all(self, user_id: UUID, *, revoked_at: datetime) -> None:
        for session_id, row in tuple(self.records.items()):
            if row.user_id == user_id and row.revoked_at is None:
                self.records[session_id] = replace(row, revoked_at=revoked_at)

    def update_csrf(self, session_id: UUID, csrf_token_hash: str) -> None:
        self.records[session_id] = replace(
            self.records[session_id], csrf_token_hash=csrf_token_hash
        )
        self.csrf_hashes.append(csrf_token_hash)

    def mark_reauthenticated(self, session_id: UUID, *, at: datetime) -> None:
        self.records[session_id] = replace(self.records[session_id], reauthenticated_at=at)

    def list_for_user(self, user_id: UUID) -> list[SessionRecord]:
        return [row for row in self.records.values() if row.user_id == user_id]


def test_session_stores_hashes_and_resolves_server_scope() -> None:
    store = _MemorySessionStore()
    service = SessionService(store)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    issued = service.issue(USER_ID, "synthetic-device", now)
    context = service.resolve(issued.raw_token, now + timedelta(days=1))

    assert context is not None
    assert context.user_id == USER_ID
    assert context.household_space_id == HOUSEHOLD_ID
    assert issued.raw_token not in repr(store.records)
    assert issued.csrf_token not in repr(store.records)
    assert store.token_hashes[0] != issued.raw_token
    assert store.csrf_hashes[0] != issued.csrf_token


def test_session_inactivity_boundary_is_inclusive_then_expires() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    exact_store = _MemorySessionStore()
    exact_service = SessionService(exact_store)
    exact = exact_service.issue(USER_ID, "synthetic-device-a", now)
    assert exact_service.resolve(exact.raw_token, now + timedelta(days=7)) is not None

    expired_store = _MemorySessionStore()
    expired_service = SessionService(expired_store)
    expired = expired_service.issue(USER_ID, "synthetic-device-b", now)
    assert (
        expired_service.resolve(
            expired.raw_token,
            now + timedelta(days=7, microseconds=1),
        )
        is None
    )


def test_session_absolute_expiry_and_revocation_fail_closed() -> None:
    store = _MemorySessionStore()
    service = SessionService(store)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    issued = service.issue(USER_ID, "synthetic-device", now)
    service.revoke(issued.session_id, actor_id=USER_ID, now=now + timedelta(days=1))

    assert service.resolve(issued.raw_token, now + timedelta(days=1)) is None

    other = service.issue(USER_ID, "synthetic-device", now)
    assert service.resolve(other.raw_token, now + timedelta(days=30, microseconds=1)) is None


def test_csrf_rotation_and_validation_use_only_hashes() -> None:
    store = _MemorySessionStore()
    service = SessionService(store)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    issued = service.issue(USER_ID, "synthetic-device", now)

    rotated = service.issue_csrf(issued.session_id)

    assert rotated != issued.csrf_token
    assert rotated not in repr(store.records)
    assert service.validate_csrf(issued.session_id, rotated) is True
    assert service.validate_csrf(issued.session_id, "synthetic-invalid-csrf") is False


def test_rotation_revokes_old_token_and_issues_new_pair() -> None:
    store = _MemorySessionStore()
    service = SessionService(store)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    issued = service.issue(USER_ID, "synthetic-device", now)

    rotated = service.rotate(issued.session_id, now + timedelta(minutes=1))

    assert rotated.session_id != issued.session_id
    assert rotated.raw_token != issued.raw_token
    assert rotated.csrf_token != issued.csrf_token
    assert service.resolve(issued.raw_token, now + timedelta(minutes=1)) is None
    assert service.resolve(rotated.raw_token, now + timedelta(minutes=1)) is not None

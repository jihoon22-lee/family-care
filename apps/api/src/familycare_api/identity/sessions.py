"""Opaque hash-only session lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from familycare_api.identity.context import AuthContext

_INACTIVITY_LIFETIME = timedelta(days=7)
_ABSOLUTE_LIFETIME = timedelta(days=30)
_REAUTHENTICATION_WINDOW = timedelta(minutes=10)


class SessionError(RuntimeError):
    """Stable session-domain failure without token or account values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    household_space_id: UUID
    username: str
    display_name: str
    is_active: bool
    token_hash: str
    csrf_token_hash: str
    device_label: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    absolute_expires_at: datetime
    reauthenticated_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedSession:
    session_id: UUID
    raw_token: str
    csrf_token: str
    expires_at: datetime


class SessionStore(Protocol):
    """Persistence contract that never accepts a raw token."""

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
    ) -> SessionRecord: ...

    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...

    def get_by_id(self, session_id: UUID) -> SessionRecord | None: ...

    def get_for_user(self, session_id: UUID, user_id: UUID) -> SessionRecord | None: ...

    def touch(self, session_id: UUID, *, seen_at: datetime, expires_at: datetime) -> None: ...

    def revoke(self, session_id: UUID, *, revoked_at: datetime) -> None: ...

    def revoke_all(self, user_id: UUID, *, revoked_at: datetime) -> None: ...

    def update_csrf(self, session_id: UUID, csrf_token_hash: str) -> None: ...

    def mark_reauthenticated(self, session_id: UUID, *, at: datetime) -> None: ...

    def list_for_user(self, user_id: UUID) -> list[SessionRecord]: ...


def _database_url(value: str) -> str:
    if not value:
        raise SessionError("SESSION_STORE_UNAVAILABLE")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _record(row: dict[str, Any]) -> SessionRecord:
    return SessionRecord(
        id=cast(UUID, row["id"]),
        user_id=cast(UUID, row["app_user_id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        username=cast(str, row["username"]),
        display_name=cast(str, row["display_name"]),
        is_active=cast(bool, row["is_active"]),
        token_hash=cast(str, row["token_hash"]),
        csrf_token_hash=cast(str, row["csrf_token_hash"]),
        device_label=cast(str, row["device_label"]),
        created_at=cast(datetime, row["created_at"]),
        last_seen_at=cast(datetime, row["last_seen_at"]),
        expires_at=cast(datetime, row["expires_at"]),
        absolute_expires_at=cast(datetime, row["absolute_expires_at"]),
        reauthenticated_at=cast(datetime | None, row.get("reauthenticated_at")),
        revoked_at=cast(datetime | None, row.get("revoked_at")),
    )


_SESSION_SELECT = """
    SELECT session.id, session.app_user_id, user_account.household_space_id,
           user_account.username, user_account.display_name, user_account.is_active,
           session.token_hash, session.csrf_token_hash, session.device_label,
           session.created_at, session.last_seen_at, session.expires_at,
           session.absolute_expires_at, session.reauthenticated_at, session.revoked_at
    FROM app_sessions AS session
    JOIN app_users AS user_account ON user_account.id = session.app_user_id
"""


class PostgresSessionStore:
    """Persist only SHA-256 token proofs and bounded metadata."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connection_url(self) -> str:
        return _database_url(self.database_url)

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
        try:
            with psycopg.connect(self._connection_url(), row_factory=dict_row) as connection:
                inserted = connection.execute(
                    """
                    INSERT INTO app_sessions (
                        app_user_id, token_hash, csrf_token_hash, device_label,
                        created_at, last_seen_at, expires_at, absolute_expires_at,
                        reauthenticated_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s
                    FROM app_users
                    WHERE id = %s AND is_active
                    RETURNING id
                    """,
                    (
                        token_hash,
                        csrf_token_hash,
                        device_label,
                        created_at,
                        created_at,
                        expires_at,
                        absolute_expires_at,
                        created_at,
                        user_id,
                    ),
                ).fetchone()
                if inserted is None:
                    raise SessionError("SESSION_USER_UNAVAILABLE")
                row = connection.execute(
                    _SESSION_SELECT + " WHERE session.id = %s",
                    (inserted["id"],),
                ).fetchone()
        except SessionError:
            raise
        except psycopg.Error:
            raise SessionError("SESSION_STORE_UNAVAILABLE") from None
        if row is None:
            raise SessionError("SESSION_STORE_UNAVAILABLE")
        return _record(row)

    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        try:
            with psycopg.connect(self._connection_url(), row_factory=dict_row) as connection:
                row = connection.execute(
                    _SESSION_SELECT + " WHERE session.token_hash = %s",
                    (token_hash,),
                ).fetchone()
        except psycopg.Error:
            raise SessionError("SESSION_STORE_UNAVAILABLE") from None
        return None if row is None else _record(row)

    def get_for_user(self, session_id: UUID, user_id: UUID) -> SessionRecord | None:
        try:
            with psycopg.connect(self._connection_url(), row_factory=dict_row) as connection:
                row = connection.execute(
                    _SESSION_SELECT + " WHERE session.id = %s AND session.app_user_id = %s",
                    (session_id, user_id),
                ).fetchone()
        except psycopg.Error:
            raise SessionError("SESSION_STORE_UNAVAILABLE") from None
        return None if row is None else _record(row)

    def get_by_id(self, session_id: UUID) -> SessionRecord | None:
        try:
            with psycopg.connect(self._connection_url(), row_factory=dict_row) as connection:
                row = connection.execute(
                    _SESSION_SELECT + " WHERE session.id = %s",
                    (session_id,),
                ).fetchone()
        except psycopg.Error:
            raise SessionError("SESSION_STORE_UNAVAILABLE") from None
        return None if row is None else _record(row)

    def touch(self, session_id: UUID, *, seen_at: datetime, expires_at: datetime) -> None:
        self._execute(
            "UPDATE app_sessions SET last_seen_at = %s, expires_at = %s "
            "WHERE id = %s AND revoked_at IS NULL",
            (seen_at, expires_at, session_id),
        )

    def revoke(self, session_id: UUID, *, revoked_at: datetime) -> None:
        self._execute(
            "UPDATE app_sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
            (revoked_at, session_id),
        )

    def revoke_all(self, user_id: UUID, *, revoked_at: datetime) -> None:
        self._execute(
            "UPDATE app_sessions SET revoked_at = %s WHERE app_user_id = %s AND revoked_at IS NULL",
            (revoked_at, user_id),
        )

    def update_csrf(self, session_id: UUID, csrf_token_hash: str) -> None:
        self._execute(
            "UPDATE app_sessions SET csrf_token_hash = %s WHERE id = %s AND revoked_at IS NULL",
            (csrf_token_hash, session_id),
        )

    def mark_reauthenticated(self, session_id: UUID, *, at: datetime) -> None:
        self._execute(
            "UPDATE app_sessions SET reauthenticated_at = %s WHERE id = %s AND revoked_at IS NULL",
            (at, session_id),
        )

    def list_for_user(self, user_id: UUID) -> list[SessionRecord]:
        try:
            with psycopg.connect(self._connection_url(), row_factory=dict_row) as connection:
                rows = connection.execute(
                    _SESSION_SELECT + " WHERE session.app_user_id = %s "
                    "ORDER BY session.created_at DESC, session.id",
                    (user_id,),
                ).fetchall()
        except psycopg.Error:
            raise SessionError("SESSION_STORE_UNAVAILABLE") from None
        return [_record(row) for row in rows]

    def _execute(self, query: str, parameters: tuple[Any, ...]) -> None:
        try:
            with psycopg.connect(self._connection_url()) as connection:
                connection.execute(query, parameters)
        except psycopg.Error:
            raise SessionError("SESSION_STORE_UNAVAILABLE") from None


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def _aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise SessionError("INVALID_SESSION_TIME")


class SessionService:
    """Issue, resolve, rotate, and revoke sessions without persisting raw tokens."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def issue(self, user_id: UUID, device_label: str, now: datetime) -> IssuedSession:
        _aware(now)
        label = device_label.strip()
        if not label or len(label) > 80 or any(ord(char) < 32 for char in label):
            raise SessionError("INVALID_DEVICE_LABEL")
        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + _INACTIVITY_LIFETIME
        absolute_expires_at = now + _ABSOLUTE_LIFETIME
        row = self.store.create(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            csrf_token_hash=_hash_token(csrf_token),
            device_label=label,
            created_at=now,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
        )
        return IssuedSession(
            session_id=row.id,
            raw_token=raw_token,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def resolve(self, raw_token: str, now: datetime) -> AuthContext | None:
        from familycare_api.identity.context import AuthContext

        _aware(now)
        if not isinstance(raw_token, str) or not 32 <= len(raw_token) <= 128:
            return None
        try:
            token_hash = _hash_token(raw_token)
        except UnicodeEncodeError:
            return None
        row = self.store.get_by_token_hash(token_hash)
        if row is None:
            return None
        if (
            not row.is_active
            or row.revoked_at is not None
            or now > row.expires_at
            or now > row.absolute_expires_at
        ):
            if row.revoked_at is None:
                self.store.revoke(row.id, revoked_at=now)
            return None
        next_expiry = min(now + _INACTIVITY_LIFETIME, row.absolute_expires_at)
        self.store.touch(row.id, seen_at=now, expires_at=next_expiry)
        needs_reauthentication = (
            row.reauthenticated_at is None
            or now - row.reauthenticated_at > _REAUTHENTICATION_WINDOW
        )
        return AuthContext(
            user_id=row.user_id,
            household_space_id=row.household_space_id,
            session_id=row.id,
            needs_reauthentication=needs_reauthentication,
        )

    def revoke(self, session_id: UUID, *, actor_id: UUID, now: datetime) -> None:
        _aware(now)
        row = self.store.get_for_user(session_id, actor_id)
        if row is None:
            raise SessionError("SESSION_NOT_FOUND")
        self.store.revoke(session_id, revoked_at=now)

    def rotate(self, session_id: UUID, now: datetime) -> IssuedSession:
        """Revoke one valid session and issue a new independent secret pair."""

        _aware(now)
        row = self.store.get_by_id(session_id)
        if (
            row is None
            or not row.is_active
            or row.revoked_at is not None
            or now > row.expires_at
            or now > row.absolute_expires_at
        ):
            raise SessionError("SESSION_NOT_FOUND")
        self.store.revoke(session_id, revoked_at=now)
        return self.issue(row.user_id, row.device_label, now)

    def revoke_all(self, user_id: UUID, *, now: datetime) -> None:
        _aware(now)
        self.store.revoke_all(user_id, revoked_at=now)

    def issue_csrf(self, session_id: UUID) -> str:
        row = self.store.get_by_id(session_id)
        if row is None or not row.is_active or row.revoked_at is not None:
            raise SessionError("SESSION_NOT_FOUND")
        raw_token = secrets.token_urlsafe(32)
        self.store.update_csrf(session_id, _hash_token(raw_token))
        return raw_token

    def validate_csrf(self, session_id: UUID, raw_token: str) -> bool:
        if not isinstance(raw_token, str) or not 32 <= len(raw_token) <= 128:
            return False
        try:
            candidate = _hash_token(raw_token)
        except UnicodeEncodeError:
            return False
        row = self.store.get_by_id(session_id)
        return row is not None and hmac.compare_digest(row.csrf_token_hash, candidate)

    def mark_reauthenticated(self, session_id: UUID, *, actor_id: UUID, now: datetime) -> None:
        _aware(now)
        if self.store.get_for_user(session_id, actor_id) is None:
            raise SessionError("SESSION_NOT_FOUND")
        self.store.mark_reauthenticated(session_id, at=now)

    def list_for_user(self, user_id: UUID) -> list[SessionRecord]:
        return self.store.list_for_user(user_id)


__all__ = [
    "IssuedSession",
    "PostgresSessionStore",
    "SessionError",
    "SessionRecord",
    "SessionService",
    "SessionStore",
]

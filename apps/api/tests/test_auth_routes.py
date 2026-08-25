"""HTTP authentication route contracts and cookie/CSRF boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from familycare_api.identity.context import AuthContext, get_session_service
from familycare_api.identity.password import PasswordHasher
from familycare_api.identity.rate_limit import LoginRateLimiter
from familycare_api.identity.router import (
    AppUserRecord,
    AuthenticationFailed,
    AuthenticationRateLimited,
    AuthService,
    get_auth_service,
)
from familycare_api.identity.sessions import IssuedSession
from familycare_api.main import create_app
from fastapi.testclient import TestClient

USER_ID = UUID("00000000-0000-4000-8000-000000000011")
HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000001")
SESSION_ID = UUID("00000000-0000-4000-8000-000000000021")
RAW_SESSION = "synthetic-session-token-that-is-long-enough-a"
CSRF_TOKEN = "synthetic-csrf-token-that-is-long-enough-a"


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

    def issue_csrf(self, session_id: UUID) -> str:
        assert session_id == SESSION_ID
        return CSRF_TOKEN

    def list_for_user(self, user_id: UUID) -> list[Any]:
        assert user_id == USER_ID
        return []


class _FakeUsers:
    user = AppUserRecord(
        id=USER_ID,
        household_space_id=HOUSEHOLD_ID,
        username="admin-a",
        display_name="Admin A",
        password_hash="$argon2id$synthetic-not-verified-by-route-fake",
        is_active=True,
    )

    def get(self, user_id: UUID) -> AppUserRecord | None:
        return self.user if user_id == USER_ID else None


class _FakeAuthService:
    def __init__(self) -> None:
        self.users = _FakeUsers()
        self.revoked: list[UUID] = []

    def login(self, **kwargs: Any) -> tuple[AppUserRecord, IssuedSession]:
        assert kwargs["username"] == "admin-a"
        assert kwargs["raw_password"] == "synthetic-auth-secret-a"
        return self.users.user, IssuedSession(
            session_id=SESSION_ID,
            raw_token=RAW_SESSION,
            csrf_token=CSRF_TOKEN,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )

    def revoke_session(
        self,
        context: AuthContext,
        session_id: UUID,
        now: datetime,
    ) -> None:
        del context, now
        self.revoked.append(session_id)


def _client() -> tuple[TestClient, _FakeAuthService]:
    app = create_app()
    sessions = _FakeSessions()
    auth = _FakeAuthService()
    app.dependency_overrides[get_session_service] = lambda: sessions
    app.dependency_overrides[get_auth_service] = lambda: auth
    return TestClient(app, base_url="https://testserver"), auth


def test_auth_routes_are_registered_without_public_signup() -> None:
    paths = set(create_app().openapi()["paths"])

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/logout" in paths
    assert "/api/v1/auth/me" in paths
    assert "/api/v1/auth/csrf" in paths
    assert "/api/v1/auth/reauthenticate" in paths
    assert "/api/v1/auth/password" in paths
    assert "/api/v1/auth/sessions" in paths
    assert "/api/v1/auth/sessions/{session_id}/revoke" in paths
    assert "/api/v1/auth/signup" not in paths
    assert "/api/v1/auth/reset" not in paths
    assert "/api/v1/auth/invite" not in paths


def test_login_sets_only_secure_host_cookie_and_no_store() -> None:
    client, _auth = _client()

    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={
            "username": "admin-a",
            "password": "synthetic-auth-secret-a",
            "device_label": "Synthetic browser",
        },
    )

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert f"{RAW_SESSION}" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"
    assert RAW_SESSION not in response.text
    assert response.json()["csrf_token"] == CSRF_TOKEN


def test_authenticated_write_requires_csrf_then_same_origin() -> None:
    client, auth = _client()
    login_response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={
            "username": "admin-a",
            "password": "synthetic-auth-secret-a",
            "device_label": "Synthetic browser",
        },
    )
    assert login_response.status_code == 200

    missing = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://testserver"},
    )
    cross_origin = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://synthetic.invalid", "X-CSRF-Token": CSRF_TOKEN},
    )
    accepted = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://testserver", "X-CSRF-Token": CSRF_TOKEN},
    )

    assert missing.status_code == 403
    assert missing.json()["error_code"] == "CSRF_REQUIRED"
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error_code"] == "ORIGIN_REQUIRED"
    assert accepted.status_code == 204
    assert auth.revoked == [SESSION_ID]
    assert all(
        response.headers["cache-control"] == "no-store"
        for response in (missing, cross_origin, accepted)
    )


def test_missing_or_expired_cookie_returns_one_unauthenticated_shape() -> None:
    client, _auth = _client()

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "error_code": "AUTHENTICATION_REQUIRED",
        "message": "authentication required",
    }
    assert response.headers["cache-control"] == "no-store"


def test_login_failures_share_one_error_and_rate_limit_unknown_users() -> None:
    encoded = PasswordHasher().hash("synthetic-auth-secret-a")
    active = AppUserRecord(
        id=USER_ID,
        household_space_id=HOUSEHOLD_ID,
        username="admin-a",
        display_name="Admin A",
        password_hash=encoded,
        is_active=True,
    )
    inactive = AppUserRecord(
        id=UUID("00000000-0000-4000-8000-000000000012"),
        household_space_id=HOUSEHOLD_ID,
        username="admin-b",
        display_name="Admin B",
        password_hash=encoded,
        is_active=False,
    )

    class _Users:
        def find_by_username(self, username: str) -> AppUserRecord | None:
            return {"admin-a": active, "admin-b": inactive}.get(username)

    service = AuthService(
        _Users(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        LoginRateLimiter(maximum_attempts=2),
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    for username in ("admin-a", "admin-b", "admin-c"):
        with pytest.raises(AuthenticationFailed) as caught:
            service.login(
                username=username,
                raw_password="synthetic-auth-secret-b",
                device_label="Synthetic browser",
                client_key="synthetic-client",
                now=now,
                existing_raw_session=None,
            )
        assert caught.value.error_code == "AUTH_FAILED"

    for _attempt in range(2):
        with pytest.raises(AuthenticationFailed):
            service.login(
                username="unknown-a",
                raw_password="synthetic-auth-secret-b",
                device_label="Synthetic browser",
                client_key="synthetic-client",
                now=now,
                existing_raw_session=None,
            )
    with pytest.raises(AuthenticationRateLimited):
        service.login(
            username="unknown-a",
            raw_password="synthetic-auth-secret-b",
            device_label="Synthetic browser",
            client_key="synthetic-client",
            now=now,
            existing_raw_session=None,
        )

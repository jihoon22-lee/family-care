"""Narrow local authentication HTTP surface."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, cast
from uuid import UUID

import psycopg
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Request,
    Response,
    status,
)
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.context import (
    AuthContext,
    get_session_service,
    resolve_auth_context,
    utc_now,
)
from familycare_api.identity.csrf import CsrfService, SameOriginService
from familycare_api.identity.password import PasswordHasher, PasswordHashError
from familycare_api.identity.rate_limit import LoginRateLimiter
from familycare_api.identity.sessions import (
    IssuedSession,
    SessionError,
    SessionService,
)

_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_COOKIE_NAME = "familycare_session"


class AuthenticationFailed(ApiBoundaryError):
    status_code = 401
    error_code = "AUTH_FAILED"
    public_message = "authentication failed"


class AuthenticationRateLimited(ApiBoundaryError):
    status_code = 429
    error_code = "AUTH_RATE_LIMITED"
    public_message = "authentication temporarily unavailable"


class AuthenticationStoreUnavailable(ApiBoundaryError):
    status_code = 503
    error_code = "AUTH_STORE_UNAVAILABLE"
    public_message = "authentication service unavailable"


class ReauthenticationRequired(ApiBoundaryError):
    status_code = 403
    error_code = "REAUTHENTICATION_REQUIRED"
    public_message = "recent reauthentication required"


class AuthSessionNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "SESSION_NOT_FOUND"
    public_message = "session not found"


class AuthInvalidRequest(ApiBoundaryError):
    status_code = 422
    error_code = "INVALID_REQUEST"
    public_message = "request validation failed"


@dataclass(frozen=True)
class AppUserRecord:
    id: UUID
    household_space_id: UUID
    username: str
    display_name: str
    password_hash: str
    is_active: bool


def _database_url(value: str) -> str:
    if not value:
        raise AuthenticationStoreUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _user(row: dict[str, Any]) -> AppUserRecord:
    return AppUserRecord(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        username=cast(str, row["username"]),
        display_name=cast(str, row["display_name"]),
        password_hash=cast(str, row["password_hash"]),
        is_active=cast(bool, row["is_active"]),
    )


class IdentityRepository:
    """Read local users and replace password hashes transactionally."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def find_by_username(self, username: str) -> AppUserRecord | None:
        return self._find("username = %s", (username,))

    def get(self, user_id: UUID) -> AppUserRecord | None:
        return self._find("id = %s", (user_id,))

    def _find(self, predicate: str, parameters: tuple[Any, ...]) -> AppUserRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    "SELECT id, household_space_id, username, display_name, "
                    "password_hash, is_active FROM app_users WHERE " + predicate,
                    parameters,
                ).fetchone()
        except psycopg.Error:
            raise AuthenticationStoreUnavailable from None
        return None if row is None else _user(row)

    def upgrade_hash(self, user_id: UUID, password_hash: str, now: datetime) -> None:
        self._execute(
            "UPDATE app_users SET password_hash = %s, updated_at = %s WHERE id = %s AND is_active",
            (password_hash, now, user_id),
        )

    def replace_password_and_revoke(
        self,
        user_id: UUID,
        password_hash: str,
        now: datetime,
    ) -> None:
        try:
            with psycopg.connect(self.database_url) as connection:
                updated = connection.execute(
                    "UPDATE app_users SET password_hash = %s, updated_at = %s "
                    "WHERE id = %s AND is_active RETURNING id",
                    (password_hash, now, user_id),
                ).fetchone()
                if updated is None:
                    raise AuthenticationFailed
                connection.execute(
                    "UPDATE app_sessions SET revoked_at = %s "
                    "WHERE app_user_id = %s AND revoked_at IS NULL",
                    (now, user_id),
                )
        except ApiBoundaryError:
            raise
        except psycopg.Error:
            raise AuthenticationStoreUnavailable from None

    def _execute(self, query: str, parameters: tuple[Any, ...]) -> None:
        try:
            with psycopg.connect(self.database_url) as connection:
                connection.execute(query, parameters)
        except psycopg.Error:
            raise AuthenticationStoreUnavailable from None


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return PasswordHasher().hash("synthetic-constant-auth-secret")


class AuthService:
    """Credential verification and sensitive session actions."""

    def __init__(
        self,
        users: IdentityRepository,
        sessions: SessionService,
        limiter: LoginRateLimiter,
        *,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.limiter = limiter
        self.password_hasher = password_hasher or PasswordHasher()

    @staticmethod
    def _normalized_username(value: str) -> str | None:
        normalized = value.strip().casefold()
        return normalized if _USERNAME_PATTERN.fullmatch(normalized) else None

    def login(
        self,
        *,
        username: str,
        raw_password: str,
        device_label: str,
        client_key: str,
        now: datetime,
        existing_raw_session: str | None,
    ) -> tuple[AppUserRecord, IssuedSession]:
        normalized = self._normalized_username(username)
        limiter_name = normalized or "invalid-user"
        if self.limiter.is_limited(limiter_name, client_key, now):
            raise AuthenticationRateLimited
        user = None if normalized is None else self.users.find_by_username(normalized)
        encoded_hash = (
            user.password_hash if user is not None and user.is_active else _dummy_password_hash()
        )
        verification = self.password_hasher.verify_and_check_upgrade(
            encoded_hash,
            raw_password,
        )
        if user is None or not user.is_active or not verification.verified:
            self.limiter.record_failure(limiter_name, client_key, now)
            raise AuthenticationFailed
        self.limiter.reset(limiter_name, client_key)
        if verification.needs_rehash:
            self.users.upgrade_hash(user.id, self.password_hasher.hash(raw_password), now)
        if existing_raw_session:
            try:
                existing = self.sessions.resolve(existing_raw_session, now)
                if existing is not None:
                    self.sessions.revoke(
                        existing.session_id,
                        actor_id=existing.user_id,
                        now=now,
                    )
            except SessionError:
                raise AuthenticationStoreUnavailable from None
        try:
            return user, self.sessions.issue(user.id, device_label, now)
        except SessionError as error:
            if error.code == "INVALID_DEVICE_LABEL":
                raise AuthInvalidRequest from None
            raise AuthenticationStoreUnavailable from None

    def reauthenticate(
        self,
        context: AuthContext,
        raw_password: str,
        now: datetime,
        *,
        client_key: str,
    ) -> None:
        user = self.users.get(context.user_id)
        limiter_name = user.username if user is not None else "invalid-user"
        if self.limiter.is_limited(limiter_name, client_key, now):
            raise AuthenticationRateLimited
        encoded_hash = (
            user.password_hash if user is not None and user.is_active else _dummy_password_hash()
        )
        if (
            user is None
            or not user.is_active
            or not self.password_hasher.verify(encoded_hash, raw_password)
        ):
            self.limiter.record_failure(limiter_name, client_key, now)
            raise AuthenticationFailed
        self.limiter.reset(limiter_name, client_key)
        try:
            self.sessions.mark_reauthenticated(
                context.session_id,
                actor_id=context.user_id,
                now=now,
            )
        except SessionError:
            raise AuthenticationStoreUnavailable from None

    def change_password(
        self,
        context: AuthContext,
        raw_password: str,
        now: datetime,
    ) -> None:
        if context.needs_reauthentication:
            raise ReauthenticationRequired
        try:
            encoded = self.password_hasher.hash(raw_password)
        except PasswordHashError:
            raise AuthInvalidRequest from None
        self.users.replace_password_and_revoke(context.user_id, encoded, now)

    def revoke_session(self, context: AuthContext, session_id: UUID, now: datetime) -> None:
        if session_id != context.session_id and context.needs_reauthentication:
            raise ReauthenticationRequired
        try:
            self.sessions.revoke(session_id, actor_id=context.user_id, now=now)
        except SessionError as error:
            if error.code == "SESSION_NOT_FOUND":
                raise AuthSessionNotFound from None
            raise AuthenticationStoreUnavailable from None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: SecretStr
    device_label: str = Field(min_length=1, max_length=80)


class ReauthenticateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    password: SecretStr


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    new_password: SecretStr


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    username: str
    display_name: str


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user: AuthUserResponse
    csrf_token: str
    expires_at: datetime


class CsrfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    csrf_token: str


class AuthSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    device_label: str
    current: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


class AuthErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=160)


def get_identity_repository() -> IdentityRepository:
    return IdentityRepository(os.getenv("FAMILYCARE_DATABASE_URL", ""))


@lru_cache(maxsize=1)
def get_login_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter()


def get_auth_service(
    users: Annotated[IdentityRepository, Depends(get_identity_repository)],
    sessions: Annotated[SessionService, Depends(get_session_service)],
) -> AuthService:
    return AuthService(users, sessions, get_login_rate_limiter())


AuthDependency = Annotated[AuthContext, Depends(resolve_auth_context)]
AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
SessionDependency = Annotated[SessionService, Depends(get_session_service)]

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": AuthErrorResponse, "description": "Authentication failed"},
    403: {"model": AuthErrorResponse, "description": "CSRF or reauthentication required"},
    404: {"model": AuthErrorResponse, "description": "Session not found"},
    429: {"model": AuthErrorResponse, "description": "Rate limited"},
    503: {"model": AuthErrorResponse, "description": "Authentication unavailable"},
}


def _user_response(user: AppUserRecord) -> AuthUserResponse:
    return AuthUserResponse(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
    )


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "local-client"


@router.post("/login", response_model=LoginResponse, responses=_AUTH_ERRORS)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDependency,
    existing_raw_session: Annotated[str | None, Cookie(alias=_COOKIE_NAME)] = None,
) -> LoginResponse:
    SameOriginService().validate(request)
    user, issued = service.login(
        username=payload.username,
        raw_password=payload.password.get_secret_value(),
        device_label=payload.device_label,
        client_key=_client_key(request),
        now=utc_now(),
        existing_raw_session=existing_raw_session,
    )
    max_age = max(0, int((issued.expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        _COOKIE_NAME,
        issued.raw_token,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(
        user=_user_response(user),
        csrf_token=issued.csrf_token,
        expires_at=issued.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_AUTH_ERRORS)
def logout(
    context: AuthDependency,
    service: AuthServiceDependency,
    response: Response,
) -> Response:
    service.revoke_session(context, context.session_id, utc_now())
    response.delete_cookie(
        _COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=AuthUserResponse, responses=_AUTH_ERRORS)
def current_user(
    context: AuthDependency,
    service: AuthServiceDependency,
) -> AuthUserResponse:
    user = service.users.get(context.user_id)
    if user is None or not user.is_active:
        raise AuthenticationFailed
    return _user_response(user)


@router.get("/csrf", response_model=CsrfResponse, responses=_AUTH_ERRORS)
def issue_csrf(
    context: AuthDependency,
    sessions: SessionDependency,
) -> CsrfResponse:
    try:
        token = CsrfService(sessions).issue(context.session_id)
    except SessionError:
        raise AuthenticationStoreUnavailable from None
    return CsrfResponse(csrf_token=token)


@router.post("/reauthenticate", status_code=status.HTTP_204_NO_CONTENT, responses=_AUTH_ERRORS)
def reauthenticate(
    payload: ReauthenticateRequest,
    request: Request,
    context: AuthDependency,
    service: AuthServiceDependency,
) -> Response:
    service.reauthenticate(
        context,
        payload.password.get_secret_value(),
        utc_now(),
        client_key=_client_key(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, responses=_AUTH_ERRORS)
def change_password(
    payload: ChangePasswordRequest,
    context: AuthDependency,
    service: AuthServiceDependency,
    response: Response,
) -> Response:
    service.change_password(context, payload.new_password.get_secret_value(), utc_now())
    response.delete_cookie(
        _COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/sessions", response_model=list[AuthSessionResponse], responses=_AUTH_ERRORS)
def list_sessions(
    context: AuthDependency,
    sessions: SessionDependency,
) -> list[AuthSessionResponse]:
    now = utc_now()
    return [
        AuthSessionResponse(
            session_id=row.id,
            device_label=row.device_label,
            current=row.id == context.session_id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
        )
        for row in sessions.list_for_user(context.user_id)
        if row.revoked_at is None and now <= row.expires_at and now <= row.absolute_expires_at
    ]


@router.post(
    "/sessions/{session_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_AUTH_ERRORS,
)
def revoke_session(
    session_id: UUID,
    context: AuthDependency,
    service: AuthServiceDependency,
) -> Response:
    service.revoke_session(context, session_id, utc_now())
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


__all__ = [
    "AppUserRecord",
    "AuthService",
    "IdentityRepository",
    "get_auth_service",
    "get_identity_repository",
    "router",
]

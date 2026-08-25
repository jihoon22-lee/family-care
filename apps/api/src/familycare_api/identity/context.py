"""Resolve authenticated users and server-owned household scope from cookies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie

from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.csrf import CsrfService, SameOriginService
from familycare_api.identity.sessions import (
    PostgresSessionStore,
    SessionError,
    SessionService,
)

if TYPE_CHECKING:
    from familycare_api.common.scope import HouseholdScope

_SESSION_COOKIE = APIKeyCookie(name="familycare_session", auto_error=False)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Unauthenticated(ApiBoundaryError):
    status_code = 401
    error_code = "AUTHENTICATION_REQUIRED"
    public_message = "authentication required"


class AuthStoreUnavailable(ApiBoundaryError):
    status_code = 503
    error_code = "AUTH_STORE_UNAVAILABLE"
    public_message = "authentication service unavailable"


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    household_space_id: UUID
    session_id: UUID
    needs_reauthentication: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID) or value.int == 0
            for value in (self.user_id, self.household_space_id, self.session_id)
        ):
            raise Unauthenticated


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_session_service() -> SessionService:
    database_url = os.getenv("FAMILYCARE_DATABASE_URL", "")
    return SessionService(PostgresSessionStore(database_url))


SessionDependency = Annotated[SessionService, Depends(get_session_service)]
RawSessionCookie = Annotated[str | None, Security(_SESSION_COOKIE)]


def resolve_auth_context(
    request: Request,
    sessions: SessionDependency,
    raw_session: RawSessionCookie = None,
) -> AuthContext:
    """Resolve one active session and enforce CSRF on authenticated writes."""

    try:
        context = sessions.resolve(raw_session or "", utc_now())
    except SessionError:
        raise AuthStoreUnavailable from None
    if context is None:
        raise Unauthenticated
    if request.method not in _SAFE_METHODS:
        CsrfService(sessions).validate(
            context.session_id,
            request.headers.get("X-CSRF-Token", ""),
        )
        SameOriginService().validate(request)
    request.state.auth_context = context
    return context


def require_household_context(context: AuthContext) -> HouseholdScope:
    from familycare_api.common.scope import HouseholdScope

    return HouseholdScope(context.household_space_id)


__all__ = [
    "AuthContext",
    "AuthStoreUnavailable",
    "Unauthenticated",
    "get_session_service",
    "require_household_context",
    "resolve_auth_context",
]

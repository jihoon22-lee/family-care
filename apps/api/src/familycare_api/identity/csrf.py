"""Same-origin and double-secret CSRF checks for cookie-authenticated writes."""

from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Request

from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.sessions import SessionService


class CsrfRequired(ApiBoundaryError):
    status_code = 403
    error_code = "CSRF_REQUIRED"
    public_message = "csrf validation required"


class OriginRequired(ApiBoundaryError):
    status_code = 403
    error_code = "ORIGIN_REQUIRED"
    public_message = "same-origin request required"


class CsrfService:
    """Rotate and verify a per-session CSRF proof stored only as a hash."""

    def __init__(self, sessions: SessionService) -> None:
        self.sessions = sessions

    def issue(self, session_id: UUID) -> str:
        return self.sessions.issue_csrf(session_id)

    def validate(self, session_id: UUID, token: str) -> None:
        if not token or not self.sessions.validate_csrf(session_id, token):
            raise CsrfRequired


class SameOriginService:
    """Accept only the configured public origin or the exact request origin."""

    def __init__(self, public_origin: str | None = None) -> None:
        self.public_origin = public_origin or os.getenv("FAMILYCARE_PUBLIC_ORIGIN")

    @staticmethod
    def _normalized_origin(value: str) -> tuple[str, str] | None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        return parsed.scheme.casefold(), parsed.netloc.casefold()

    def validate(self, request: Request) -> None:
        supplied = request.headers.get("Origin", "")
        expected = self.public_origin or str(request.base_url)
        if self._normalized_origin(supplied) is None or self._normalized_origin(
            supplied
        ) != self._normalized_origin(expected):
            raise OriginRequired


__all__ = ["CsrfRequired", "CsrfService", "OriginRequired", "SameOriginService"]

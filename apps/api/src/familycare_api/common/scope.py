"""Server-owned household scope boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Request

from familycare_api.errors import ApiBoundaryError


class HouseholdScopeUnavailable(ApiBoundaryError):
    """Raised when no authenticated server context can provide a household."""

    status_code = 401
    error_code = "AUTHENTICATION_REQUIRED"
    public_message = "authentication required"


@dataclass(frozen=True)
class HouseholdScope:
    """The sole authoritative tenant key for a business operation."""

    household_space_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.household_space_id, UUID) or self.household_space_id.int == 0:
            raise HouseholdScopeUnavailable


class HouseholdScopeResolver(Protocol):
    """Protocol replaced by the authenticated session resolver in Phase 7."""

    def resolve(self, request: Request) -> HouseholdScope:
        """Resolve a household from trusted server state only."""


def resolve_household_scope(request: Request) -> HouseholdScope:
    """Fail closed until authentication installs a server-derived resolver."""

    del request
    raise HouseholdScopeUnavailable


__all__ = [
    "HouseholdScope",
    "HouseholdScopeResolver",
    "HouseholdScopeUnavailable",
    "resolve_household_scope",
]

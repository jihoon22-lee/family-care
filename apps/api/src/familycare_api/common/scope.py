"""Server-owned household scope boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends

from familycare_api.contracts.generated_business import PolicyErrorCode
from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.context import AuthContext, resolve_auth_context


class HouseholdScopeUnavailable(ApiBoundaryError):
    """Raised when no authenticated server context can provide a household."""

    status_code = 401
    error_code: PolicyErrorCode = "AUTHENTICATION_REQUIRED"
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

    def resolve(self, context: AuthContext) -> HouseholdScope:
        """Resolve a household from trusted server state only."""


AuthDependency = Annotated[AuthContext, Depends(resolve_auth_context)]


def resolve_household_scope(context: AuthDependency) -> HouseholdScope:
    """Derive the only authoritative business scope from an active session."""

    if not isinstance(context, AuthContext):
        raise HouseholdScopeUnavailable
    return HouseholdScope(context.household_space_id)


__all__ = [
    "HouseholdScope",
    "HouseholdScopeResolver",
    "HouseholdScopeUnavailable",
    "resolve_household_scope",
]

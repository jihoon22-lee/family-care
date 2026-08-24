"""Value-free policy-ledger domain failures."""

from __future__ import annotations

from familycare_api.contracts.generated_business import PolicyApiErrorCode, PolicyErrorCode
from familycare_api.errors import ApiBoundaryError


class PolicyLedgerError(ApiBoundaryError):
    """Base class whose message never contains a request or stored value."""


class EvidenceInvalid(PolicyLedgerError):
    """Evidence is missing, malformed, stale, or not from a policy document."""

    status_code = 422
    error_code: PolicyErrorCode = "EVIDENCE_INVALID"
    public_message = "evidence is invalid"


class VersionConflict(PolicyLedgerError):
    """A write did not match the expected current version."""

    status_code = 409
    error_code: PolicyErrorCode = "VERSION_CONFLICT"
    public_message = "version conflict"


class PolicyRepositoryUnavailable(PolicyLedgerError):
    """The policy persistence boundary could not complete an operation."""

    status_code = 503
    error_code: PolicyApiErrorCode = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "policy service unavailable"


class FamilyMemberNotFound(PolicyLedgerError):
    """No active family member exists inside the server scope."""

    status_code = 404
    error_code: PolicyErrorCode = "FAMILY_MEMBER_NOT_FOUND"
    public_message = "family member not found"


class PolicyNotFound(PolicyLedgerError):
    """No active policy exists inside the server scope."""

    status_code = 404
    error_code: PolicyErrorCode = "POLICY_NOT_FOUND"
    public_message = "policy not found"


class PolicyStateConflict(PolicyLedgerError):
    """The requested operation conflicts with the aggregate lifecycle."""

    status_code = 409
    error_code: PolicyErrorCode = "POLICY_STATE_CONFLICT"
    public_message = "policy state conflict"


__all__ = [
    "EvidenceInvalid",
    "FamilyMemberNotFound",
    "PolicyLedgerError",
    "PolicyNotFound",
    "PolicyRepositoryUnavailable",
    "PolicyStateConflict",
    "VersionConflict",
]

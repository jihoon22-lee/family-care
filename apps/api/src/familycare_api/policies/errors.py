"""Value-free policy-ledger domain failures."""

from __future__ import annotations


class PolicyLedgerError(RuntimeError):
    """Base class whose message never contains a request or stored value."""


class EvidenceInvalid(PolicyLedgerError):
    """Evidence is missing, malformed, stale, or not from a policy document."""


class VersionConflict(PolicyLedgerError):
    """A write did not match the expected current version."""


class PolicyRepositoryUnavailable(PolicyLedgerError):
    """The policy persistence boundary could not complete an operation."""


class FamilyMemberNotFound(PolicyLedgerError):
    """No active family member exists inside the server scope."""


class PolicyNotFound(PolicyLedgerError):
    """No active policy exists inside the server scope."""


class PolicyStateConflict(PolicyLedgerError):
    """The requested operation conflicts with the aggregate lifecycle."""


__all__ = [
    "EvidenceInvalid",
    "FamilyMemberNotFound",
    "PolicyLedgerError",
    "PolicyNotFound",
    "PolicyRepositoryUnavailable",
    "PolicyStateConflict",
    "VersionConflict",
]

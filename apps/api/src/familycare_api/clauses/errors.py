"""Fixed, value-free failures for the Clause catalog and search boundary."""

from __future__ import annotations

from typing import Literal

from familycare_api.contracts.generated_business import PolicyApiErrorCode, PolicyErrorCode
from familycare_api.errors import ApiBoundaryError, ApiErrorCode

ClauseErrorCode = Literal[
    "TERMS_EDITION_NOT_FOUND",
    "CLAUSE_NOT_FOUND",
    "SEARCH_INDEX_VERSION_MISMATCH",
]
RiderClauseReasonCode = Literal[
    "CANDIDATE_DOMAIN_MISMATCH",
    "CANDIDATE_NOT_APPROVED",
    "CLAUSE_DOCUMENT_MISMATCH",
    "CONTRACT_DATE_UNKNOWN",
    "INVALID_REJECTION_REASON",
    "LINK_EVIDENCE_INCOMPLETE",
    "LINK_EVIDENCE_INVALID",
    "LINK_NOT_ACTIVE",
    "LINK_SCOPE_MISMATCH",
    "RIDER_POLICY_MISMATCH",
    "TERMS_EDITION_MISMATCH",
    "TERMS_EDITION_NOT_APPLICABLE",
    "TERMS_ONLY_RIDER",
    "TERMS_SCOPE_CONFLICT",
]


class ClauseError(ApiBoundaryError):
    """Base error whose public fields never contain Clause text or query input."""


class InvalidSearchQuery(ClauseError):
    status_code = 422
    error_code: PolicyApiErrorCode = "INVALID_REQUEST"
    public_message = "search request is invalid"


class TermsEditionNotFound(ClauseError):
    status_code = 404
    error_code: ApiErrorCode = "TERMS_EDITION_NOT_FOUND"
    public_message = "terms edition not found"


class ClauseNotFound(ClauseError):
    status_code = 404
    error_code: ApiErrorCode = "CLAUSE_NOT_FOUND"
    public_message = "clause not found"


class ClauseEvidenceInvalid(ClauseError):
    status_code = 422
    error_code: PolicyErrorCode = "EVIDENCE_INVALID"
    public_message = "clause evidence is invalid"


class ClauseVersionConflict(ClauseError):
    status_code = 409
    error_code: PolicyErrorCode = "VERSION_CONFLICT"
    public_message = "version conflict"


class RiderClauseLinkInvalid(ClauseError):
    """A fixed link invariant failed without exposing source values."""

    status_code = 422
    error_code: PolicyErrorCode = "EVIDENCE_INVALID"
    public_message = "Rider clause link is invalid"

    def __init__(self, reason_code: RiderClauseReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__()


class ClauseStateConflict(ClauseError):
    status_code = 409
    error_code: PolicyErrorCode = "POLICY_STATE_CONFLICT"
    public_message = "clause state conflict"


class SearchIndexVersionMismatch(ClauseError):
    status_code = 409
    error_code: ApiErrorCode = "SEARCH_INDEX_VERSION_MISMATCH"
    public_message = "search index version mismatch"


class ClauseRepositoryUnavailable(ClauseError):
    status_code = 503
    error_code: PolicyApiErrorCode = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "clause service unavailable"


__all__ = [
    "ClauseError",
    "ClauseErrorCode",
    "ClauseEvidenceInvalid",
    "ClauseNotFound",
    "ClauseRepositoryUnavailable",
    "ClauseStateConflict",
    "ClauseVersionConflict",
    "InvalidSearchQuery",
    "RiderClauseLinkInvalid",
    "RiderClauseReasonCode",
    "SearchIndexVersionMismatch",
    "TermsEditionNotFound",
]

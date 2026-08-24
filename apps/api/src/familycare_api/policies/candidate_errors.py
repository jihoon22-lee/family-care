"""Value-free policy-candidate review failures."""

from __future__ import annotations

from familycare_api.contracts.generated_business import CandidateErrorCode, PolicyApiErrorCode
from familycare_api.errors import ApiBoundaryError


class CandidateReviewError(ApiBoundaryError):
    """Base class whose public representation never contains stored values."""


class ReviewItemNotFound(CandidateReviewError):
    status_code = 404
    error_code: CandidateErrorCode = "REVIEW_ITEM_NOT_FOUND"
    public_message = "review item not found"


class CandidateVersionConflict(CandidateReviewError):
    status_code = 409
    error_code: CandidateErrorCode = "VERSION_CONFLICT"
    public_message = "version conflict"


class InvalidCandidateCorrection(CandidateReviewError):
    status_code = 422
    error_code: CandidateErrorCode = "INVALID_CANDIDATE_CORRECTION"
    public_message = "candidate correction is invalid"


class CandidateRepositoryUnavailable(CandidateReviewError):
    status_code = 503
    error_code: PolicyApiErrorCode = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "candidate review service unavailable"


__all__ = [
    "CandidateRepositoryUnavailable",
    "CandidateReviewError",
    "CandidateVersionConflict",
    "InvalidCandidateCorrection",
    "ReviewItemNotFound",
]

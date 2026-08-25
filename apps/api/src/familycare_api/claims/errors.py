"""Value-free failures for the local claim workflow boundary."""

from __future__ import annotations

from familycare_api.errors import ApiBoundaryError, ClaimBoundaryErrorCode


class ClaimWorkflowError(ApiBoundaryError):
    """Base claim failure whose public representation contains no stored values."""


class ClaimNotFound(ClaimWorkflowError):
    status_code = 404
    error_code: ClaimBoundaryErrorCode = "CLAIM_NOT_FOUND"
    public_message = "claim case not found"


class ChecklistItemNotFound(ClaimWorkflowError):
    status_code = 404
    error_code: ClaimBoundaryErrorCode = "CLAIM_CHECKLIST_ITEM_NOT_FOUND"
    public_message = "claim checklist item not found"


class InvalidClaimTransitionError(ClaimWorkflowError):
    status_code = 409
    error_code: ClaimBoundaryErrorCode = "INVALID_CLAIM_TRANSITION"
    public_message = "claim transition is not allowed"


class ClaimInvalid(ClaimWorkflowError):
    status_code = 422
    error_code: ClaimBoundaryErrorCode = "CLAIM_INVALID"
    public_message = "claim data is invalid"


class ClaimRepositoryUnavailable(ClaimWorkflowError):
    status_code = 503
    error_code = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "claim service unavailable"


__all__ = [
    "ChecklistItemNotFound",
    "ClaimInvalid",
    "ClaimNotFound",
    "ClaimRepositoryUnavailable",
    "ClaimWorkflowError",
    "InvalidClaimTransitionError",
]

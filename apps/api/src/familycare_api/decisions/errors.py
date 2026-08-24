"""Value-free failures for the MedicalEvent and decision boundary."""

from __future__ import annotations

from typing import Literal

from familycare_api.errors import ApiBoundaryError

DecisionErrorCode = Literal[
    "MEDICAL_EVENT_NOT_FOUND",
    "DECISION_RESULT_NOT_FOUND",
    "DECISION_INVALID",
    "RESOURCE_LIMIT_EXCEEDED",
]


class DecisionError(ApiBoundaryError):
    """Base decision error that never includes stored or request values."""


class MedicalEventNotFound(DecisionError):
    status_code = 404
    error_code: DecisionErrorCode = "MEDICAL_EVENT_NOT_FOUND"
    public_message = "medical event not found"


class DecisionResultNotFound(DecisionError):
    status_code = 404
    error_code: DecisionErrorCode = "DECISION_RESULT_NOT_FOUND"
    public_message = "decision result not found"


class DecisionInvalid(DecisionError):
    status_code = 422
    error_code: DecisionErrorCode = "DECISION_INVALID"
    public_message = "decision request is invalid"


class DecisionRepositoryUnavailable(DecisionError):
    status_code = 503
    error_code: DecisionErrorCode = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "decision service unavailable"


__all__ = [
    "DecisionError",
    "DecisionErrorCode",
    "DecisionInvalid",
    "DecisionRepositoryUnavailable",
    "DecisionResultNotFound",
    "MedicalEventNotFound",
]

"""Sanitized HTTP errors for the local synthetic analysis boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from familycare_api.contracts.generated_business import PolicyErrorCode
from familycare_api.documents.generated_contracts import ErrorCode

ApiErrorCode = ErrorCode | PolicyErrorCode


class ApiBoundaryError(RuntimeError):
    """Base exception with a fixed public error code and no request data."""

    status_code: int
    error_code: ApiErrorCode
    public_message: str

    def __init__(self) -> None:
        super().__init__(self.error_code)


class AnalysisJobNotFound(ApiBoundaryError):
    status_code = 404
    error_code: ErrorCode = "ANALYSIS_JOB_NOT_FOUND"
    public_message = "analysis job not found"


class AnalysisServiceUnavailable(ApiBoundaryError):
    status_code = 503
    error_code: ErrorCode = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "analysis service unavailable"


class ErrorResponse(BaseModel):
    """Stable error envelope that never echoes request values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: ErrorCode
    message: str
    fields: list[str] | None = None


def _validation_fields(error: RequestValidationError) -> list[str]:
    fields: set[str] = set()
    for item in error.errors():
        location = item.get("loc", ())
        if isinstance(location, tuple | list):
            safe_location = ".".join(
                str(component)
                for component in location
                if isinstance(component, str | int) and component not in {"body", "query", "path"}
            )
            if safe_location:
                fields.add(safe_location)
    return sorted(fields)


def install_error_handlers(app: FastAPI) -> None:
    """Install value-free validation and fixed application exception handlers."""

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request
        payload = ErrorResponse(
            error_code="INVALID_REQUEST",
            message="request validation failed",
            fields=_validation_fields(error),
        )
        return JSONResponse(status_code=422, content=payload.model_dump(exclude_none=True))

    @app.exception_handler(ApiBoundaryError)
    async def handle_boundary_error(
        request: Request,
        error: ApiBoundaryError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error_code": error.error_code,
                "message": error.public_message,
            },
        )


__all__ = [
    "AnalysisJobNotFound",
    "AnalysisServiceUnavailable",
    "ApiBoundaryError",
    "ErrorResponse",
    "install_error_handlers",
]

"""Stable, sanitized errors for local PDF intake."""

from __future__ import annotations

from enum import StrEnum


class IntakeErrorCode(StrEnum):
    """Error codes shared with the asynchronous analysis contract."""

    ANALYSIS_JOB_NOT_FOUND = "ANALYSIS_JOB_NOT_FOUND"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    DOCUMENT_PATH_ESCAPE = "DOCUMENT_PATH_ESCAPE"
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    EXTRACTION_TIMEOUT = "EXTRACTION_TIMEOUT"
    INVALID_REQUEST = "INVALID_REQUEST"
    PAGE_LIMIT_EXCEEDED = "PAGE_LIMIT_EXCEEDED"
    PASSWORD_INVALID = "PASSWORD_INVALID"
    PASSWORD_REQUIRED = "PASSWORD_REQUIRED"
    PDF_CORRUPT = "PDF_CORRUPT"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    TEMP_CLEANUP_FAILED = "TEMP_CLEANUP_FAILED"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"


class PdfIntakeError(Exception):
    """Base error whose message contains only a stable error code."""

    code: IntakeErrorCode

    def __init__(self) -> None:
        super().__init__(self.code.value)


class DocumentNotFound(PdfIntakeError):
    code = IntakeErrorCode.DOCUMENT_NOT_FOUND


class DocumentPathEscape(PdfIntakeError):
    code = IntakeErrorCode.DOCUMENT_PATH_ESCAPE


class DocumentTooLarge(PdfIntakeError):
    code = IntakeErrorCode.DOCUMENT_TOO_LARGE


class InvalidRequest(PdfIntakeError):
    code = IntakeErrorCode.INVALID_REQUEST


class PageLimitExceeded(PdfIntakeError):
    code = IntakeErrorCode.PAGE_LIMIT_EXCEEDED


class PasswordRequired(PdfIntakeError):
    code = IntakeErrorCode.PASSWORD_REQUIRED


class PdfCorrupt(PdfIntakeError):
    code = IntakeErrorCode.PDF_CORRUPT


class ExtractionTimeout(PdfIntakeError):
    code = IntakeErrorCode.EXTRACTION_TIMEOUT


class ResourceLimitExceeded(PdfIntakeError):
    code = IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED


class TempCleanupFailed(PdfIntakeError):
    code = IntakeErrorCode.TEMP_CLEANUP_FAILED


class UnsupportedFileType(PdfIntakeError):
    code = IntakeErrorCode.UNSUPPORTED_FILE_TYPE

"""Descriptor-based local PDF intake primitives."""

from familycare_worker.pdf.errors import (
    DocumentNotFound,
    DocumentPathEscape,
    DocumentTooLarge,
    ExtractionTimeout,
    IntakeErrorCode,
    InvalidRequest,
    PageLimitExceeded,
    PasswordInvalid,
    PasswordRequired,
    PdfCorrupt,
    PdfIntakeError,
    ResourceLimitExceeded,
    TempCleanupFailed,
    UnsupportedFileType,
)
from familycare_worker.pdf.intake import (
    OpenedSource,
    ValidatedPdf,
    open_source,
    stream_sha256,
    validate_pdf,
)
from familycare_worker.pdf.limits import MAX_INPUT_BYTES, MAX_PDF_PAGES

__all__ = [
    "MAX_INPUT_BYTES",
    "MAX_PDF_PAGES",
    "DocumentNotFound",
    "DocumentPathEscape",
    "DocumentTooLarge",
    "ExtractionTimeout",
    "IntakeErrorCode",
    "InvalidRequest",
    "OpenedSource",
    "PageLimitExceeded",
    "PasswordInvalid",
    "PasswordRequired",
    "PdfCorrupt",
    "PdfIntakeError",
    "ResourceLimitExceeded",
    "TempCleanupFailed",
    "UnsupportedFileType",
    "ValidatedPdf",
    "open_source",
    "stream_sha256",
    "validate_pdf",
]

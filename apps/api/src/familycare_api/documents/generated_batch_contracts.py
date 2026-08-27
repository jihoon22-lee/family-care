"""Generated from packages/contracts/schemas; do not edit manually."""

from __future__ import annotations

from typing import Literal, TypedDict

__all__ = [
    "BatchDocumentKind",
    "BatchErrorCode",
    "BatchId",
    "BatchItemState",
    "BatchState",
    "DocumentBatch",
    "DocumentBatchItem",
    "DocumentBatchRequest",
    "DocumentBatchSource",
    "DocumentBatchStatus",
    "FamilyMemberId",
    "ImportSource",
    "OcrState",
    "OcrWarningCode",
    "SourceId",
]


BatchDocumentKind = Literal[
    "application",
    "policy",
    "product_explanation",
    "supporting",
    "terms",
]


BatchErrorCode = Literal[
    "ARCHIVE_INTEGRITY_ERROR",
    "ARCHIVE_KEY_UNAVAILABLE",
    "ARCHIVE_WRITE_FAILED",
    "DOCUMENT_NOT_FOUND",
    "DOCUMENT_PATH_ESCAPE",
    "DOCUMENT_TOO_LARGE",
    "EXTRACTION_TIMEOUT",
    "INVALID_REQUEST",
    "OCR_FAILED",
    "OCR_OUTPUT_LIMIT_EXCEEDED",
    "OCR_TIMEOUT",
    "OCR_UNAVAILABLE",
    "PAGE_LIMIT_EXCEEDED",
    "PASSWORD_INVALID",
    "PASSWORD_REQUIRED",
    "PDF_CORRUPT",
    "RESOURCE_LIMIT_EXCEEDED",
    "SOURCE_CHANGED",
    "TEMP_CLEANUP_FAILED",
    "UNSUPPORTED_FILE_TYPE",
]


BatchId = str


BatchItemState = Literal[
    "cancelled",
    "password_required",
    "permanently_failed",
    "queued",
    "retryable_failed",
    "running",
    "succeeded",
]


BatchState = Literal[
    "cancelled",
    "created",
    "failed",
    "partial",
    "running",
    "succeeded",
]


FamilyMemberId = str


OcrState = Literal[
    "completed",
    "failed",
    "native_only",
    "pending",
    "running",
    "warning",
]


OcrWarningCode = Literal[
    "LOW_CONFIDENCE",
    "NO_TEXT_DETECTED",
]


SourceId = str


class DocumentBatch(TypedDict):
    batch_id: BatchId
    family_member_id: FamilyMemberId
    items: list[DocumentBatchItem]
    schema_version: Literal["1"]
    state: BatchState


class DocumentBatchItem(TypedDict):
    attempts: int
    display_label: str
    document_kind: BatchDocumentKind
    error_code: BatchErrorCode | None
    ocr_pages_processed: int
    ocr_state: OcrState
    ocr_warning_codes: list[OcrWarningCode]
    source_id: SourceId
    state: BatchItemState


class DocumentBatchRequest(TypedDict):
    family_member_id: FamilyMemberId
    schema_version: Literal["1"]
    sources: list[DocumentBatchSource]


class DocumentBatchSource(TypedDict):
    document_kind: BatchDocumentKind
    source_id: SourceId


class DocumentBatchStatus(TypedDict):
    batch_id: BatchId
    family_member_id: FamilyMemberId
    items: list[DocumentBatchItem]
    schema_version: Literal["1"]
    state: BatchState


class ImportSource(TypedDict):
    display_label: str
    encrypted: bool
    size_bytes: int
    source_id: SourceId

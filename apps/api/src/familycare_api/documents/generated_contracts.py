"""Generated from packages/contracts/schemas; do not edit manually."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

__all__ = [
    "AnalysisJob",
    "AnalysisSettings",
    "BoundingBox",
    "DocumentId",
    "DocumentIngestionRequest",
    "DocumentStatus",
    "ErrorCode",
    "Evidence",
    "ExtractionCell",
    "ExtractionPage",
    "ExtractionResult",
    "ExtractionSummary",
    "ExtractionTable",
    "ExtractorConfig",
    "JobId",
    "JobState",
    "PageQuality",
    "TextBlock",
]


BoundingBox = list[float]


DocumentId = str


ErrorCode = Literal[
    "ANALYSIS_JOB_NOT_FOUND",
    "DOCUMENT_NOT_FOUND",
    "DOCUMENT_PATH_ESCAPE",
    "DOCUMENT_TOO_LARGE",
    "EXTRACTION_TIMEOUT",
    "INVALID_REQUEST",
    "PAGE_LIMIT_EXCEEDED",
    "PASSWORD_INVALID",
    "PASSWORD_REQUIRED",
    "PDF_CORRUPT",
    "RESOURCE_LIMIT_EXCEEDED",
    "TEMP_CLEANUP_FAILED",
    "UNSUPPORTED_FILE_TYPE",
]


JobId = str


JobState = Literal[
    "cancelled",
    "permanently_failed",
    "queued",
    "retryable_failed",
    "running",
    "succeeded",
]


class AnalysisJob(TypedDict):
    document_id: DocumentId
    error_code: NotRequired[ErrorCode]
    extractor_config_hash: str
    job_id: str
    schema_version: Literal["1"]
    settings: AnalysisSettings
    source_key: str
    state: JobState


class AnalysisSettings(TypedDict):
    document_kind: Literal[
        "amendment", "application", "claim", "policy", "product_explanation", "supporting", "terms"
    ]
    extractor_config: ExtractorConfig


class DocumentIngestionRequest(TypedDict):
    document_kind: Literal[
        "amendment", "application", "claim", "policy", "product_explanation", "supporting", "terms"
    ]
    extractor_config: ExtractorConfig
    schema_version: Literal["1"]
    source_key: str


class DocumentStatus(TypedDict):
    document_id: DocumentId
    error_code: NotRequired[ErrorCode]
    extraction_summary: NotRequired[ExtractionSummary]
    job_id: JobId
    schema_version: Literal["1"]
    state: JobState


class Evidence(TypedDict):
    bbox: NotRequired[BoundingBox]
    content_sha256: str
    document_version_id: str
    page_number: int
    review_state: Literal["candidate", "confirmed", "rejected"]


class ExtractionCell(TypedDict):
    bbox: BoundingBox
    column_index: int
    review_state: Literal["candidate", "confirmed", "rejected"]
    row_index: int
    text: str


class ExtractionPage(TypedDict):
    blocks: list[TextBlock]
    height_points: float
    page_number: int
    quality: PageQuality
    tables: list[ExtractionTable]
    warning_codes: list[str]
    width_points: float


class ExtractionResult(TypedDict):
    content_sha256: str
    evidence: list[Evidence]
    extractor_config_hash: str
    extractor_name: str
    extractor_version: str
    pages: list[ExtractionPage]
    quality_rule_version: Literal["quality-v1"]
    schema_version: Literal["1"]


class ExtractionSummary(TypedDict):
    block_count: int
    cell_count: int
    page_count: int
    table_count: int


class ExtractionTable(TypedDict):
    bbox: BoundingBox
    cells: list[ExtractionCell]
    review_state: Literal["candidate", "confirmed", "rejected"]


class ExtractorConfig(TypedDict):
    profile: Literal["quality-v1"]
    quality_rule_version: Literal["quality-v1"]
    table_strategy: Literal["auto", "lines", "text"]


class PageQuality(TypedDict):
    alphanumeric_ratio: float
    classification: Literal["OCR_REQUIRED", "TEXT_SUFFICIENT"]
    maximum_repeated_character_run: int
    non_whitespace_chars: int
    replacement_character_ratio: float
    rule_version: Literal["quality-v1"]


class TextBlock(TypedDict):
    bbox: BoundingBox
    page_number: int
    reading_order: int
    text: str

"""Bounded, path-free models shared by the local OCR adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

OcrWarningCode = Literal["LOW_CONFIDENCE", "NO_TEXT_DETECTED"]


class OcrConfigurationError(ValueError):
    """Reject an unsupported OCR configuration without echoing input."""

    def __init__(self) -> None:
        super().__init__("invalid OCR configuration")


class OcrRenderError(RuntimeError):
    """Map PDFium failures to one sanitized stable code."""

    code = "OCR_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)


class OcrExecutionError(RuntimeError):
    """Represent a sanitized local OCR process or output failure."""

    _CODES = frozenset(
        {
            "OCR_FAILED",
            "OCR_OUTPUT_LIMIT_EXCEEDED",
            "OCR_TIMEOUT",
            "OCR_UNAVAILABLE",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "OCR_FAILED"
        self.code = code
        super().__init__(code)


class OcrCancelled(RuntimeError):
    """Stop OCR without leaking the source of cancellation."""

    def __init__(self) -> None:
        super().__init__("OCR_CANCELLED")


class OcrTempCleanupError(RuntimeError):
    """Require the enclosing job to fail if generated images remain."""

    code = "TEMP_CLEANUP_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    rendered_dpi: int
    image_width_pixels: int
    image_height_pixels: int


@dataclass(frozen=True)
class RawOcrBlock:
    text: str
    pixel_bbox: tuple[int, int, int, int]
    reading_order: int
    confidence: float


@dataclass(frozen=True)
class EnginePageResult:
    engine_name: Literal["tesseract"]
    engine_version: str
    image_width_pixels: int
    image_height_pixels: int
    blocks: tuple[RawOcrBlock, ...]
    warning_codes: tuple[OcrWarningCode, ...]


@dataclass(frozen=True)
class OcrBlock:
    text: str
    bbox: tuple[float, float, float, float]
    reading_order: int
    confidence: float
    source_layer: Literal["ocr"] = "ocr"
    review_state: Literal["candidate"] = "candidate"


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    rendered_dpi: int
    image_width_pixels: int
    image_height_pixels: int
    status: Literal["completed", "warning"]
    warning_codes: tuple[OcrWarningCode, ...]
    blocks: tuple[OcrBlock, ...]


@dataclass(frozen=True)
class SelectiveOcrResult:
    document_version_id: UUID
    content_sha256: str
    engine_name: Literal["tesseract"]
    engine_version: str
    language_codes: tuple[Literal["kor"], Literal["eng"]]
    language_config_hash: str
    quality_rule_version: Literal["quality-v1"]
    pages: tuple[OcrPageResult, ...]
    warning_codes: tuple[OcrWarningCode, ...]


__all__ = [
    "EnginePageResult",
    "OcrBlock",
    "OcrCancelled",
    "OcrConfigurationError",
    "OcrExecutionError",
    "OcrPageResult",
    "OcrRenderError",
    "OcrTempCleanupError",
    "OcrWarningCode",
    "RawOcrBlock",
    "RenderedPage",
    "SelectiveOcrResult",
]

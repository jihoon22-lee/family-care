"""Bounded, path-free models shared by the local OCR adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


__all__ = [
    "EnginePageResult",
    "OcrConfigurationError",
    "OcrExecutionError",
    "OcrRenderError",
    "OcrWarningCode",
    "RawOcrBlock",
    "RenderedPage",
]

"""Local selective OCR adapters and provenance models."""

from .engine import TesseractOcrEngine
from .models import (
    EnginePageResult,
    OcrBlock,
    OcrCancelled,
    OcrConfigurationError,
    OcrExecutionError,
    OcrPageResult,
    OcrRenderError,
    OcrTempCleanupError,
    RawOcrBlock,
    RenderedPage,
    SelectiveOcrResult,
)
from .processor import SelectiveOcrProcessor
from .renderer import PdfiumPageRenderer

__all__ = [
    "EnginePageResult",
    "OcrBlock",
    "OcrCancelled",
    "OcrConfigurationError",
    "OcrExecutionError",
    "OcrPageResult",
    "OcrRenderError",
    "OcrTempCleanupError",
    "PdfiumPageRenderer",
    "RawOcrBlock",
    "RenderedPage",
    "SelectiveOcrProcessor",
    "SelectiveOcrResult",
    "TesseractOcrEngine",
]

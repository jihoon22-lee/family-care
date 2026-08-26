"""Local selective OCR adapters and provenance models."""

from .engine import TesseractOcrEngine
from .models import (
    EnginePageResult,
    OcrConfigurationError,
    OcrExecutionError,
    OcrRenderError,
    RawOcrBlock,
    RenderedPage,
)
from .renderer import PdfiumPageRenderer

__all__ = [
    "EnginePageResult",
    "OcrConfigurationError",
    "OcrExecutionError",
    "OcrRenderError",
    "PdfiumPageRenderer",
    "RawOcrBlock",
    "RenderedPage",
    "TesseractOcrEngine",
]

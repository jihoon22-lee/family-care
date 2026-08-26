"""Select OCR_REQUIRED pages, map coordinates, and remove every rendered image."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from uuid import UUID

from familycare_worker.pdf.workspace import Workspace

from .models import (
    EnginePageResult,
    OcrBlock,
    OcrCancelled,
    OcrConfigurationError,
    OcrPageResult,
    OcrTempCleanupError,
    OcrWarningCode,
    RenderedPage,
    SelectiveOcrResult,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGES = ("kor", "eng")
_RENDER_DPI = 300


class PageRenderer(Protocol):
    def render(
        self,
        source_fd: int,
        page_number: int,
        output: BinaryIO,
        *,
        dpi: int,
    ) -> RenderedPage: ...


class OcrEngine(Protocol):
    engine_version: str

    def recognize(
        self,
        image_path: Path,
        *,
        languages: tuple[str, ...],
    ) -> EnginePageResult: ...


EngineFactory = Callable[[], OcrEngine]
ProgressCallback = Callable[[int], bool]


def _unlink(path: Path) -> None:
    path.unlink()


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OcrConfigurationError
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > 1_000_000:
        raise OcrConfigurationError
    return number


def _selected_pages(extraction: object) -> list[tuple[int, float, float]]:
    if not isinstance(extraction, Mapping):
        raise OcrConfigurationError
    if (
        extraction.get("schema_version") != "1"
        or extraction.get("quality_rule_version") != "quality-v1"
    ):
        raise OcrConfigurationError
    pages = extraction.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 500:
        raise OcrConfigurationError
    selected: list[tuple[int, float, float]] = []
    for expected_number, raw_page in enumerate(pages, start=1):
        if not isinstance(raw_page, Mapping) or raw_page.get("page_number") != expected_number:
            raise OcrConfigurationError
        width = _number(raw_page.get("width_points"))
        height = _number(raw_page.get("height_points"))
        quality = raw_page.get("quality")
        if not isinstance(quality, Mapping) or quality.get("rule_version") != "quality-v1":
            raise OcrConfigurationError
        classification = quality.get("classification")
        if classification not in {"TEXT_SUFFICIENT", "OCR_REQUIRED"}:
            raise OcrConfigurationError
        if classification == "OCR_REQUIRED":
            selected.append((expected_number, width, height))
    return selected


def _bbox(
    pixel_bbox: tuple[int, int, int, int],
    *,
    image_width: int,
    image_height: int,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    if image_width < 1 or image_height < 1 or len(pixel_bbox) != 4:
        raise OcrConfigurationError
    left, top, right, bottom = pixel_bbox
    if (
        any(isinstance(value, bool) or not isinstance(value, int) for value in pixel_bbox)
        or left < 0
        or top < 0
        or right <= left
        or bottom <= top
        or right > image_width
        or bottom > image_height
    ):
        raise OcrConfigurationError
    x_scale = page_width / image_width
    y_scale = page_height / image_height
    return cast(
        tuple[float, float, float, float],
        tuple(
            round(value, 3)
            for value in (left * x_scale, top * y_scale, right * x_scale, bottom * y_scale)
        ),
    )


def _config_hash(engine_version: str) -> str:
    canonical = json.dumps(
        {
            "dpi": _RENDER_DPI,
            "engine_name": "tesseract",
            "engine_version": engine_version,
            "language_codes": list(_LANGUAGES),
            "quality_rule_version": "quality-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class SelectiveOcrProcessor:
    """Run a local engine only for deterministically selected native pages."""

    def __init__(self, renderer: PageRenderer, engine_factory: EngineFactory) -> None:
        self.renderer = renderer
        self.engine_factory = engine_factory

    def process(
        self,
        extraction: object,
        source_fd: int,
        workspace: Workspace,
        *,
        document_version_id: UUID,
        content_sha256: str,
        on_progress: ProgressCallback = lambda _processed: True,
    ) -> SelectiveOcrResult | None:
        if (
            not isinstance(document_version_id, UUID)
            or document_version_id.int == 0
            or _SHA256_PATTERN.fullmatch(content_sha256) is None
            or not isinstance(source_fd, int)
            or source_fd < 0
        ):
            raise OcrConfigurationError
        if (
            not isinstance(extraction, Mapping)
            or extraction.get("content_sha256") != content_sha256
        ):
            raise OcrConfigurationError
        selected = _selected_pages(extraction)
        if not selected:
            return None
        if not on_progress(0):
            raise OcrCancelled
        engine = self.engine_factory()
        pages: list[OcrPageResult] = []
        warnings: list[OcrWarningCode] = []
        for page_number, page_width, page_height in selected:
            image_path = workspace.path / f"ocr-page-{page_number}.png"
            cleanup_required = False
            try:
                with workspace.create_file(image_path.name) as output:
                    cleanup_required = True
                    rendered = self.renderer.render(
                        source_fd,
                        page_number,
                        output,
                        dpi=_RENDER_DPI,
                    )
                recognized = engine.recognize(image_path, languages=_LANGUAGES)
                if (
                    rendered.page_number != page_number
                    or rendered.rendered_dpi != _RENDER_DPI
                    or rendered.image_width_pixels != recognized.image_width_pixels
                    or rendered.image_height_pixels != recognized.image_height_pixels
                    or recognized.engine_name != "tesseract"
                    or recognized.engine_version != engine.engine_version
                ):
                    raise OcrConfigurationError
                blocks = tuple(
                    OcrBlock(
                        text=block.text,
                        bbox=_bbox(
                            block.pixel_bbox,
                            image_width=rendered.image_width_pixels,
                            image_height=rendered.image_height_pixels,
                            page_width=page_width,
                            page_height=page_height,
                        ),
                        reading_order=block.reading_order,
                        confidence=block.confidence,
                    )
                    for block in recognized.blocks
                )
                page_warnings = tuple(recognized.warning_codes)
                for warning in page_warnings:
                    if warning not in warnings:
                        warnings.append(warning)
                pages.append(
                    OcrPageResult(
                        page_number=page_number,
                        rendered_dpi=rendered.rendered_dpi,
                        image_width_pixels=rendered.image_width_pixels,
                        image_height_pixels=rendered.image_height_pixels,
                        status="warning" if page_warnings else "completed",
                        warning_codes=page_warnings,
                        blocks=blocks,
                    )
                )
            finally:
                if cleanup_required:
                    try:
                        _unlink(image_path)
                    except OSError:
                        raise OcrTempCleanupError from None
            if not on_progress(len(pages)):
                raise OcrCancelled
        return SelectiveOcrResult(
            document_version_id=document_version_id,
            content_sha256=content_sha256,
            engine_name="tesseract",
            engine_version=engine.engine_version,
            language_codes=("kor", "eng"),
            language_config_hash=_config_hash(engine.engine_version),
            quality_rule_version="quality-v1",
            pages=tuple(pages),
            warning_codes=tuple(warnings),
        )


__all__ = [
    "EngineFactory",
    "OcrEngine",
    "PageRenderer",
    "ProgressCallback",
    "SelectiveOcrProcessor",
]

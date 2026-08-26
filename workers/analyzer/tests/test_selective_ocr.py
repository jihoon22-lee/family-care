"""Selective OCR orchestration tests over synthetic native extraction results."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import pytest
from familycare_worker.ocr.models import (
    EnginePageResult,
    OcrCancelled,
    RawOcrBlock,
    RenderedPage,
)
from familycare_worker.ocr.processor import SelectiveOcrProcessor
from familycare_worker.pdf.workspace import create_workspace

DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000002")
CONTENT_SHA256 = "a" * 64


def _extraction(classifications: list[str]) -> dict[str, object]:
    return {
        "schema_version": "1",
        "content_sha256": CONTENT_SHA256,
        "extractor_name": "synthetic-native",
        "extractor_version": "1",
        "extractor_config_hash": "b" * 64,
        "quality_rule_version": "quality-v1",
        "pages": [
            {
                "page_number": number,
                "width_points": 600.0,
                "height_points": 800.0,
                "quality": {
                    "rule_version": "quality-v1",
                    "classification": classification,
                    "non_whitespace_chars": 0 if classification == "OCR_REQUIRED" else 30,
                    "alphanumeric_ratio": 0.0 if classification == "OCR_REQUIRED" else 0.8,
                    "replacement_character_ratio": 0.0,
                    "maximum_repeated_character_run": 0,
                },
                "blocks": [],
                "tables": [],
                "warning_codes": ["OCR_REQUIRED"] if classification == "OCR_REQUIRED" else [],
            }
            for number, classification in enumerate(classifications, start=1)
        ],
        "evidence": [
            {
                "document_version_id": str(DOCUMENT_VERSION_ID),
                "page_number": 1,
                "content_sha256": CONTENT_SHA256,
                "review_state": "candidate",
            }
        ],
    }


class FakeRenderer:
    def __init__(self) -> None:
        self.pages: list[int] = []

    def render(
        self,
        source_fd: int,
        page_number: int,
        output: BinaryIO,
        *,
        dpi: int,
    ) -> RenderedPage:
        assert source_fd == 41
        assert dpi == 300
        self.pages.append(page_number)
        output.write(b"synthetic-png")
        output.flush()
        return RenderedPage(page_number, dpi, 1200, 1600)


class FakeEngine:
    engine_version = "synthetic-engine-1"

    def __init__(self, *, fail_page: int | None = None, warning: bool = False) -> None:
        self.pages: list[int] = []
        self.fail_page = fail_page
        self.warning = warning

    def recognize(self, image_path: Path, *, languages: tuple[str, ...]) -> EnginePageResult:
        assert languages == ("kor", "eng")
        page_number = int(image_path.stem.rsplit("-", 1)[1])
        self.pages.append(page_number)
        if page_number == self.fail_page:
            raise RuntimeError("synthetic engine failure")
        warnings = ("LOW_CONFIDENCE",) if self.warning else ()
        return EnginePageResult(
            engine_name="tesseract",
            engine_version=self.engine_version,
            image_width_pixels=1200,
            image_height_pixels=1600,
            blocks=(
                RawOcrBlock(
                    text=f"Synthetic block {page_number}",
                    pixel_bbox=(120, 160, 600, 320),
                    reading_order=0,
                    confidence=45.0 if self.warning else 95.0,
                ),
            ),
            warning_codes=warnings,
        )


def _workspace(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    return create_workspace(root)


def test_only_ocr_required_pages_are_processed_and_native_result_is_unchanged(
    tmp_path: Path,
) -> None:
    extraction = _extraction(["TEXT_SUFFICIENT", "OCR_REQUIRED", "TEXT_SUFFICIENT", "OCR_REQUIRED"])
    original = copy.deepcopy(extraction)
    renderer = FakeRenderer()
    engine = FakeEngine()
    workspace = _workspace(tmp_path)
    progress: list[int] = []
    try:
        result = SelectiveOcrProcessor(renderer, lambda: engine).process(
            extraction,
            41,
            workspace,
            document_version_id=DOCUMENT_VERSION_ID,
            content_sha256=CONTENT_SHA256,
            on_progress=lambda processed: progress.append(processed) or True,
        )

        assert result is not None
        assert renderer.pages == [2, 4]
        assert engine.pages == [2, 4]
        assert [page.page_number for page in result.pages] == [2, 4]
        assert progress == [0, 1, 2]
        assert result.pages[0].blocks[0].bbox == (60.0, 80.0, 300.0, 160.0)
        assert result.pages[0].blocks[0].source_layer == "ocr"
        assert result.pages[0].blocks[0].review_state == "candidate"
        assert result.language_codes == ("kor", "eng")
        assert extraction == original
        assert list(workspace.path.glob("*.png")) == []
        assert list(workspace.path.glob("*.tsv")) == []
    finally:
        workspace.close_and_cleanup()


def test_text_sufficient_document_skips_renderer_and_engine(tmp_path: Path) -> None:
    renderer = FakeRenderer()
    engine_created = False

    def engine_factory() -> FakeEngine:
        nonlocal engine_created
        engine_created = True
        return FakeEngine()

    workspace = _workspace(tmp_path)
    try:
        result = SelectiveOcrProcessor(renderer, engine_factory).process(
            _extraction(["TEXT_SUFFICIENT"]),
            41,
            workspace,
            document_version_id=DOCUMENT_VERSION_ID,
            content_sha256=CONTENT_SHA256,
        )

        assert result is None
        assert renderer.pages == []
        assert engine_created is False
    finally:
        workspace.close_and_cleanup()


def test_progress_cancellation_stops_before_render_and_leaves_no_image(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    renderer = FakeRenderer()
    try:
        with pytest.raises(OcrCancelled):
            SelectiveOcrProcessor(renderer, FakeEngine).process(
                _extraction(["OCR_REQUIRED"]),
                41,
                workspace,
                document_version_id=DOCUMENT_VERSION_ID,
                content_sha256=CONTENT_SHA256,
                on_progress=lambda _processed: False,
            )

        assert renderer.pages == []
        assert list(workspace.path.iterdir()) == []
    finally:
        workspace.close_and_cleanup()


def test_page_warning_is_preserved_without_overwriting_native_warning(tmp_path: Path) -> None:
    extraction = _extraction(["OCR_REQUIRED"])
    workspace = _workspace(tmp_path)
    try:
        result = SelectiveOcrProcessor(FakeRenderer(), lambda: FakeEngine(warning=True)).process(
            extraction,
            41,
            workspace,
            document_version_id=DOCUMENT_VERSION_ID,
            content_sha256=CONTENT_SHA256,
        )

        assert result is not None
        assert result.warning_codes == ("LOW_CONFIDENCE",)
        assert result.pages[0].status == "warning"
        assert extraction["pages"][0]["warning_codes"] == ["OCR_REQUIRED"]  # type: ignore[index]
    finally:
        workspace.close_and_cleanup()

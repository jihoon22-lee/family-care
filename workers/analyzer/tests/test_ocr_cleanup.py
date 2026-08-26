"""OCR image cleanup tests for success, failure, cancellation, and unlink errors."""

from __future__ import annotations

from pathlib import Path

import pytest
from familycare_worker.ocr.models import OcrTempCleanupError
from familycare_worker.ocr.processor import SelectiveOcrProcessor
from familycare_worker.pdf.workspace import create_workspace

from workers.analyzer.tests.test_selective_ocr import (
    CONTENT_SHA256,
    DOCUMENT_VERSION_ID,
    FakeEngine,
    FakeRenderer,
    _extraction,
)


def _workspace(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    return create_workspace(root)


@pytest.mark.parametrize("fail_page", [None, 1])
def test_ocr_images_are_removed_after_success_or_engine_failure(
    tmp_path: Path, fail_page: int | None
) -> None:
    workspace = _workspace(tmp_path)
    try:
        processor = SelectiveOcrProcessor(FakeRenderer(), lambda: FakeEngine(fail_page=fail_page))
        if fail_page is None:
            processor.process(
                _extraction(["OCR_REQUIRED"]),
                41,
                workspace,
                document_version_id=DOCUMENT_VERSION_ID,
                content_sha256=CONTENT_SHA256,
            )
        else:
            with pytest.raises(RuntimeError, match="synthetic engine failure"):
                processor.process(
                    _extraction(["OCR_REQUIRED"]),
                    41,
                    workspace,
                    document_version_id=DOCUMENT_VERSION_ID,
                    content_sha256=CONTENT_SHA256,
                )
        assert list(workspace.path.glob("*.png")) == []
        assert list(workspace.path.glob("*.tsv")) == []
    finally:
        workspace.close_and_cleanup()


def test_unlink_failure_is_a_stable_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)

    def fail_unlink(_path: Path) -> None:
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr("familycare_worker.ocr.processor._unlink", fail_unlink)
    try:
        with pytest.raises(OcrTempCleanupError) as captured:
            SelectiveOcrProcessor(FakeRenderer(), FakeEngine).process(
                _extraction(["OCR_REQUIRED"]),
                41,
                workspace,
                document_version_id=DOCUMENT_VERSION_ID,
                content_sha256=CONTENT_SHA256,
            )

        assert str(captured.value) == "TEMP_CLEANUP_FAILED"
    finally:
        workspace.close_and_cleanup()

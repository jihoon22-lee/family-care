"""Descriptor-only PDFium rendering tests using from-scratch synthetic PDFs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from familycare_worker.ocr.models import OcrConfigurationError, OcrRenderError
from familycare_worker.ocr.renderer import PdfiumPageRenderer
from familycare_worker.pdf.workspace import create_workspace
from PIL import Image
from reportlab.pdfgen.canvas import Canvas


def _two_page_pdf(path: Path) -> Path:
    canvas = Canvas(str(path), pagesize=(216, 288), invariant=1, pageCompression=0)
    canvas.drawString(24, 250, "Synthetic page one")
    canvas.showPage()
    canvas.drawString(24, 250, "Synthetic page two")
    canvas.showPage()
    canvas.save()
    return path


def test_renderer_reads_descriptor_and_renders_requested_page_to_secure_handle(
    tmp_path: Path,
) -> None:
    source_path = _two_page_pdf(tmp_path / "synthetic-two-page.pdf")
    work_root = tmp_path / "work"
    work_root.mkdir()
    workspace = create_workspace(work_root)
    source_fd = os.open(source_path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with workspace.create_file("synthetic-page.png") as output:
            rendered = PdfiumPageRenderer().render(source_fd, 2, output, dpi=300)
            output.seek(0)
            with Image.open(output) as image:
                assert image.format == "PNG"
                assert image.size == (rendered.image_width_pixels, rendered.image_height_pixels)
                assert image.getpixel((50, 50)) is not None
            assert os.fstat(output.fileno()).st_mode & 0o777 == 0o600
        assert rendered.page_number == 2
        assert rendered.rendered_dpi == 300
        assert rendered.image_width_pixels in {900, 901}
        assert rendered.image_height_pixels == 1200
    finally:
        os.close(source_fd)
        workspace.close_and_cleanup()


@pytest.mark.parametrize(("page_number", "dpi"), [(0, 300), (3, 300), (1, 299)])
def test_renderer_rejects_invalid_page_or_dpi(tmp_path: Path, page_number: int, dpi: int) -> None:
    source_path = _two_page_pdf(tmp_path / "synthetic.pdf")
    source_fd = os.open(source_path, os.O_RDONLY | os.O_CLOEXEC)
    output_path = tmp_path / "output.png"
    output_fd = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        with (
            os.fdopen(output_fd, "w+b", closefd=False) as output,
            pytest.raises(OcrConfigurationError),
        ):
            PdfiumPageRenderer().render(source_fd, page_number, output, dpi=dpi)
    finally:
        os.close(output_fd)
        os.close(source_fd)


def test_renderer_rejects_writable_source_descriptor(tmp_path: Path) -> None:
    source_path = _two_page_pdf(tmp_path / "synthetic.pdf")
    source_fd = os.open(source_path, os.O_RDWR | os.O_CLOEXEC)
    output_fd = os.open(tmp_path / "output.png", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        with (
            os.fdopen(output_fd, "w+b", closefd=False) as output,
            pytest.raises(OcrConfigurationError),
        ):
            PdfiumPageRenderer().render(source_fd, 1, output, dpi=300)
    finally:
        os.close(output_fd)
        os.close(source_fd)


def test_renderer_rejects_output_without_mode_0600(tmp_path: Path) -> None:
    source_path = _two_page_pdf(tmp_path / "synthetic.pdf")
    source_fd = os.open(source_path, os.O_RDONLY | os.O_CLOEXEC)
    output_fd = os.open(tmp_path / "output.png", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o640)
    os.fchmod(output_fd, 0o640)
    try:
        with (
            os.fdopen(output_fd, "w+b", closefd=False) as output,
            pytest.raises(OcrConfigurationError),
        ):
            PdfiumPageRenderer().render(source_fd, 1, output, dpi=300)
    finally:
        os.close(output_fd)
        os.close(source_fd)


def test_renderer_rejects_oversized_page_before_allocating_bitmap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedPage:
        render_called = False

        def get_size(self) -> tuple[float, float]:
            return (5_000.0, 5_000.0)

        def render(self, *, scale: float) -> object:
            del scale
            self.render_called = True
            raise AssertionError("bitmap allocation must not start")

        def close(self) -> None:
            return None

    class SyntheticDocument:
        page = OversizedPage()

        def __len__(self) -> int:
            return 1

        def get_page(self, index: int) -> OversizedPage:
            assert index == 0
            return self.page

        def close(self) -> None:
            return None

    document = SyntheticDocument()
    monkeypatch.setattr(
        "familycare_worker.ocr.renderer.pdfium.PdfDocument",
        lambda _source: document,
    )
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(b"%PDF-1.7\nsynthetic")
    source_fd = os.open(source_path, os.O_RDONLY | os.O_CLOEXEC)
    output_fd = os.open(tmp_path / "output.png", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        with (
            os.fdopen(output_fd, "w+b", closefd=False) as output,
            pytest.raises(OcrRenderError),
        ):
            PdfiumPageRenderer().render(source_fd, 1, output, dpi=300)
        assert document.page.render_called is False
    finally:
        os.close(output_fd)
        os.close(source_fd)

"""Descriptor-only bounded PDFium rendering into a pre-created secure handle."""

from __future__ import annotations

import fcntl
import math
import os
import stat
from typing import BinaryIO

import pypdfium2 as pdfium  # type: ignore[import-untyped]

from familycare_worker.pdf.limits import MAX_INPUT_BYTES, WORKSPACE_FILE_MODE

from .models import OcrConfigurationError, OcrRenderError, RenderedPage

RENDER_DPI = 300
MAX_RENDERED_PIXELS = 25_000_000
MAX_RENDERED_DIMENSION = 20_000
_READ_CHUNK_BYTES = 1024 * 1024


def _descriptor_access_mode(descriptor: int) -> int:
    try:
        return fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
    except OSError:
        raise OcrConfigurationError from None


def _read_source(source_fd: int) -> bytes:
    try:
        metadata = os.fstat(source_fd)
    except OSError:
        raise OcrConfigurationError from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _descriptor_access_mode(source_fd) != os.O_RDONLY
        or metadata.st_size <= 0
        or metadata.st_size > MAX_INPUT_BYTES
    ):
        raise OcrConfigurationError
    chunks: list[bytes] = []
    offset = 0
    try:
        while offset < metadata.st_size:
            chunk = os.pread(
                source_fd,
                min(_READ_CHUNK_BYTES, metadata.st_size - offset),
                offset,
            )
            if not chunk:
                raise OcrRenderError
            chunks.append(chunk)
            offset += len(chunk)
    except OcrRenderError:
        raise
    except OSError:
        raise OcrRenderError from None
    return b"".join(chunks)


def _validate_output(output: BinaryIO) -> None:
    try:
        metadata = os.fstat(output.fileno())
    except AttributeError, OSError, ValueError:
        raise OcrConfigurationError from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != WORKSPACE_FILE_MODE
        or _descriptor_access_mode(output.fileno()) == os.O_RDONLY
    ):
        raise OcrConfigurationError


class PdfiumPageRenderer:
    """Render one 1-based page without accepting a source filesystem path."""

    def render(
        self,
        source_fd: int,
        page_number: int,
        output: BinaryIO,
        *,
        dpi: int,
    ) -> RenderedPage:
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or dpi != RENDER_DPI
        ):
            raise OcrConfigurationError
        _validate_output(output)
        source = _read_source(source_fd)
        document = None
        page = None
        bitmap = None
        image = None
        try:
            document = pdfium.PdfDocument(source)
            if page_number > len(document):
                raise OcrConfigurationError
            page = document.get_page(page_number - 1)
            page_width, page_height = page.get_size()
            if (
                not math.isfinite(page_width)
                or not math.isfinite(page_height)
                or page_width <= 0
                or page_height <= 0
            ):
                raise OcrRenderError
            expected_width = math.ceil(page_width * dpi / 72)
            expected_height = math.ceil(page_height * dpi / 72)
            if (
                expected_width > MAX_RENDERED_DIMENSION
                or expected_height > MAX_RENDERED_DIMENSION
                or expected_width * expected_height > MAX_RENDERED_PIXELS
            ):
                raise OcrRenderError
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil()
            width, height = image.size
            if (
                width < 1
                or height < 1
                or width > MAX_RENDERED_DIMENSION
                or height > MAX_RENDERED_DIMENSION
                or width * height > MAX_RENDERED_PIXELS
            ):
                raise OcrRenderError
            output.seek(0)
            output.truncate(0)
            image.save(output, format="PNG", optimize=False)
            output.flush()
            os.fsync(output.fileno())
            return RenderedPage(
                page_number=page_number,
                rendered_dpi=dpi,
                image_width_pixels=width,
                image_height_pixels=height,
            )
        except OcrConfigurationError, OcrRenderError:
            raise
        except Exception:
            raise OcrRenderError from None
        finally:
            if image is not None:
                image.close()
            if bitmap is not None:
                bitmap.close()
            if page is not None:
                page.close()
            if document is not None:
                document.close()


__all__ = [
    "MAX_RENDERED_DIMENSION",
    "MAX_RENDERED_PIXELS",
    "PdfiumPageRenderer",
    "RENDER_DPI",
]

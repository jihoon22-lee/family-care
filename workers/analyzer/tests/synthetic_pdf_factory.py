"""Deterministic, synthetic-only PDF fixtures for analyzer tests.

The builders intentionally use only ReportLab so that tests never need a real
document or a checked-in binary fixture.  ``invariant=1`` fixes ReportLab's
document identifiers and timestamps, making repeated builds byte-for-byte
stable.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen.canvas import Canvas

_PAGE_SIZE = letter
_TEXT_X = 72.0
_TEXT_Y = 700.0


def _new_canvas(path: Path, *, password: str | None = None) -> Canvas:
    path.parent.mkdir(parents=True, exist_ok=True)
    encryption = StandardEncryption(password) if password is not None else None
    return Canvas(str(path), pagesize=_PAGE_SIZE, invariant=1, encrypt=encryption)


def make_text_pdf(path: Path) -> Path:
    """Write a one-page PDF with three separately positioned synthetic words."""

    canvas = _new_canvas(path)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(_TEXT_X, _TEXT_Y, "Synthetic")
    canvas.drawString(160.0, _TEXT_Y, "Policy")
    canvas.drawString(230.0, _TEXT_Y, "Evidence")
    canvas.save()
    return path


def make_table_pdf(path: Path) -> Path:
    """Write a one-page ruled 2x2 grid with synthetic cell labels."""

    canvas = _new_canvas(path)
    x_origin = 72.0
    y_origin = 500.0
    cell_width = 180.0
    cell_height = 60.0

    canvas.setLineWidth(1.0)
    canvas.line(x_origin, y_origin, x_origin + 2 * cell_width, y_origin)
    canvas.line(
        x_origin,
        y_origin + cell_height,
        x_origin + 2 * cell_width,
        y_origin + cell_height,
    )
    canvas.line(
        x_origin,
        y_origin + 2 * cell_height,
        x_origin + 2 * cell_width,
        y_origin + 2 * cell_height,
    )
    canvas.line(x_origin, y_origin, x_origin, y_origin + 2 * cell_height)
    canvas.line(
        x_origin + cell_width,
        y_origin,
        x_origin + cell_width,
        y_origin + 2 * cell_height,
    )
    canvas.line(
        x_origin + 2 * cell_width,
        y_origin,
        x_origin + 2 * cell_width,
        y_origin + 2 * cell_height,
    )

    canvas.setFont("Helvetica", 10)
    canvas.drawString(x_origin + 12.0, y_origin + cell_height + 22.0, "Synthetic A1")
    canvas.drawString(
        x_origin + cell_width + 12.0,
        y_origin + cell_height + 22.0,
        "Synthetic B1",
    )
    canvas.drawString(x_origin + 12.0, y_origin + 22.0, "Synthetic A2")
    canvas.drawString(x_origin + cell_width + 12.0, y_origin + 22.0, "Synthetic B2")
    canvas.save()
    return path


def make_low_quality_pdf(path: Path) -> Path:
    """Write a one-page PDF containing only a short synthetic label."""

    canvas = _new_canvas(path)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(_TEXT_X, _TEXT_Y, "Low Quality")
    canvas.save()
    return path


def make_encrypted_pdf(path: Path, password: str) -> Path:
    """Write a deterministic encrypted PDF using the caller's one-shot password."""

    canvas = _new_canvas(path, password=password)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(_TEXT_X, _TEXT_Y, "Encrypted Evidence")
    canvas.save()
    return path

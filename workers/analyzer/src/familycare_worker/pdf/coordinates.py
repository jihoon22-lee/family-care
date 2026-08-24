"""Validation and normalization for PDF-point bounding boxes."""

from __future__ import annotations

import math

from familycare_worker.pdf.errors import PdfCorrupt

__all__ = ["normalize_bbox"]


def _finite_number(value: float) -> float:
    """Return a finite number or raise the sanitized corrupt-PDF error."""

    try:
        number = float(value)
    except TypeError, ValueError, OverflowError:
        raise PdfCorrupt from None
    if not math.isfinite(number):
        raise PdfCorrupt
    return number


def _rounded_coordinate(value: float) -> float:
    """Round a coordinate to the contract precision, avoiding negative zero."""

    rounded = round(value, 3)
    return 0.0 if rounded == 0 else rounded


def normalize_bbox(
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[float]:
    """Validate and round a top-left-origin PDF bounding box.

    ``page_width`` and ``page_height`` are optional to preserve the small
    four-coordinate normalization interface used by callers that validate
    page bounds separately. When supplied, they are validated as one pair and
    the right and bottom edges must be inside the inclusive page bounds.
    """

    if (page_width is None) != (page_height is None):
        raise PdfCorrupt

    left, top_edge, right, bottom_edge = (_finite_number(value) for value in (x0, top, x1, bottom))

    if (
        left < 0
        or top_edge < 0
        or right < 0
        or bottom_edge < 0
        or left > right
        or top_edge > bottom_edge
    ):
        raise PdfCorrupt

    if page_width is not None and page_height is not None:
        width = _finite_number(page_width)
        height = _finite_number(page_height)
        if width <= 0 or height <= 0 or right > width or bottom_edge > height:
            raise PdfCorrupt

    return [
        _rounded_coordinate(left),
        _rounded_coordinate(top_edge),
        _rounded_coordinate(right),
        _rounded_coordinate(bottom_edge),
    ]

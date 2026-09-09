"""Check a generated, nonembedded Korean font fixture inside the Worker image.

Run with the PDF produced by create_korean_font_fixture.py. This checks visible
glyph cells, not OCR accuracy. It needs only the image's PDFium/Pillow packages,
creates no page images on disk, and fails instead of skipping missing fonts.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image

GLYPHS = "가나다라마바사"
PAGE_SIZE = (300, 180)
RENDER_DPI = 300
IMAGE_SIZE = (1250, 750)
GLYPH_X = 24
GLYPH_STEP = 36
GLYPH_BASELINE = 110
GLYPH_SIZE = 24
CONTROL_X = 24
CONTROL_BASELINE = 40
CONTROL_SIZE = 14
_MAX_FIXTURE_BYTES = 65_536


def _pixel_region(left: int, bottom: int, right: int, top: int) -> tuple[int, int, int, int]:
    scale = RENDER_DPI / 72
    return (
        math.floor(left * scale),
        math.floor((PAGE_SIZE[1] - top) * scale),
        math.ceil(right * scale),
        math.ceil((PAGE_SIZE[1] - bottom) * scale),
    )


def glyph_regions() -> tuple[tuple[int, int, int, int], ...]:
    return tuple(
        _pixel_region(
            GLYPH_X + index * GLYPH_STEP - 1,
            GLYPH_BASELINE - 6,
            GLYPH_X + index * GLYPH_STEP + GLYPH_SIZE + 1,
            GLYPH_BASELINE + GLYPH_SIZE + 2,
        )
        for index in range(len(GLYPHS))
    )


def _dark_pixels(image: Image.Image, region: tuple[int, int, int, int]) -> int:
    with image.crop(region) as crop, crop.convert("L") as grayscale:
        return sum(grayscale.histogram()[:128])


def validate_rendered_image(image: Image.Image) -> None:
    if image.size != IMAGE_SIZE:
        raise ValueError("Unexpected rendered dimensions")
    for region in glyph_regions():
        if _dark_pixels(image, region) < 50:
            raise ValueError("Korean glyph cell is blank")
    control_region = _pixel_region(CONTROL_X - 1, 35, 250, 57)
    if _dark_pixels(image, control_region) < 50:
        raise ValueError("Latin control is blank")


def check_fixture(path: Path) -> None:
    with path.open("rb") as source:
        data = source.read(_MAX_FIXTURE_BYTES + 1)
    if not data or len(data) > _MAX_FIXTURE_BYTES:
        raise ValueError("Unexpected synthetic fixture size")
    with pdfium.PdfDocument(data) as document:
        if len(document) != 1:
            raise ValueError("Unexpected synthetic fixture page count")
        with document.get_page(0) as page:
            if page.get_size() != PAGE_SIZE:
                raise ValueError("Unexpected synthetic fixture page size")
            with page.render(scale=RENDER_DPI / 72) as bitmap, bitmap.to_pil() as image:
                validate_rendered_image(image)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    try:
        check_fixture(args.fixture)
    except Exception:
        print("Synthetic Korean font rendering failed", file=sys.stderr)
        return 1
    print("Synthetic Korean font rendering passed: seven glyph cells and Latin control")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Wholly synthetic coverage for the Worker image's missing CJK font regression."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject

from scripts.check_korean_font_rendering import (
    GLYPHS,
    IMAGE_SIZE,
    glyph_regions,
    validate_rendered_image,
)
from scripts.create_korean_font_fixture import create_fixture


def test_fixture_is_deterministic_korean_text_without_embedded_fonts(tmp_path: Path) -> None:
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    create_fixture(first)
    create_fixture(second)
    assert first.read_bytes() == second.read_bytes()
    checked_in = Path(__file__).resolve().parents[2] / "fixtures/synthetic/korean-font-fallback.pdf"
    assert first.read_bytes() == checked_in.read_bytes()
    reader = PdfReader(first)
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert tuple(page.mediabox) == (0, 0, 300, 180)
    assert "".join(page.extract_text().split()) == GLYPHS + "SYNTHETIC123"
    resources = page["/Resources"]
    assert isinstance(resources, DictionaryObject)
    font_resources = resources["/Font"]
    assert isinstance(font_resources, DictionaryObject)
    fonts: list[DictionaryObject] = []
    for reference in font_resources.values():
        font = reference.get_object()
        assert isinstance(font, DictionaryObject)
        fonts.append(font)
    korean_fonts = [font for font in fonts if font.get("/Subtype") == "/Type0"]
    assert len(korean_fonts) == 1
    korean = korean_fonts[0]
    assert str(korean["/BaseFont"]) == "/HYSMyeongJo-Medium"
    assert str(korean["/Encoding"]) == "/UniKS-UCS2-H"
    descendants = korean["/DescendantFonts"]
    assert isinstance(descendants, ArrayObject)
    assert len(descendants) == 1
    descendant = descendants[0].get_object()
    assert isinstance(descendant, DictionaryObject)
    descriptor = descendant["/FontDescriptor"]
    assert isinstance(descriptor, DictionaryObject)
    assert all(key not in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _control_and_synthetic_shapes(*, missing_cell: int | None = None) -> Image.Image:
    image = Image.new("RGB", IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 540, 800, 580), fill="black")
    for index, (left, top, right, bottom) in enumerate(glyph_regions()):
        if index != missing_cell:
            draw.rectangle((left + 12, top + 10, right - 12, bottom - 10), fill="black")
    return image


def test_pixel_check_accepts_populated_separate_cells_and_control() -> None:
    with _control_and_synthetic_shapes() as image:
        validate_rendered_image(image)


@pytest.mark.parametrize("missing_cell", range(7))
def test_pixel_check_rejects_each_missing_glyph_despite_visible_control(missing_cell: int) -> None:
    with (
        _control_and_synthetic_shapes(missing_cell=missing_cell) as image,
        pytest.raises(ValueError, match="Korean glyph cell is blank"),
    ):
        validate_rendered_image(image)


def test_pixel_check_rejects_visible_glyphs_without_control() -> None:
    with _control_and_synthetic_shapes() as image:
        ImageDraw.Draw(image).rectangle((0, 500, IMAGE_SIZE[0], IMAGE_SIZE[1]), fill="white")
        with pytest.raises(ValueError, match="Latin control is blank"):
            validate_rendered_image(image)


def test_pixel_check_rejects_wrong_geometry() -> None:
    with (
        Image.new("RGB", (100, 100), "black") as image,
        pytest.raises(ValueError, match="Unexpected rendered dimensions"),
    ):
        validate_rendered_image(image)

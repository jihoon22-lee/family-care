"""Create a wholly synthetic PDF requiring a system Korean font substitute.

Host-only generator: ReportLab's UnicodeCIDFont references a Korean font without
embedding it. No original document, font binary, or copied layout is consumed.
Run as python -m scripts.create_korean_font_fixture /task-temp/synthetic.pdf.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from scripts.check_korean_font_rendering import (
    CONTROL_BASELINE,
    CONTROL_SIZE,
    CONTROL_X,
    GLYPH_BASELINE,
    GLYPH_SIZE,
    GLYPH_STEP,
    GLYPH_X,
    GLYPHS,
    PAGE_SIZE,
)


def create_fixture(path: Path) -> None:
    font_name = "HYSMyeongJo-Medium"
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    with path.open("xb") as output:
        canvas = Canvas(output, pagesize=PAGE_SIZE, invariant=1, pageCompression=0)
        canvas.setTitle("Synthetic Korean font rendering fixture")
        canvas.setFont(font_name, GLYPH_SIZE)
        for index, glyph in enumerate(GLYPHS):
            canvas.drawString(GLYPH_X + index * GLYPH_STEP, GLYPH_BASELINE, glyph)
        canvas.setFont("Helvetica", CONTROL_SIZE)
        canvas.drawString(CONTROL_X, CONTROL_BASELINE, "SYNTHETIC 123")
        canvas.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_fixture(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

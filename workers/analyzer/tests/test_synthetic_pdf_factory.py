"""Regression tests for deterministic synthetic PDF fixtures."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from workers.analyzer.tests.synthetic_pdf_factory import (
    make_encrypted_pdf,
    make_low_quality_pdf,
    make_table_pdf,
    make_text_pdf,
)


def test_make_text_pdf_writes_deterministic_synthetic_labels(tmp_path: Path) -> None:
    first_path = tmp_path / "text-first.pdf"
    second_path = tmp_path / "text-second.pdf"

    assert make_text_pdf(first_path) == first_path
    assert make_text_pdf(second_path) == second_path
    assert first_path.read_bytes() == second_path.read_bytes()

    reader = PdfReader(str(first_path))
    assert reader.is_encrypted is False
    assert "Synthetic Policy Evidence" in " ".join(reader.pages[0].extract_text().split())


def test_every_builder_is_byte_stable(tmp_path: Path) -> None:
    builders = (
        ("text", lambda path: make_text_pdf(path)),
        ("table", lambda path: make_table_pdf(path)),
        ("low-quality", lambda path: make_low_quality_pdf(path)),
        (
            "encrypted",
            lambda path: make_encrypted_pdf(path, "synthetic-password"),
        ),
    )

    for name, builder in builders:
        first_path = builder(tmp_path / "first" / f"{name}.pdf")
        second_path = builder(tmp_path / "second" / f"{name}.pdf")
        assert first_path.read_bytes() == second_path.read_bytes()


def test_make_table_pdf_writes_a_ruled_two_by_two_grid(tmp_path: Path) -> None:
    path = make_table_pdf(tmp_path / "table.pdf")

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        tables = page.find_tables()
        assert len(tables) == 1
        assert tables[0].extract() == [
            ["Synthetic A1", "Synthetic B1"],
            ["Synthetic A2", "Synthetic B2"],
        ]


def test_make_low_quality_pdf_writes_short_synthetic_label(tmp_path: Path) -> None:
    path = make_low_quality_pdf(tmp_path / "low-quality.pdf")

    reader = PdfReader(str(path))
    assert "Low Quality" in " ".join(reader.pages[0].extract_text().split())


def test_make_encrypted_pdf_requires_the_caller_password(tmp_path: Path) -> None:
    path = make_encrypted_pdf(tmp_path / "encrypted.pdf", "synthetic-password")

    reader = PdfReader(str(path))
    assert reader.is_encrypted is True
    assert reader.decrypt("wrong-synthetic-password") == 0
    assert reader.decrypt("synthetic-password") != 0
    assert "Encrypted Evidence" in reader.pages[0].extract_text()

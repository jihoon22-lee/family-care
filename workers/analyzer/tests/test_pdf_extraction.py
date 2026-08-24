"""Synthetic-only extraction tests for text, tables, evidence, and isolation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from familycare_worker.pdf.errors import InvalidRequest, PdfCorrupt
from familycare_worker.pdf.extractor import ExtractionSettings, PdfPlumberExtractor
from familycare_worker.pdf.intake import open_source, validate_pdf
from familycare_worker.pdf.isolation import run_isolated_parser


def _load_factory() -> Any:
    path = Path(__file__).with_name("synthetic_pdf_factory.py")
    spec = importlib.util.spec_from_file_location("synthetic_pdf_factory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTORY = _load_factory()
DOCUMENT_VERSION_ID = "00000000-0000-4000-8000-000000000101"
CONFIG_HASH = "b" * 64


def _copy_to_ingest_root(source: Path, ingest_root: Path) -> Path:
    ingest_root.mkdir()
    destination = ingest_root / source.name
    shutil.copyfile(source, destination)
    return destination


def _settings(content_sha256: str, *, table_strategy: str = "auto") -> ExtractionSettings:
    return ExtractionSettings(
        document_version_id=DOCUMENT_VERSION_ID,
        content_sha256=content_sha256,
        extractor_config_hash=CONFIG_HASH,
        quality_rule_version="quality-v1",
        table_strategy=table_strategy,
    )


def test_words_are_ordered_text_blocks_with_page_relative_pdf_points(
    tmp_path: Path,
) -> None:
    authored = FACTORY.make_text_pdf(tmp_path / "authored" / "synthetic-text.pdf")
    copied = _copy_to_ingest_root(authored, tmp_path / "ingest")

    with open_source(copied.parent, copied.name) as source:
        validated = validate_pdf(source)
        original_offset = source.fd
        result = PdfPlumberExtractor().extract(source.fd, _settings(validated.content_sha256))

    assert original_offset >= 0
    assert result["schema_version"] == "1"
    assert result["content_sha256"] == validated.content_sha256
    assert result["extractor_name"] == "pdfplumber"
    assert result["extractor_version"] == "0.11.10"
    assert result["extractor_config_hash"] == CONFIG_HASH
    assert result["quality_rule_version"] == "quality-v1"
    assert len(result["pages"]) == 1

    page = result["pages"][0]
    assert page["page_number"] == 1
    assert page["width_points"] > 0
    assert page["height_points"] > 0
    assert page["quality"]["classification"] == "TEXT_SUFFICIENT"
    assert page["warning_codes"] == []
    assert [block["reading_order"] for block in page["blocks"]] == list(range(len(page["blocks"])))
    assert [block["text"] for block in page["blocks"]][:3] == [
        "Synthetic",
        "Policy",
        "Evidence",
    ]
    for block in page["blocks"]:
        assert block["page_number"] == 1
        x0, top, x1, bottom = block["bbox"]
        assert 0 <= x0 <= x1 <= page["width_points"]
        assert 0 <= top <= bottom <= page["height_points"]
        assert block["bbox"] == [round(value, 3) for value in block["bbox"]]

    assert len(result["evidence"]) >= len(page["blocks"])
    for evidence in result["evidence"]:
        assert evidence["document_version_id"] == DOCUMENT_VERSION_ID
        assert evidence["content_sha256"] == validated.content_sha256
        assert evidence["page_number"] == 1
        assert evidence["review_state"] == "candidate"


def test_ruled_table_retains_table_and_cell_coordinates(tmp_path: Path) -> None:
    authored = FACTORY.make_table_pdf(tmp_path / "authored" / "synthetic-table.pdf")
    copied = _copy_to_ingest_root(authored, tmp_path / "ingest")

    with open_source(copied.parent, copied.name) as source:
        validated = validate_pdf(source)
        result = PdfPlumberExtractor().extract(
            source.fd,
            _settings(validated.content_sha256, table_strategy="lines"),
        )

    page = result["pages"][0]
    assert page["tables"]
    table = page["tables"][0]
    assert table["review_state"] == "candidate"
    assert len(table["cells"]) == 4
    assert {(cell["row_index"], cell["column_index"]) for cell in table["cells"]} == {
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    }
    assert all(cell["text"].startswith("Synthetic") for cell in table["cells"])
    assert all(cell["review_state"] == "candidate" for cell in table["cells"])
    assert all(len(cell["bbox"]) == 4 for cell in table["cells"])


def test_low_quality_page_is_classified_without_running_ocr(tmp_path: Path) -> None:
    authored = FACTORY.make_low_quality_pdf(tmp_path / "authored" / "synthetic-low.pdf")
    copied = _copy_to_ingest_root(authored, tmp_path / "ingest")

    with open_source(copied.parent, copied.name) as source:
        validated = validate_pdf(source)
        result = PdfPlumberExtractor().extract(source.fd, _settings(validated.content_sha256))

    page = result["pages"][0]
    assert page["quality"]["classification"] == "OCR_REQUIRED"
    assert page["warning_codes"] == ["OCR_REQUIRED"]
    assert "ocr" not in json.dumps(result).lower().replace("ocr_required", "")


def test_isolated_entrypoint_returns_bounded_json_result(tmp_path: Path) -> None:
    authored = FACTORY.make_text_pdf(tmp_path / "authored" / "synthetic-isolated.pdf")
    copied = _copy_to_ingest_root(authored, tmp_path / "ingest")

    with open_source(copied.parent, copied.name) as source:
        validated = validate_pdf(source)
        settings_json = _settings(validated.content_sha256).to_json()
        outcome = run_isolated_parser(source.fd, settings_json)

    assert outcome.success is True
    assert isinstance(outcome.result, dict)
    assert outcome.result["content_sha256"] == validated.content_sha256
    assert outcome.result["pages"][0]["blocks"][0]["text"] == "Synthetic"


def test_extraction_settings_reject_forbidden_or_invalid_runtime_values() -> None:
    valid = json.loads(_settings("a" * 64).to_json())
    for key, value in (
        ("password", "synthetic-secret"),
        ("source_path", "/synthetic/private.pdf"),
        ("content_sha256", "not-a-hash"),
        ("document_version_id", "not-a-uuid"),
        ("table_strategy", "external"),
    ):
        mutation = {**valid, key: value}
        try:
            ExtractionSettings.from_json(json.dumps(mutation))
        except InvalidRequest as error:
            assert str(error) == "INVALID_REQUEST"
        else:
            raise AssertionError(f"invalid synthetic setting accepted: {key}")


def test_extraction_does_not_change_parent_descriptor_offset(tmp_path: Path) -> None:
    authored = FACTORY.make_text_pdf(tmp_path / "authored" / "synthetic-offset.pdf")
    copied = _copy_to_ingest_root(authored, tmp_path / "ingest")

    with open_source(copied.parent, copied.name) as source:
        validated = validate_pdf(source)
        expected_hash = hashlib.sha256(copied.read_bytes()).hexdigest()
        assert validated.content_sha256 == expected_hash
        os.lseek(source.fd, 11, os.SEEK_SET)
        expected_offset = os.lseek(source.fd, 0, os.SEEK_CUR)
        PdfPlumberExtractor().extract(source.fd, _settings(validated.content_sha256))
        assert os.lseek(source.fd, 0, os.SEEK_CUR) == expected_offset


def test_page_and_document_caches_close_after_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SyntheticPage:
        width = 100.0
        height = 200.0
        closed = False

        def extract_words(self, **kwargs: object) -> list[dict[str, object]]:
            del kwargs
            return [
                {
                    "text": "SyntheticEvidencePageText",
                    "x0": 1.0,
                    "top": 2.0,
                    "x1": 50.0,
                    "bottom": 12.0,
                }
            ]

        def find_tables(self, table_settings: object) -> list[object]:
            del table_settings
            return []

        def close(self) -> None:
            self.closed = True

    class SyntheticPdf:
        def __init__(self) -> None:
            self.pages = [SyntheticPage()]
            self.closed = False

        def close(self) -> None:
            self.closed = True

    synthetic_pdf = SyntheticPdf()
    monkeypatch.setattr(
        "familycare_worker.pdf.extractor.pdfplumber.open",
        lambda handle, password=None: synthetic_pdf,
    )
    source = tmp_path / "synthetic-cache.pdf"
    source.write_bytes(b"%PDF-synthetic-cache")
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = PdfPlumberExtractor().extract(fd, _settings("e" * 64))
    finally:
        os.close(fd)

    assert result["pages"][0]["blocks"][0]["text"] == "SyntheticEvidencePageText"
    assert synthetic_pdf.pages[0].closed is True
    assert synthetic_pdf.closed is True


@pytest.mark.parametrize("invalid_dimension", [float("nan"), float("inf"), 0.0, -1.0])
def test_invalid_page_dimensions_are_sanitized_as_corrupt_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_dimension: float,
) -> None:
    class SyntheticPage:
        width = invalid_dimension
        height = 200.0

        def extract_words(self, **kwargs: object) -> list[dict[str, object]]:
            del kwargs
            return []

        def find_tables(self, table_settings: object) -> list[object]:
            del table_settings
            return []

        def close(self) -> None:
            return None

    class SyntheticPdf:
        pages = [SyntheticPage()]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "familycare_worker.pdf.extractor.pdfplumber.open",
        lambda handle, password=None: SyntheticPdf(),
    )
    source = tmp_path / "synthetic-invalid-dimension.pdf"
    source.write_bytes(b"%PDF-synthetic-invalid-dimension")
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(PdfCorrupt) as raised:
            PdfPlumberExtractor().extract(fd, _settings("f" * 64))
    finally:
        os.close(fd)

    assert str(raised.value) == "PDF_CORRUPT"
    assert raised.value.__cause__ is None

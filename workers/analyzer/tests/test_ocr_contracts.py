"""Worker-facing OCR contract privacy and provenance tests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "packages/contracts/schemas/ocr-result.v1.schema.json"
EXAMPLE_PATH = ROOT / "packages/contracts/examples/ocr-result.v1.json"


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | {
            nested for child in value.values() for nested in _nested_keys(child)
        }
    if isinstance(value, list):
        return {nested for child in value for nested in _nested_keys(child)}
    return set()


def test_ocr_contract_has_no_runtime_file_or_secret_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    forbidden = {
        "absolute_path",
        "archive_key",
        "image_bytes",
        "image_path",
        "password",
        "pdf_bytes",
        "source_key",
        "tsv_path",
    }

    assert forbidden.isdisjoint(_nested_keys(schema))
    assert forbidden.isdisjoint(_nested_keys(example))
    assert example["engine_name"] == "tesseract"
    assert example["pages"][0]["blocks"][0]["review_state"] == "candidate"


def test_native_extraction_contract_is_not_extended_with_ocr_output() -> None:
    native_schema = json.loads(
        (ROOT / "packages/contracts/schemas/extraction-result.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert "ocr" not in native_schema["properties"]
    assert "ocr_blocks" not in json.dumps(native_schema)

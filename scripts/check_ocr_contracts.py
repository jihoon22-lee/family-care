#!/usr/bin/env python3
"""Validate the local selective-OCR schema and synthetic example."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "packages/contracts/schemas/ocr-result.v1.schema.json"
EXAMPLE_PATH = ROOT / "packages/contracts/examples/ocr-result.v1.json"
UUID4_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "image_bytes",
    "image_path",
    "password",
    "pdf_bytes",
    "source_key",
    "tsv_path",
}


def _schema_validator() -> Callable[..., list[str]]:
    try:
        module = import_module("scripts.check_document_contracts")
    except ModuleNotFoundError:  # pragma: no cover
        module = import_module("check_document_contracts")
    return cast(Callable[..., list[str]], module.validate_schema_instance)


validate_schema_instance = _schema_validator()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing OCR contract: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"OCR contract must be an object: {path.relative_to(ROOT)}")
    return value


def _keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        found: list[tuple[str, str]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            found.append((child_path, str(key).lower()))
            found.extend(_keys(child, child_path))
        return found
    if isinstance(value, list):
        return [
            item for index, child in enumerate(value) for item in _keys(child, f"{path}[{index}]")
        ]
    return []


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    objects = [dict(value)] if value.get("type") == "object" else []
    return objects + [child for value_child in value.values() for child in _objects(value_child)]


def validate_ocr_contracts() -> list[str]:
    try:
        schema = _load(SCHEMA_PATH)
        example = _load(EXAMPLE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]

    errors = [
        f"OCR example schema mismatch: {error}"
        for error in validate_schema_instance(schema, example)
    ]
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("OCR schema must use JSON Schema draft 2020-12")
    if schema.get("title") != "OcrResult" or schema.get("additionalProperties") is not False:
        errors.append("OCR schema root must be a strict OcrResult object")
    if any(item.get("additionalProperties") is not False for item in _objects(schema)):
        errors.append("all OCR object schemas must reject additional properties")
    for label, value in (("schema", schema), ("example", example)):
        errors.extend(
            f"OCR {label} contains forbidden field at {path}"
            for path, key in _keys(value)
            if key in FORBIDDEN_FIELDS
        )
    if example.get("schema_version") != "1" or example.get("source_layer") != "ocr":
        errors.append("OCR example must use schema version 1 and the separate ocr layer")
    if example.get("engine_name") != "tesseract":
        errors.append("OCR example must use the local Tesseract engine")
    if example.get("language_codes") != ["kor", "eng"]:
        errors.append("OCR language order must be exactly kor, eng")
    if example.get("quality_rule_version") != "quality-v1":
        errors.append("OCR example must reference quality-v1")
    extraction_id = example.get("extraction_id")
    if not isinstance(extraction_id, str) or UUID4_PATTERN.fullmatch(extraction_id) is None:
        errors.append("OCR extraction_id must be UUIDv4")
    pages = example.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 500:
        errors.append("OCR example must contain 1 to 500 selected pages")
    else:
        numbers: list[int] = []
        for page in pages:
            if not isinstance(page, Mapping):
                continue
            page_number = page.get("page_number")
            if not isinstance(page_number, int) or isinstance(page_number, bool):
                errors.append("OCR page_number must be an integer")
                continue
            numbers.append(page_number)
            if page.get("selected_classification") != "OCR_REQUIRED":
                errors.append("OCR pages must originate from OCR_REQUIRED")
            evidence = page.get("evidence")
            if not isinstance(evidence, Mapping) or evidence.get("page_number") != page_number:
                errors.append("OCR Evidence page must match its selected page")
            elif (
                not isinstance(evidence.get("content_sha256"), str)
                or SHA256_PATTERN.fullmatch(str(evidence["content_sha256"])) is None
            ):
                errors.append("OCR Evidence content hash must be SHA-256 shaped")
        if numbers != sorted(set(numbers)):
            errors.append("OCR selected page numbers must be sorted and unique")
    return errors


def main() -> int:
    errors = validate_ocr_contracts()
    if errors:
        print("\n".join(errors))
        return 1
    print("OCR contract checks passed (separate provenance layer and synthetic example)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

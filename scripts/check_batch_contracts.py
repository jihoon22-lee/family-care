#!/usr/bin/env python3
"""Check encrypted document batch schemas, examples, and generated consumers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "packages/contracts/schemas"
EXAMPLE_ROOT = ROOT / "packages/contracts/examples"
API_OUTPUT = ROOT / "apps/api/src/familycare_api/documents/generated_batch_contracts.py"
WORKER_OUTPUT = ROOT / "workers/analyzer/src/familycare_worker/generated_batch_contracts.py"

REQUEST_SCHEMA_PATH = SCHEMA_ROOT / "document-batch.v1.schema.json"
STATUS_SCHEMA_PATH = SCHEMA_ROOT / "document-batch-status.v1.schema.json"
REQUEST_EXAMPLE_PATH = EXAMPLE_ROOT / "document-batch.v1.json"
STATUS_EXAMPLE_PATH = EXAMPLE_ROOT / "document-batch-status.v1.json"
MAX_IMPORT_SOURCE_BYTES = 128 * 1024 * 1024

SYNTHETIC_FAMILY_MEMBER_ID = "00000000-0000-4000-8000-000000000004"
SYNTHETIC_BATCH_ID = "00000000-0000-4000-8000-000000000005"
SYNTHETIC_SOURCE_IDS = (
    "a" * 64,
    "b" * 64,
)
PRIVATE_DOCUMENT_KINDS = [
    "application",
    "policy",
    "product_explanation",
    "supporting",
    "terms",
]
SOURCE_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")
UUID4_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "archive_master_key",
    "auth_tag",
    "bbox",
    "coordinates",
    "document_text",
    "file_name",
    "filename",
    "image_path",
    "key",
    "nonce",
    "object_key",
    "ocr_path",
    "ocr_text",
    "password",
    "path",
    "plaintext",
    "raw_pdf",
    "raw_error",
    "stderr",
    "source_key",
    "wrapped_data_key",
}
BATCH_STATES = ["created", "running", "partial", "succeeded", "failed", "cancelled"]
BATCH_ITEM_STATES = [
    "queued",
    "running",
    "succeeded",
    "password_required",
    "retryable_failed",
    "permanently_failed",
    "cancelled",
]
BATCH_ERROR_CODES = [
    "ARCHIVE_INTEGRITY_ERROR",
    "ARCHIVE_KEY_UNAVAILABLE",
    "ARCHIVE_WRITE_FAILED",
    "DOCUMENT_NOT_FOUND",
    "DOCUMENT_PATH_ESCAPE",
    "DOCUMENT_TOO_LARGE",
    "EXTRACTION_TIMEOUT",
    "INVALID_REQUEST",
    "OCR_FAILED",
    "OCR_TIMEOUT",
    "OCR_UNAVAILABLE",
    "OCR_OUTPUT_LIMIT_EXCEEDED",
    "PAGE_LIMIT_EXCEEDED",
    "PASSWORD_INVALID",
    "PASSWORD_REQUIRED",
    "PDF_CORRUPT",
    "RESOURCE_LIMIT_EXCEEDED",
    "SOURCE_CHANGED",
    "TEMP_CLEANUP_FAILED",
    "UNSUPPORTED_FILE_TYPE",
]
OCR_STATES = ["pending", "native_only", "running", "completed", "warning", "failed"]
OCR_WARNING_CODES = ["LOW_CONFIDENCE", "NO_TEXT_DETECTED"]


def _load_schema_validator() -> Callable[..., list[str]]:
    """Load the repository's small JSON-Schema validator without importing its CLI."""

    try:
        module = import_module("scripts.check_document_contracts")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("check_document_contracts")
    return cast(Callable[..., list[str]], module.validate_schema_instance)


validate_schema_instance = _load_schema_validator()


def _load_generator() -> Callable[..., str]:
    """Load the batch generator in package or direct-script execution context."""

    try:
        module = import_module("scripts.generate_batch_contract_types")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("generate_batch_contract_types")
    return cast(Callable[..., str], module.generate)


generate = _load_generator()


def load_json(path: Path) -> dict[str, Any]:
    """Read one contract artifact as a JSON object."""

    if not path.is_file():
        raise ValueError(f"missing contract artifact: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path.relative_to(ROOT)}")
    return value


def _nested_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        result: list[tuple[str, str]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            result.append((child_path, str(key)))
            result.extend(_nested_keys(child, child_path))
        return result
    if isinstance(value, list):
        result = []
        for index, child in enumerate(value):
            result.extend(_nested_keys(child, f"{path}[{index}]"))
        return result
    return []


def forbidden_keys(value: Any) -> list[str]:
    """Return nested contract paths that expose secrets or internal paths."""

    return [path for path, key in _nested_keys(value) if key.lower() in FORBIDDEN_FIELDS]


def _object_schemas(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if schema.get("type") == "object":
        objects.append(dict(schema))
    definitions = schema.get("$defs", {})
    if isinstance(definitions, Mapping):
        objects.extend(
            dict(definition)
            for definition in definitions.values()
            if isinstance(definition, Mapping) and definition.get("type") == "object"
        )
    return objects


def _validate_examples(
    request_schema: dict[str, Any],
    status_schema: dict[str, Any],
    request: dict[str, Any],
    status: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    errors.extend(
        f"request example schema mismatch: {error}"
        for error in validate_schema_instance(request_schema, request)
    )
    errors.extend(
        f"status example schema mismatch: {error}"
        for error in validate_schema_instance(status_schema, status)
    )

    expected_sources = [
        {"document_kind": "policy", "source_id": SYNTHETIC_SOURCE_IDS[0]},
        {"document_kind": "terms", "source_id": SYNTHETIC_SOURCE_IDS[1]},
    ]
    if request != {
        "family_member_id": SYNTHETIC_FAMILY_MEMBER_ID,
        "schema_version": "1",
        "sources": expected_sources,
    }:
        errors.append("request example must use the fixed synthetic batch projection")
    if request.get("family_member_id") != SYNTHETIC_FAMILY_MEMBER_ID:
        errors.append("request family_member_id must be synthetic")
    sources = request.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("request sources must contain at least one item")
    else:
        source_ids = [source.get("source_id") for source in sources if isinstance(source, Mapping)]
        if len(source_ids) != len(set(source_ids)):
            errors.append("request source IDs must be unique")
        if any(
            not isinstance(source, Mapping)
            or not isinstance(source.get("source_id"), str)
            or SOURCE_ID_PATTERN.fullmatch(str(source["source_id"])) is None
            for source in sources
        ):
            errors.append("request source IDs must be lowercase SHA-256-shaped IDs")
        if any(
            not isinstance(source, Mapping)
            or source.get("document_kind") not in PRIVATE_DOCUMENT_KINDS
            for source in sources
        ):
            errors.append("request source document kinds are unsupported")

    if status.get("batch_id") != SYNTHETIC_BATCH_ID:
        errors.append("status batch_id must be synthetic")
    if status.get("family_member_id") != SYNTHETIC_FAMILY_MEMBER_ID:
        errors.append("status family_member_id must be synthetic")
    items = status.get("items")
    if not isinstance(items, list) or not items:
        errors.append("status items must contain at least one item")
    elif any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("source_id"), str)
        or SOURCE_ID_PATTERN.fullmatch(str(item["source_id"])) is None
        for item in items
    ):
        errors.append("status item source_id must be lowercase SHA-256-shaped IDs")
    elif {str(item["source_id"]) for item in items} != set(SYNTHETIC_SOURCE_IDS):
        errors.append("status item source IDs must be the fixed synthetic IDs")
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, Mapping):
            continue
        if item.get("document_kind") not in PRIVATE_DOCUMENT_KINDS:
            errors.append(f"status item {index} document kind is unsupported")
        warnings = item.get("ocr_warning_codes")
        if isinstance(warnings, list) and len(warnings) != len(set(warnings)):
            errors.append(f"status item {index} OCR warning codes must be unique")
    return errors


def validate_batch_contracts() -> list[str]:
    """Return all batch contract, privacy, example, and generated-output errors."""

    try:
        request_schema = load_json(REQUEST_SCHEMA_PATH)
        status_schema = load_json(STATUS_SCHEMA_PATH)
        request = load_json(REQUEST_EXAMPLE_PATH)
        status = load_json(STATUS_EXAMPLE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]

    errors: list[str] = []
    if request_schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("request schema must use JSON Schema draft 2020-12")
    if status_schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("status schema must use JSON Schema draft 2020-12")
    if request_schema.get("title") != "DocumentBatchRequest":
        errors.append("request schema title changed")
    if status_schema.get("title") != "DocumentBatchStatus":
        errors.append("status schema title changed")
    for label, schema in (("request", request_schema), ("status", status_schema)):
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            errors.append(f"{label} schema root must be a strict object")
        if any(item.get("additionalProperties") is not False for item in _object_schemas(schema)):
            errors.append(f"{label} schema nested objects must reject additional properties")
        errors.extend(
            f"{label} schema contains forbidden field at {path}" for path in forbidden_keys(schema)
        )
    errors.extend(
        f"request example contains forbidden field at {path}" for path in forbidden_keys(request)
    )
    errors.extend(
        f"status example contains forbidden field at {path}" for path in forbidden_keys(status)
    )
    errors.extend(_validate_examples(request_schema, status_schema, request, status))

    request_source = request_schema.get("$defs", {}).get("SourceId", {})
    status_source = status_schema.get("$defs", {}).get("SourceId", {})
    for label, source in (("request", request_source), ("status", status_source)):
        if source.get("pattern") != "^[a-f0-9]{64}$":
            errors.append(f"{label} SourceId must be lowercase 64-character hex")
        if source.get("minLength") != 64 or source.get("maxLength") != 64:
            errors.append(f"{label} SourceId must be exactly 64 characters")
    definitions = status_schema.get("$defs", {})
    request_definitions = request_schema.get("$defs", {})
    if request_definitions.get("BatchDocumentKind", {}).get("enum") != PRIVATE_DOCUMENT_KINDS:
        errors.append("request batch document-kind enum changed")
    if definitions.get("BatchDocumentKind", {}).get("enum") != PRIVATE_DOCUMENT_KINDS:
        errors.append("status batch document-kind enum changed")
    request_sources = request_schema.get("properties", {}).get("sources", {})
    if (
        request_sources.get("minItems") != 1
        or request_sources.get("maxItems") != 100
        or request_sources.get("uniqueItems") is not True
        or request_sources.get("items") != {"$ref": "#/$defs/DocumentBatchSource"}
    ):
        errors.append("request sources must be bounded and per-source")
    source_definition = request_definitions.get("DocumentBatchSource", {})
    if (
        source_definition.get("additionalProperties") is not False
        or set(source_definition.get("required", [])) != {"source_id", "document_kind"}
        or source_definition.get("properties", {}).get("document_kind")
        != {"$ref": "#/$defs/BatchDocumentKind"}
    ):
        errors.append("request source entries must require source_id and document_kind")
    status_item_kind = (
        definitions.get("DocumentBatchItem", {}).get("properties", {}).get("document_kind", {})
    )
    if status_item_kind != {"$ref": "#/$defs/BatchDocumentKind"}:
        errors.append("status items must project document_kind")
    if definitions.get("BatchErrorCode", {}).get("enum") != BATCH_ERROR_CODES:
        errors.append("batch error-code enum changed")
    if definitions.get("BatchState", {}).get("enum") != BATCH_STATES:
        errors.append("batch state enum changed")
    if definitions.get("BatchItemState", {}).get("enum") != BATCH_ITEM_STATES:
        errors.append("batch item state enum changed")
    if definitions.get("OcrState", {}).get("enum") != OCR_STATES:
        errors.append("OCR state enum changed")
    if definitions.get("OcrWarningCode", {}).get("enum") != OCR_WARNING_CODES:
        errors.append("OCR warning-code enum changed")
    item_schema = definitions.get("DocumentBatchItem", {})
    ocr_pages_schema = item_schema.get("properties", {}).get("ocr_pages_processed", {})
    if ocr_pages_schema.get("minimum") != 0 or ocr_pages_schema.get("maximum") != 500:
        errors.append("OCR processed-page bounds changed")
    ocr_warnings_schema = item_schema.get("properties", {}).get("ocr_warning_codes", {})
    if (
        ocr_warnings_schema.get("maxItems") != 8
        or ocr_warnings_schema.get("uniqueItems") is not True
    ):
        errors.append("OCR warning-code bounds changed")
    import_source = definitions.get("ImportSource", {})
    import_source_size = import_source.get("properties", {}).get("size_bytes", {})
    if (
        import_source_size.get("minimum") != 0
        or import_source_size.get("maximum") != MAX_IMPORT_SOURCE_BYTES
    ):
        errors.append("import source size bound changed")

    for label, value in (("request", request), ("status", status)):
        for path, child in _nested_strings(value):
            if "\n" in child or "\r" in child or "\\" in child or "://" in child:
                errors.append(f"{label} example contains unsafe string at {path}")
    for label, value in (("request", request), ("status", status)):
        for key in ("family_member_id", "batch_id"):
            if key in value and not UUID4_PATTERN.fullmatch(str(value[key])):
                errors.append(f"{label} {key} must be a UUIDv4")

    if not API_OUTPUT.is_file() or not WORKER_OUTPUT.is_file():
        errors.append("generated batch contract module is missing")
    else:
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            generated_api = temporary_root / "api_generated_batch_contracts.py"
            generated_worker = temporary_root / "worker_generated_batch_contracts.py"
            try:
                generate(generated_api, generated_worker)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"batch contract generation failed: {error}")
            else:
                expected = generated_api.read_bytes()
                if generated_worker.read_bytes() != expected:
                    errors.append("generated batch API and Worker modules differ")
                if API_OUTPUT.read_bytes() != expected:
                    errors.append("API generated batch contract is stale")
                if WORKER_OUTPUT.read_bytes() != expected:
                    errors.append("Worker generated batch contract is stale")
    return errors


def _nested_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        strings: list[tuple[str, str]] = []
        for key, child in value.items():
            strings.extend(_nested_strings(child, f"{path}.{key}"))
        return strings
    if isinstance(value, list):
        strings = []
        for index, child in enumerate(value):
            strings.extend(_nested_strings(child, f"{path}[{index}]"))
        return strings
    if isinstance(value, str):
        return [(path, value)]
    return []


def main() -> int:
    errors = validate_batch_contracts()
    if errors:
        print("\n".join(errors))
        return 1
    print("batch contract checks passed (request, status, and generated API/Worker types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

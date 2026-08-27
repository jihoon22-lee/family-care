#!/usr/bin/env python3
"""Check document JSON Schemas, synthetic examples, and generated consumers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
EXAMPLE_ROOT = CONTRACT_ROOT / "examples"
API_OUTPUT = ROOT / "apps/api/src/familycare_api/documents/generated_contracts.py"
WORKER_OUTPUT = ROOT / "workers/analyzer/src/familycare_worker/generated_contracts.py"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_KEY_PATTERN_TEXT = (
    r"^(?!/)(?![A-Za-z]:)(?!.*\\)(?!.*[\r\n])(?!.*(?:^|/)\.\.(?:/|$))[^\u0000]+$"
)
SOURCE_KEY_PATTERN = re.compile(SOURCE_KEY_PATTERN_TEXT)
JOB_STATES = (
    "cancelled",
    "permanently_failed",
    "queued",
    "retryable_failed",
    "running",
    "succeeded",
)
ERROR_CODES = (
    "ANALYSIS_JOB_NOT_FOUND",
    "DOCUMENT_NOT_FOUND",
    "DOCUMENT_PATH_ESCAPE",
    "DOCUMENT_TOO_LARGE",
    "EXTRACTION_TIMEOUT",
    "INVALID_REQUEST",
    "PAGE_LIMIT_EXCEEDED",
    "PASSWORD_INVALID",
    "PASSWORD_REQUIRED",
    "PDF_CORRUPT",
    "RESOURCE_LIMIT_EXCEEDED",
    "TEMP_CLEANUP_FAILED",
    "UNSUPPORTED_FILE_TYPE",
)
FORBIDDEN_FIELDS = {"password", "absolute_path", "raw_pdf", "external_url"}
SAFETY_LIMITS = {
    "child_address_space_bytes": 1536 * 1024 * 1024,
    "child_cpu_limit_seconds": 90,
    "max_input_bytes": 128 * 1024 * 1024,
    "max_open_descriptors": 64,
    "max_output_bytes": 64 * 1024 * 1024,
    "max_pages": 500,
    "parent_wall_timeout_seconds": 120,
    "sha256_chunk_bytes": 1 * 1024 * 1024,
    "work_directory_mode": "0700",
    "work_file_mode": "0600",
}


def _load_generator() -> Any:
    """Load the generator from package or direct-script execution context."""

    try:
        module = import_module("scripts.generate_document_contract_types")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("generate_document_contract_types")
    return module.generate


generate = _load_generator()


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object and return a checker-friendly error on failure."""

    if not path.is_file():
        raise ValueError(f"missing contract artifact: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path.relative_to(ROOT)}")
    return value


def contains_key(value: Any, key: str) -> bool:
    """Return whether a nested JSON value contains a key."""

    if isinstance(value, dict):
        return key in value or any(contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(contains_key(child, key) for child in value)
    return False


def forbidden_keys(value: Any, path: str = "$") -> list[str]:
    """Return paths containing fields that must never cross the contract boundary."""

    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_FIELDS:
                errors.append(child_path)
            errors.extend(forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(forbidden_keys(child, f"{path}[{index}]"))
    return errors


def is_relative_source_key(value: Any) -> bool:
    """Apply the schema's source-key safety shape to a synthetic example."""

    return isinstance(value, str) and SOURCE_KEY_PATTERN.fullmatch(value) is not None


def valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def valid_uuid4(value: Any) -> bool:
    try:
        return UUID(str(value)).version == 4
    except ValueError, AttributeError:
        return False


def classify_quality(
    *,
    non_whitespace_chars: int,
    alphanumeric_ratio: float,
    replacement_character_ratio: float,
    maximum_repeated_character_run: int,
) -> str:
    """Apply the versioned four-threshold page-quality classification rule."""

    if (
        non_whitespace_chars < 20
        or alphanumeric_ratio < 0.25
        or replacement_character_ratio > 0.05
        or maximum_repeated_character_run > 20
    ):
        return "OCR_REQUIRED"
    return "TEXT_SUFFICIENT"


def _resolve_local_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any] | None:
    """Resolve the local JSON Pointer references used by these contracts."""

    if not reference.startswith("#/"):
        return None
    current: Any = root_schema
    for component in reference[2:].split("/"):
        component = component.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or component not in current:
            return None
        current = current[component]
    return current if isinstance(current, dict) else None


def validate_schema_instance(
    schema: dict[str, Any],
    value: Any,
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """Validate the supported JSON Schema subset used by the three v1 contracts."""

    root = root_schema or schema
    if "$ref" in schema:
        resolved = _resolve_local_ref(root, str(schema["$ref"]))
        if resolved is None:
            return [f"{path}: unresolved local reference {schema['$ref']}"]
        return validate_schema_instance(resolved, value, root_schema=root, path=path)
    errors: list[str] = []
    if "anyOf" in schema:
        branches = [
            validate_schema_instance(branch, value, root_schema=root, path=path)
            for branch in schema["anyOf"]
        ]
        if not any(not branch_errors for branch_errors in branches):
            errors.append(f"{path}: no anyOf branch matched")
    expected_type = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected_type is not None:
        allowed_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches.get(str(item), False) for item in allowed_types):
            return [f"{path}: expected {expected_type}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is outside enum")

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            errors.append(f"{path}: pattern mismatch")
        if schema.get("format") == "uuid":
            try:
                UUID(value)
            except ValueError:
                errors.append(f"{path}: invalid UUID")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: below exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: above exclusiveMaximum")
        if "multipleOf" in schema:
            multiple = float(schema["multipleOf"])
            quotient = float(value) / multiple
            if abs(quotient - round(quotient)) > 1e-9:
                errors.append(f"{path}: not a multipleOf {multiple}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: additional property {key}")
        for key, child in value.items():
            if key in properties:
                errors.extend(
                    validate_schema_instance(
                        properties[key], child, root_schema=root, path=f"{path}.{key}"
                    )
                )

    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: more than maxItems")
        if "items" in schema:
            for index, child in enumerate(value):
                errors.extend(
                    validate_schema_instance(
                        schema["items"], child, root_schema=root, path=f"{path}[{index}]"
                    )
                )
    return errors


def validate_schema_shapes(
    document_schema: dict[str, Any],
    extraction_schema: dict[str, Any],
    job_schema: dict[str, Any],
) -> list[str]:
    """Validate the stable top-level and safety shapes without external packages."""

    errors: list[str] = []
    if document_schema.get("required") != [
        "schema_version",
        "source_key",
        "document_kind",
        "extractor_config",
    ]:
        errors.append("document-ingestion required fields changed")
    if job_schema.get("required") != [
        "schema_version",
        "job_id",
        "document_id",
        "source_key",
        "settings",
        "extractor_config_hash",
        "state",
    ]:
        errors.append("analysis-job required fields changed")
    if extraction_schema.get("required") != [
        "schema_version",
        "content_sha256",
        "extractor_name",
        "extractor_version",
        "extractor_config_hash",
        "quality_rule_version",
        "pages",
        "evidence",
    ]:
        errors.append("extraction-result required fields changed")

    for name, schema in (
        ("document-ingestion", document_schema),
        ("extraction-result", extraction_schema),
        ("analysis-job", job_schema),
    ):
        if schema.get("additionalProperties") is not False:
            errors.append(f"{name} top-level additionalProperties must be false")
        if forbidden_keys(schema):
            errors.append(f"{name} schema contains a forbidden field")

    if contains_key(document_schema, "extractor_config_hash"):
        errors.append(
            "document-ingestion schema must not expose client-controlled extractor_config_hash"
        )
    if "content_sha256" in job_schema.get("properties", {}):
        errors.append("analysis-job schema must not expose pre-intake content_sha256")
    if job_schema.get("$defs", {}).get("JobState", {}).get("enum") != list(JOB_STATES):
        errors.append("analysis-job state enum changed")
    if job_schema.get("$defs", {}).get("ErrorCode", {}).get("enum") != list(ERROR_CODES):
        errors.append("analysis-job error-code enum changed")
    text_block = extraction_schema.get("$defs", {}).get("TextBlock", {})
    if text_block.get("properties", {}).get("reading_order", {}).get("minimum") != 0:
        errors.append("TextBlock reading_order must be non-negative")
    if (
        "page_number" not in text_block.get("required", [])
        or text_block.get("properties", {}).get("page_number", {}).get("minimum") != 1
    ):
        errors.append("TextBlock page_number must be required and 1-based")
    bbox = extraction_schema.get("$defs", {}).get("BoundingBox", {})
    if bbox.get("minItems") != 4 or bbox.get("maxItems") != 4:
        errors.append("BoundingBox must contain exactly four PDF coordinates")
    for name, schema in (("document-ingestion", document_schema), ("analysis-job", job_schema)):
        source_key_schema = schema.get("properties", {}).get("source_key", {})
        if source_key_schema.get("pattern") != SOURCE_KEY_PATTERN_TEXT:
            errors.append(f"{name} source_key safety pattern changed")
    page_quality = extraction_schema.get("$defs", {}).get("PageQuality", {})
    if page_quality.get("required") != [
        "rule_version",
        "classification",
        "non_whitespace_chars",
        "alphanumeric_ratio",
        "replacement_character_ratio",
        "maximum_repeated_character_run",
    ]:
        errors.append("PageQuality metrics or ordering changed")
    evidence = extraction_schema.get("$defs", {}).get("Evidence", {})
    if evidence.get("required") != [
        "document_version_id",
        "page_number",
        "content_sha256",
        "review_state",
    ]:
        errors.append("Evidence required fields changed")
    document_status = document_schema.get("$defs", {}).get("DocumentStatus", {})
    if document_status.get("required") != [
        "schema_version",
        "job_id",
        "document_id",
        "state",
    ]:
        errors.append("DocumentStatus required fields changed")
    if document_schema.get("x-familycare-safety-limits") != SAFETY_LIMITS:
        errors.append("document-ingestion safety-limit constants changed")
    return errors


def validate_examples(
    document_example: dict[str, Any],
    extraction_example: dict[str, Any],
    job_example: dict[str, Any],
) -> list[str]:
    """Validate synthetic examples and nested privacy/coordinate boundaries."""

    errors: list[str] = []
    for name, value in (
        ("document-ingestion", document_example),
        ("extraction-result", extraction_example),
        ("analysis-job", job_example),
    ):
        if forbidden_keys(value):
            errors.append(f"{name} example contains a forbidden field")
    if not is_relative_source_key(document_example.get("source_key")):
        errors.append("document-ingestion source_key must be relative")
    if not is_relative_source_key(job_example.get("source_key")):
        errors.append("analysis-job source_key must be relative")
    if not valid_uuid4(job_example.get("job_id")):
        errors.append("analysis-job job_id must be a UUIDv4")
    if not valid_uuid4(job_example.get("document_id")):
        errors.append("analysis-job document_id must be a UUIDv4")
    if not valid_sha256(job_example.get("extractor_config_hash")):
        errors.append("analysis-job extractor_config_hash must be lowercase SHA-256")
    if not valid_sha256(extraction_example.get("content_sha256")):
        errors.append("extraction-result content_sha256 must be lowercase SHA-256")
    if not valid_sha256(extraction_example.get("extractor_config_hash")):
        errors.append("extraction-result extractor_config_hash must be lowercase SHA-256")
    if extraction_example.get("quality_rule_version") != "quality-v1":
        errors.append("extraction-result quality rule must be quality-v1")
    evidence = extraction_example.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("extraction-result must contain evidence")
    else:
        for item in evidence:
            if not isinstance(item, dict) or not valid_uuid4(item.get("document_version_id")):
                errors.append("evidence document_version_id must be a UUID")
            if not isinstance(item, dict) or not valid_sha256(item.get("content_sha256")):
                errors.append("evidence content_sha256 must be lowercase SHA-256")
            if not isinstance(item, dict) or item.get("page_number", 0) < 1:
                errors.append("evidence page_number must be 1-based")
            if not isinstance(item, dict) or item.get("review_state") not in {
                "candidate",
                "confirmed",
                "rejected",
            }:
                errors.append("evidence review_state is invalid")

    pages = extraction_example.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.append("extraction-result must contain at least one page")
        return errors
    for page in pages:
        if not isinstance(page, dict) or page.get("page_number", 0) < 1:
            errors.append("extraction page numbers must be 1-based")
            continue
        quality = page.get("quality")
        if not isinstance(quality, dict) or quality.get("rule_version") != "quality-v1":
            errors.append("extraction page quality must use quality-v1")
        elif (
            not isinstance(quality.get("non_whitespace_chars"), int)
            or isinstance(quality["non_whitespace_chars"], bool)
            or quality["non_whitespace_chars"] < 0
            or not isinstance(quality.get("maximum_repeated_character_run"), int)
            or isinstance(quality["maximum_repeated_character_run"], bool)
            or quality["maximum_repeated_character_run"] < 0
            or not isinstance(quality.get("alphanumeric_ratio"), (int, float))
            or isinstance(quality["alphanumeric_ratio"], bool)
            or not 0 <= quality["alphanumeric_ratio"] <= 1
            or not isinstance(quality.get("replacement_character_ratio"), (int, float))
            or isinstance(quality["replacement_character_ratio"], bool)
            or not 0 <= quality["replacement_character_ratio"] <= 1
        ):
            errors.append("extraction page quality metrics are invalid")
        elif quality.get("classification") != classify_quality(
            non_whitespace_chars=quality["non_whitespace_chars"],
            alphanumeric_ratio=quality["alphanumeric_ratio"],
            replacement_character_ratio=quality["replacement_character_ratio"],
            maximum_repeated_character_run=quality["maximum_repeated_character_run"],
        ):
            errors.append("extraction page quality classification violates quality-v1")
        blocks = page.get("blocks", [])
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict) or block.get("reading_order", -1) < 0:
                errors.append("extraction reading_order must start at zero")
            if not _valid_bbox(block.get("bbox")):
                errors.append("extraction block bbox must be four rounded PDF points")
        tables = page.get("tables", [])
        for table in tables if isinstance(tables, list) else []:
            if not isinstance(table, dict) or not _valid_bbox(table.get("bbox")):
                errors.append("extraction table bbox must be four rounded PDF points")
            cells = table.get("cells", []) if isinstance(table, dict) else []
            for cell in cells if isinstance(cells, list) else []:
                if not isinstance(cell, dict) or not _valid_bbox(cell.get("bbox")):
                    errors.append("extraction cell bbox must be four rounded PDF points")
    return errors


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    return all(
        isinstance(item, (int, float))
        and 0 <= item <= 1_000_000
        and abs(float(item) - round(float(item), 3)) < 1e-9
        for item in value
    )


def validate_generated_outputs() -> list[str]:
    """Regenerate into a temporary directory and compare bytes with both consumers."""

    errors: list[str] = []
    if not API_OUTPUT.is_file() or not WORKER_OUTPUT.is_file():
        return ["generated document contract module is missing"]
    with TemporaryDirectory() as directory:
        temporary_root = Path(directory)
        generated_api = temporary_root / "api_generated_contracts.py"
        generated_worker = temporary_root / "worker_generated_contracts.py"
        try:
            generate(generated_api, generated_worker)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return [f"contract generation failed: {error}"]
        expected = generated_api.read_bytes()
        if generated_worker.read_bytes() != expected:
            errors.append("generated API and Worker contract modules differ")
        if API_OUTPUT.read_bytes() != expected:
            errors.append("API generated document contract is stale")
        if WORKER_OUTPUT.read_bytes() != expected:
            errors.append("Worker generated document contract is stale")
    return errors


def validate_document_contracts() -> list[str]:
    """Return all document-contract, example, generator, and policy errors."""

    paths = {
        "document_schema": SCHEMA_ROOT / "document-ingestion.v1.schema.json",
        "extraction_schema": SCHEMA_ROOT / "extraction-result.v1.schema.json",
        "job_schema": SCHEMA_ROOT / "analysis-job.v1.schema.json",
        "document_example": EXAMPLE_ROOT / "document-ingestion.v1.json",
        "extraction_example": EXAMPLE_ROOT / "extraction-result.v1.json",
        "job_example": EXAMPLE_ROOT / "analysis-job.v1.json",
    }
    errors: list[str] = []
    loaded: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        try:
            loaded[name] = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(str(error))
    if len(loaded) == len(paths):
        errors.extend(
            validate_schema_shapes(
                loaded["document_schema"],
                loaded["extraction_schema"],
                loaded["job_schema"],
            )
        )
        for name in ("document", "extraction", "job"):
            schema = loaded[f"{name}_schema"]
            example = loaded[f"{name}_example"]
            errors.extend(
                f"{name} example schema mismatch: {error}"
                for error in validate_schema_instance(schema, example)
            )
        errors.extend(
            validate_examples(
                loaded["document_example"],
                loaded["extraction_example"],
                loaded["job_example"],
            )
        )
    errors.extend(validate_generated_outputs())
    return errors


def main() -> int:
    errors = validate_document_contracts()
    if errors:
        print("\n".join(errors))
        return 1
    print("document contract checks passed (schemas, examples, generated types, safety policy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate committed FamilyCare API and worker contracts."""

from __future__ import annotations

import argparse
import json
import re
from importlib import import_module
from pathlib import Path
from typing import Any

from familycare_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "packages/contracts/openapi/familycare.v1.json"
JOB_SCHEMA_PATH = ROOT / "packages/contracts/schemas/analysis-job.v1.schema.json"
JOB_EXAMPLE_PATH = ROOT / "packages/contracts/examples/analysis-job.v1.json"
DOCUMENT_SCHEMA_PATH = ROOT / "packages/contracts/schemas/document-ingestion.v1.schema.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _load_document_contract_checker() -> Any:
    """Load the document checker from package or direct-script context."""

    try:
        return import_module("scripts.check_document_contracts")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        return import_module("check_document_contracts")


_DOCUMENT_CONTRACT_CHECKER = _load_document_contract_checker()
is_relative_source_key = _DOCUMENT_CONTRACT_CHECKER.is_relative_source_key
valid_uuid4 = _DOCUMENT_CONTRACT_CHECKER.valid_uuid4
validate_document_contracts = _DOCUMENT_CONTRACT_CHECKER.validate_document_contracts


def render_openapi() -> str:
    """Render the canonical OpenAPI document deterministically."""

    return (
        json.dumps(
            create_app(enable_synthetic_ingestion=True).openapi(),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def load_json(path: Path) -> dict[str, Any]:
    """Load a required JSON object or raise a useful validation error."""

    if not path.is_file():
        raise ValueError(f"missing contract artifact: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path.relative_to(ROOT)}")
    return value


def validate_openapi() -> list[str]:
    """Return drift errors for the committed API contract."""

    if not OPENAPI_PATH.is_file():
        return [f"missing contract artifact: {OPENAPI_PATH.relative_to(ROOT)}"]
    committed = OPENAPI_PATH.read_text(encoding="utf-8")
    generated = render_openapi()
    errors: list[str] = []
    if committed != generated:
        errors.append("OpenAPI contract drift: regenerate familycare.v1.json")
    document = json.loads(generated)
    paths = document.get("paths", {})
    expected_paths = {
        "/health/live",
        "/health/ready",
        "/api/v1/documents/analysis",
        "/api/v1/analysis-jobs/{job_id}",
    }
    if set(paths) != expected_paths:
        errors.append("OpenAPI paths must contain health and gated analysis routes only")
        return errors

    post = paths["/api/v1/documents/analysis"].get("post", {})
    status_get = paths["/api/v1/analysis-jobs/{job_id}"].get("get", {})
    if set(post.get("responses", {})) != {"202", "422", "503"}:
        errors.append("analysis POST responses must be exactly 202, 422, and 503")
    if set(status_get.get("responses", {})) != {"200", "404", "422", "503"}:
        errors.append("analysis status responses must be exactly 200, 404, 422, and 503")
    for operation in (post, status_get):
        description = str(operation.get("description", ""))
        if "synthetic-only" not in description or "not production-safe" not in description:
            errors.append("analysis routes must state their local synthetic-only safety boundary")

    schemas = document.get("components", {}).get("schemas", {})
    request_schema = schemas.get("DocumentAnalysisRequest", {})
    if set(request_schema.get("properties", {})) != {
        "schema_version",
        "source_key",
        "document_kind",
        "extractor_config",
    }:
        errors.append("analysis request exposes fields outside the v1 ingestion contract")
    if request_schema.get("additionalProperties") is not False:
        errors.append("analysis request must reject additional properties")
    request_examples = request_schema.get("examples", [])
    if (
        not isinstance(request_examples, list)
        or len(request_examples) != 1
        or not isinstance(request_examples[0], dict)
        or not str(request_examples[0].get("source_key", "")).startswith("synthetic/")
    ):
        errors.append("analysis request must have one wholly synthetic example")
    document_contract = load_json(DOCUMENT_SCHEMA_PATH)
    contract_properties = document_contract.get("properties", {})
    for field in ("schema_version", "source_key", "document_kind"):
        openapi_field = request_schema.get("properties", {}).get(field, {})
        contract_field = contract_properties.get(field, {})
        for keyword in ("const", "enum", "minLength", "maxLength", "pattern", "type"):
            if openapi_field.get(keyword) != contract_field.get(keyword):
                errors.append(f"analysis request {field} drifted from the v1 JSON Schema")
                break
    extractor_schema = schemas.get("ExtractorConfigRequest", {})
    contract_extractor = document_contract.get("$defs", {}).get("ExtractorConfig", {})
    if extractor_schema.get("additionalProperties") is not False:
        errors.append("analysis extractor config must reject additional properties")
    for field in ("profile", "quality_rule_version", "table_strategy"):
        openapi_field = extractor_schema.get("properties", {}).get(field, {})
        contract_field = contract_extractor.get("properties", {}).get(field, {})
        for keyword in ("const", "enum", "type"):
            if openapi_field.get(keyword) != contract_field.get(keyword):
                errors.append(f"analysis extractor {field} drifted from the v1 JSON Schema")
                break
    accepted_schema = schemas.get("AnalysisAcceptedResponse", {})
    accepted_fields = {
        "schema_version",
        "job_id",
        "state",
        "status_url",
    }
    if set(accepted_schema.get("properties", {})) != accepted_fields:
        errors.append("analysis accepted response exposes unexpected fields")
    if set(accepted_schema.get("required", [])) != accepted_fields:
        errors.append("analysis accepted response fields must all be required")
    status_schema = schemas.get("AnalysisJobStatusResponse", {})
    status_fields = {
        "schema_version",
        "job_id",
        "document_id",
        "state",
        "attempts",
        "error_code",
        "extraction_summary",
    }
    if set(status_schema.get("properties", {})) != status_fields:
        errors.append("analysis status response exposes unexpected fields")
    required_status_fields = status_fields - {"error_code", "extraction_summary"}
    if set(status_schema.get("required", [])) != required_status_fields:
        errors.append("analysis status identity and state fields must all be required")
    if "source_key" in json.dumps(
        [accepted_schema.get("examples", []), status_schema.get("examples", [])],
        sort_keys=True,
    ):
        errors.append("analysis response examples must not expose source keys")
    return errors


def validate_job_contract() -> list[str]:
    """Validate the versioned analyzer job schema and its synthetic example."""

    try:
        schema = load_json(JOB_SCHEMA_PATH)
        example = load_json(JOB_EXAMPLE_PATH)
    except (json.JSONDecodeError, ValueError) as error:
        return [str(error)]

    errors: list[str] = []
    required = {
        "schema_version",
        "job_id",
        "document_id",
        "source_key",
        "settings",
        "extractor_config_hash",
        "state",
    }
    if set(schema.get("required", [])) != required:
        errors.append("analysis job schema required properties are inconsistent")
    if schema.get("additionalProperties") is not False:
        errors.append("analysis job schema must reject additional properties")
    if schema.get("properties", {}).get("document_id") != {"$ref": "#/$defs/DocumentId"}:
        errors.append("analysis job document_id must reference the UUID DocumentId contract")
    if set(example) != required:
        errors.append("analysis job example keys do not match the schema")
    if example.get("schema_version") != "1":
        errors.append("analysis job schema_version must be 1")

    if not valid_uuid4(example.get("job_id")):
        errors.append("analysis job job_id must be a UUID")

    if not valid_uuid4(example.get("document_id")):
        errors.append("analysis job example document_id must be a UUIDv4")
    if not is_relative_source_key(example.get("source_key")):
        errors.append("analysis job source_key must be a relative path")
    if not SHA256_PATTERN.fullmatch(str(example.get("extractor_config_hash", ""))):
        errors.append("analysis job extractor_config_hash must be 64 lowercase hex characters")
    if example.get("state") not in {
        "queued",
        "running",
        "succeeded",
        "retryable_failed",
        "permanently_failed",
        "cancelled",
    }:
        errors.append("analysis job state is not one of the six stable states")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-openapi", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Validate contracts or regenerate only the deterministic OpenAPI artifact."""

    args = parse_args()
    if args.write_openapi:
        OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPENAPI_PATH.write_text(render_openapi(), encoding="utf-8", newline="\n")
        print(f"wrote {OPENAPI_PATH.relative_to(ROOT)}")
        return 0

    errors = [*validate_openapi(), *validate_job_contract(), *validate_document_contracts()]
    if errors:
        print("\n".join(errors))
        return 1
    print("contract checks passed (OpenAPI, analysis-job.v1, and document ingestion contracts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

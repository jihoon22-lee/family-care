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

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


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
    if committed != generated:
        return ["OpenAPI contract drift: regenerate familycare.v1.json"]
    return []


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

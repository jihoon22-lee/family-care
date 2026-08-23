#!/usr/bin/env python3
"""Validate committed FamilyCare API and worker contracts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from familycare_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "packages/contracts/openapi/familycare.v1.json"
JOB_SCHEMA_PATH = ROOT / "packages/contracts/schemas/analysis-job.v1.schema.json"
JOB_EXAMPLE_PATH = ROOT / "packages/contracts/examples/analysis-job.v1.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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
    required = {"schema_version", "job_id", "document_id", "content_sha256"}
    if set(schema.get("required", [])) != required:
        errors.append("analysis job schema required properties are inconsistent")
    if schema.get("additionalProperties") is not False:
        errors.append("analysis job schema must reject additional properties")
    if set(example) != required:
        errors.append("analysis job example keys do not match the schema")
    if example.get("schema_version") != "1":
        errors.append("analysis job schema_version must be 1")

    try:
        job_id = UUID(str(example.get("job_id")))
        if job_id.version != 4:
            errors.append("analysis job job_id must be a UUIDv4")
    except ValueError:
        errors.append("analysis job job_id must be a UUID")

    if not str(example.get("document_id", "")).startswith("synthetic-"):
        errors.append("analysis job example document_id must be synthetic")
    if not SHA256_PATTERN.fullmatch(str(example.get("content_sha256", ""))):
        errors.append("analysis job content_sha256 must be 64 lowercase hex characters")
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

    errors = [*validate_openapi(), *validate_job_contract()]
    if errors:
        print("\n".join(errors))
        return 1
    print("contract checks passed (OpenAPI and analysis-job.v1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

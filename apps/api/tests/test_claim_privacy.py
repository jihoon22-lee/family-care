"""Privacy regressions for ClaimCase storage and HTTP contracts."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import familycare_api.claims.repository as claim_repository
import familycare_api.claims.router as claim_router
import familycare_api.claims.schemas as claim_schemas
import familycare_api.claims.service as claim_service
from familycare_api.main import create_app

FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "blob",
    "document_body",
    "document_path",
    "document_text",
    "external_document_id",
    "file",
    "file_id",
    "file_path",
    "full_text",
    "image",
    "image_bytes",
    "medical_text",
    "ocr",
    "ocr_text",
    "password",
    "path",
    "pdf_path",
    "raw_text",
    "source_path",
}


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | {
            nested for child in value.values() for nested in _keys(child)
        }
    if isinstance(value, list):
        return {nested for child in value for nested in _keys(child)}
    return set()


def test_claim_openapi_has_no_file_document_body_path_or_scope_inputs() -> None:
    document = create_app(enable_synthetic_ingestion=False).openapi()
    claim_paths = {path: value for path, value in document["paths"].items() if "/claims" in path}
    claim_components = {
        name: value
        for name, value in document["components"]["schemas"].items()
        if "Claim" in name or "Checklist" in name
    }

    keys = _keys({"paths": claim_paths, "schemas": claim_components})
    assert not keys & FORBIDDEN_FIELDS
    serialized = json.dumps(claim_paths, sort_keys=True).lower()
    assert "household_space_id" not in serialized
    assert "insurer submission" not in serialized


def test_claim_runtime_modules_do_not_log_or_print_request_and_stored_values() -> None:
    sources = "\n".join(
        inspect.getsource(module)
        for module in (claim_repository, claim_router, claim_schemas, claim_service)
    )

    assert "logging." not in sources
    assert "logger." not in sources
    assert "print(" not in sources
    assert "console" not in sources.lower()


def test_claim_migration_columns_are_metadata_only() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0010_claim_workflow.py"
    ).read_text(encoding="utf-8")
    column_names = {
        match.split('"', 2)[1].lower() for match in source.splitlines() if 'sa.Column("' in match
    }

    assert not column_names & FORBIDDEN_FIELDS
    assert "receipt_number" in column_names
    assert "metadata_json" not in {"raw_note", "medical_text"}

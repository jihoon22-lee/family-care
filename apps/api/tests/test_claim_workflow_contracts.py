"""Contract tests for the transport-neutral ClaimCase workflow boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/claim-workflow.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/claim-workflow.v1.json"

FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "blob",
    "document",
    "document_id",
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
    "text",
}


def load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing contract artifact: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def load_schema_validator() -> Any:
    sys.path.insert(0, str(ROOT))
    from scripts.check_document_contracts import validate_schema_instance

    return validate_schema_instance


def walk_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        objects = [value] if value.get("type") == "object" else []
        for child in value.values():
            objects.extend(walk_objects(child))
        return objects
    if isinstance(value, list):
        objects: list[dict[str, Any]] = []
        for child in value:
            objects.extend(walk_objects(child))
        return objects
    return []


def walk_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        keys: list[tuple[str, str]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            keys.append((child_path, str(key)))
            keys.extend(walk_keys(child, child_path))
        return keys
    if isinstance(value, list):
        keys: list[tuple[str, str]] = []
        for index, child in enumerate(value):
            keys.extend(walk_keys(child, f"{path}[{index}]"))
        return keys
    return []


def test_claim_workflow_example_matches_strict_schema_and_covers_history() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "ClaimWorkflowEnvelope"
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "schema_version",
        "claim_case",
        "snapshot",
        "checklist",
        "status_events",
        "history",
    ]
    assert example["schema_version"] == "1"
    assert example["claim_case"]["status"] == "closed"
    assert example["snapshot"]["snapshot_version"] == 1
    assert example["checklist"]
    assert example["status_events"]
    assert {item["outcome"] for item in example["history"]} == {
        "paid",
        "partially_paid",
        "denied",
    }


def test_claim_workflow_schema_is_recursive_strict_and_bounded() -> None:
    schema = load_json(SCHEMA_PATH)

    objects = walk_objects(schema)
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)

    definitions = schema["$defs"]
    assert definitions["ClaimStatus"]["enum"] == [
        "preparing",
        "submitted",
        "supplementation_requested",
        "paid",
        "partially_paid",
        "denied",
        "closed",
    ]
    assert definitions["ClaimOutcome"]["enum"] == ["paid", "partially_paid", "denied"]
    assert definitions["ClaimCase"]["properties"]["allowed_transitions"]["maxItems"] == 6
    assert definitions["ChecklistItem"]["properties"]["document_kind"]["maxLength"] == 64
    assert definitions["ClaimCaseSnapshot"]["properties"]["snapshot_sha256"]["pattern"] == (
        "^[a-f0-9]{64}$"
    )
    assert schema["properties"]["checklist"]["maxItems"] == 64
    assert schema["properties"]["status_events"]["maxItems"] == 64
    assert schema["properties"]["history"]["maxItems"] == 64


def test_claim_workflow_contract_has_no_medical_file_or_raw_text_fields() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    for document in (schema, example):
        assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(document))

    checklist = example["checklist"][0]
    assert set(checklist) == {
        "id",
        "claim_case_id",
        "document_kind",
        "requirement_code",
        "required",
        "conditional",
        "prepared",
        "note_code",
        "source_rule_version_id",
        "source_evidence_id",
        "version",
        "created_at",
        "updated_at",
    }
    assert "note" not in checklist
    assert "household_space_id" not in example["claim_case"]


def test_claim_workflow_schema_rejects_invalid_fields_and_bounds() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    mutations = [
        {**example, "raw_note": "synthetic note must not cross the boundary"},
        {
            **example,
            "claim_case": {**example["claim_case"], "unexpected": True},
        },
        {
            **example,
            "claim_case": {**example["claim_case"], "status": "approved"},
        },
        {
            **example,
            "claim_case": {**example["claim_case"], "claimed_amount": "-1"},
        },
        {
            **example,
            "claim_case": {**example["claim_case"], "currency": "KR"},
        },
        {
            **example,
            "checklist": example["checklist"] * 65,
        },
        {
            **example,
            "status_events": [
                {**example["status_events"][0], "metadata": {"raw_note": "forbidden"}}
            ],
        },
        {
            **example,
            "history": [{**example["history"][0], "amount": "-0.01"}],
        },
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_claim_workflow_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_claim_workflow_contract

    assert validate_claim_workflow_contract() == []

"""Contract tests for the deterministic coverage-decision response."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/coverage-decision.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/coverage-decision.v1.json"

FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "diagnosis_text",
    "document_text",
    "file_id",
    "file_path",
    "full_text",
    "image_bytes",
    "ocr_text",
    "password",
    "pdf_bytes",
    "raw_text",
    "source_path",
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
        objects = []
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
        keys = []
        for index, child in enumerate(value):
            keys.extend(walk_keys(child, f"{path}[{index}]"))
        return keys
    return []


def test_decision_example_matches_strict_schema() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert example["schema_version"] == "1"
    assert example["candidates"]
    assert example["evaluations"]


def test_decision_schema_is_recursive_strict_and_bounded() -> None:
    schema = load_json(SCHEMA_PATH)

    objects = walk_objects(schema)
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)
    assert schema["properties"]["candidates"]["maxItems"] == 64
    assert schema["properties"]["evaluations"]["maxItems"] == 256


def test_decision_evaluation_requires_tri_state_rule_version_evidence_and_engine() -> None:
    schema = load_json(SCHEMA_PATH)
    evaluation = schema["$defs"]["RuleEvaluation"]

    assert set(evaluation["required"]) >= {
        "rule_version_id",
        "result",
        "reason_code",
        "evidence",
        "engine_version",
    }
    assert schema["$defs"]["TriState"]["enum"] == ["MATCH", "NO_MATCH", "UNKNOWN"]
    assert "amount" not in evaluation["properties"]
    assert "amount" not in schema["properties"]


def test_decision_contract_has_no_private_payload_fields_or_amounts() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    for document in (schema, example):
        assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(document))
        assert all(key.lower() != "amount" for _, key in walk_keys(document))
    assert all(
        isinstance(item.get("run_id"), str)
        and item["run_id"].startswith("00000000-0000-4000-8000-")
        for item in [example]
    )


def test_decision_example_covers_unknown_and_deterministic_mismatch() -> None:
    example = load_json(EXAMPLE_PATH)
    results = {item["result"] for item in example["evaluations"]}
    candidate_results = {item["aggregate_result"] for item in example["candidates"]}

    assert "UNKNOWN" in results
    assert "NO_MATCH" in results
    assert results <= {"MATCH", "NO_MATCH", "UNKNOWN"}
    assert candidate_results <= {"MATCH", "NO_MATCH", "UNKNOWN"}


def test_decision_schema_rejects_unknown_tri_state_rule_and_private_fields() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    mutations = [
        {**example, "amount": 100},
        {
            **example,
            "evaluations": [
                {**example["evaluations"][0], "result": "PENDING"},
            ],
        },
        {
            **example,
            "evaluations": [
                {**example["evaluations"][0], "rule_version_id": "synthetic-rule"},
            ],
        },
        {
            **example,
            "evaluations": [
                {**example["evaluations"][0], "evidence": []},
            ],
        },
        {
            **example,
            "candidates": [
                {**example["candidates"][0], "document_text": "not allowed"},
            ],
        },
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_decision_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_decision_contract

    assert validate_decision_contract() == []

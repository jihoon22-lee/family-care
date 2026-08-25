"""Contract tests for the non-authoritative event-structuring boundary."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/medical-event-structuring.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/medical-event-structuring.v1.json"

FORBIDDEN_FIELDS = {
    "absolute_path",
    "amount",
    "decision",
    "document",
    "eligible",
    "payment",
    "password",
    "path",
    "provider_payload",
    "provider_response",
    "raw_payload",
    "raw_provider_response",
    "result",
    "tri_state",
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


def test_event_structuring_example_matches_strict_request_result_and_job_schema() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "MedicalEventStructuringEnvelope"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "request", "response", "job"}
    assert example["schema_version"] == "1"
    assert example["request"]["mode"] == "pre_visit"
    assert example["response"]["facts"]
    assert example["response"]["questions"]
    assert example["job"]["state"] == "succeeded"


def test_event_structuring_schema_is_recursive_strict_and_bounded() -> None:
    schema = load_json(SCHEMA_PATH)

    objects = walk_objects(schema)
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)

    definitions = schema["$defs"]
    assert definitions["FactFieldId"]["enum"] == [
        "event_date",
        "visit_date",
        "condition_class",
        "diagnosis_label",
        "treatment_kind",
        "admission",
        "outpatient",
        "pharmacy",
    ]
    assert definitions["FactSource"]["enum"] == ["user", "ai", "system"]
    assert definitions["FactState"]["enum"] == [
        "confirmed",
        "ambiguous",
        "missing",
        "conflict",
    ]
    assert definitions["StructuringJobState"]["enum"] == [
        "queued",
        "running",
        "succeeded",
        "retryable_failed",
        "permanently_failed",
        "cancelled",
    ]
    assert schema["properties"]["request"]["$ref"] == "#/$defs/StructuringRequest"
    assert definitions["StructuringRequest"]["properties"]["situation"]["maxLength"] == 2000
    assert definitions["StructuringResult"]["properties"]["facts"]["maxItems"] == 32
    assert definitions["StructuringResult"]["properties"]["questions"]["maxItems"] == 16
    assert definitions["StructuringJob"]["properties"]["attempts"]["maximum"] == 10


def test_event_structuring_contract_has_no_decision_money_document_or_provider_payload() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    for document in (schema, example):
        assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(document))

    request = example["request"]
    assert set(request) == {
        "schema_version",
        "mode",
        "situation",
        "event_date",
        "visit_date",
    }
    assert "user_id" not in request
    assert "family_member_id" not in request
    assert "provider_request_id" not in example["response"]


def test_event_structuring_schema_rejects_unknown_fields_states_and_bounds() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    fact = example["response"]["facts"][0]
    mutations = [
        {**example, "decision": "MATCH"},
        {**example, "result": {"state": "MATCH"}},
        {**example, "request": {**example["request"], "source_path": "synthetic/path"}},
        {**example, "request": {**example["request"], "document_text": "synthetic"}},
        {**example, "request": {**example["request"], "user_id": "synthetic-user"}},
        {
            **example,
            "response": {
                **example["response"],
                "facts": [{**fact, "unexpected": True}],
            },
        },
        {
            **example,
            "response": {**example["response"], "facts": [{**fact, "state": "guessed"}]},
        },
        {
            **example,
            "response": {**example["response"], "facts": [{**fact, "source": "provider"}]},
        },
        {
            **example,
            "response": {
                **example["response"],
                "facts": [{**fact, "field_id": "decision"}],
            },
        },
        {
            **example,
            "request": {**example["request"], "situation": "x" * 2001},
        },
        {
            **example,
            "response": {
                **example["response"],
                "facts": example["response"]["facts"] * 33,
            },
        },
        {
            **example,
            "job": {**example["job"], "state": "MATCH"},
        },
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_event_structuring_schema_accepts_missing_and_conflicting_facts() -> None:
    schema = load_json(SCHEMA_PATH)
    example = deepcopy(load_json(EXAMPLE_PATH))
    validate_schema_instance = load_schema_validator()

    facts = example["response"]["facts"]
    facts[0]["state"] = "ambiguous"
    facts[0]["value"] = None
    facts[1]["state"] = "conflict"
    facts[1]["source"] = "user"
    facts[1]["value"] = "synthetic corrected value"

    assert not validate_schema_instance(schema, example)


def test_event_structuring_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_event_structuring_contract

    assert validate_event_structuring_contract() == []

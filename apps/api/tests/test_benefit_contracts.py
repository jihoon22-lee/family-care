"""Contract tests for transport-neutral benefit calculation results."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/benefit-calculation.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/benefit-calculation.v1.json"

FORBIDDEN_FIELDS = {
    "absolute_path",
    "diagnosis",
    "diagnosis_text",
    "file",
    "file_id",
    "file_path",
    "full_text",
    "medical_text",
    "note",
    "note_code",
    "path",
    "raw_note",
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


def test_benefit_examples_match_strict_schema_and_cover_fixed_and_partial_indemnity() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert example["schema_version"] == "1"

    calculations = example["calculations"]
    assert len(calculations) == 2
    assert {item["kind"] for item in calculations} == {"fixed", "indemnity"}
    assert {item["status"] for item in calculations} == {"computed", "partial"}

    fixed = next(item for item in calculations if item["kind"] == "fixed")
    indemnity = next(item for item in calculations if item["kind"] == "indemnity")
    assert fixed["schema_version"] == "1"
    assert indemnity["schema_version"] == "1"
    assert fixed["steps"]
    assert fixed["confirmed"]["amount"] == "66667"
    assert indemnity["status"] == "partial"
    assert indemnity["confirmed"]["amount"] == "64000"
    assert indemnity["additional"]["amount"] == "12500"
    assert indemnity["excluded"]["amount"] == "3000"
    assert indemnity["hold_reason_codes"]


def test_benefit_schema_is_recursive_strict_and_bounded() -> None:
    schema = load_json(SCHEMA_PATH)

    objects = walk_objects(schema)
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)

    result = schema["$defs"]["BenefitCalculation"]
    assert result["properties"]["steps"]["maxItems"] == 64
    assert result["properties"]["hold_reason_codes"]["maxItems"] == 16
    assert result["properties"]["steps"]["items"]["$ref"] == "#/$defs/CalculationStep"
    assert result["properties"]["confirmed"]["$ref"] == "#/$defs/MoneyOrNull"
    assert schema["$defs"]["DecimalString"]["type"] == "string"


def test_benefit_contract_has_no_private_or_raw_note_fields() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    for document in (schema, example):
        assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(document))


def test_benefit_schema_rejects_unknown_fields_statuses_numeric_amounts_and_unbounded_lists() -> (
    None
):
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    fixed = example["calculations"][0]
    mutations = [
        {**example, "raw_note": "not allowed"},
        {
            **example,
            "calculations": [{**fixed, "unexpected": True}, example["calculations"][1]],
        },
        {
            **example,
            "calculations": [
                {**fixed, "status": "pending"},
                example["calculations"][1],
            ],
        },
        {
            **example,
            "calculations": [
                {
                    **fixed,
                    "confirmed": {**fixed["confirmed"], "amount": 66667},
                },
                example["calculations"][1],
            ],
        },
        {
            **example,
            "calculations": [
                {
                    **fixed,
                    "confirmed": {**fixed["confirmed"], "amount": "-1"},
                },
                example["calculations"][1],
            ],
        },
        {
            **example,
            "calculations": [
                {
                    **fixed,
                    "confirmed": {**fixed["confirmed"], "amount": "1e3"},
                },
                example["calculations"][1],
            ],
        },
        {
            **example,
            "calculations": [
                {
                    **fixed,
                    "steps": fixed["steps"] * 65,
                },
                example["calculations"][1],
            ],
        },
        {
            **example,
            "calculations": [
                {
                    **fixed,
                    "hold_reason_codes": ["MISSING_INPUT"] * 17,
                },
                example["calculations"][1],
            ],
        },
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_benefit_schema_accepts_unknown_result_with_bounded_hold_reason() -> None:
    schema = load_json(SCHEMA_PATH)
    example = deepcopy(load_json(EXAMPLE_PATH))
    validate_schema_instance = load_schema_validator()

    unknown = example["calculations"][0]
    unknown["status"] = "unknown"
    unknown["confirmed"] = None
    unknown["steps"] = []
    unknown["hold_reason_codes"] = ["MISSING_FIXED_INPUT"]

    assert not validate_schema_instance(schema, example)


def test_benefit_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_benefit_calculation_contract

    assert validate_benefit_calculation_contract() == []

"""Contract tests for the deterministic coverage-decision response."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/coverage-decision.v2.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/coverage-decision.v2.json"
HISTORICAL_SCHEMA_PATH = CONTRACT_ROOT / "schemas/coverage-decision.v1.schema.json"
HISTORICAL_EXAMPLE_PATH = CONTRACT_ROOT / "examples/coverage-decision.v1.json"

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


def test_decision_v2_example_matches_strict_schema_and_v1_is_retained() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert example["schema_version"] == "2"
    assert example["candidates"]
    assert example["evaluations"]
    assert load_json(HISTORICAL_EXAMPLE_PATH)["schema_version"] == "1"
    assert load_json(HISTORICAL_SCHEMA_PATH)["properties"]["schema_version"]["const"] == "1"


def test_decision_schema_is_recursive_strict_and_bounded() -> None:
    schema = load_json(SCHEMA_PATH)

    objects = walk_objects(schema)
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)
    assert schema["properties"]["candidates"]["maxItems"] == 128
    assert schema["properties"]["evaluations"]["maxItems"] == 512
    assert (
        schema["$defs"]["AnalysisAssistanceResponse"]["properties"]["recommendations"]["maxItems"]
        == 12
    )


def test_decision_evaluations_have_discriminated_lineage_and_exact_citations() -> None:
    schema = load_json(SCHEMA_PATH)
    operational = schema["$defs"]["OperationalEvaluationResponse"]
    private = schema["$defs"]["PrivateKnowledgeEvaluationResponse"]

    assert set(operational["required"]) >= {"source", "result", "citations", "engine_version"}
    assert set(private["required"]) >= {"source", "result", "citations", "engine_version"}
    assert operational["properties"]["source"]["$ref"].endswith(
        "/OperationalEvaluationSourceResponse"
    )
    assert private["properties"]["source"]["$ref"].endswith(
        "/PrivateKnowledgeEvaluationSourceResponse"
    )
    assert operational["properties"]["result"]["enum"] == [
        "MATCH",
        "NO_MATCH",
        "UNKNOWN",
    ]


def test_decision_contract_has_no_private_payload_fields_and_uses_decimal_strings() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    for document in (schema, example):
        assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(document))
    subtotal = example["conditional_fixed_subtotals"][0]
    assert isinstance(subtotal["amount"], str)
    assert subtotal["amount"] == "300000"
    assert example["indemnity_summary"]["status"] == "UNKNOWN"
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
    source_kinds = {item["source"]["kind"] for item in example["candidates"]}
    assert source_kinds == {"OPERATIONAL_RIDER", "PRIVATE_KNOWLEDGE_COVERAGE"}
    private = next(
        item
        for item in example["candidates"]
        if item["source"]["kind"] == "PRIVATE_KNOWLEDGE_COVERAGE"
    )
    assert private["claim_start_ready"] is False


def test_decision_schema_rejects_unknown_tri_state_rule_and_private_fields() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    mutations = [
        {**example, "payable_amount": "300000"},
        {
            **example,
            "evaluations": [
                {**example["evaluations"][0], "result": "PENDING"},
            ],
        },
        {
            **example,
            "conditional_fixed_subtotals": [
                {**example["conditional_fixed_subtotals"][0], "amount": 300000},
            ],
        },
        {
            **example,
            "evaluations": [
                {**example["evaluations"][0], "citations": []},
            ],
        },
        {
            **example,
            "candidates": [
                {**example["candidates"][0], "document_text": "not allowed"},
            ],
        },
        {
            **example,
            "assistance": {
                **example["assistance"],
                "eligibility_result": "MATCH",
            },
        },
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_decision_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_decision_contract

    assert validate_decision_contract() == []


def test_decision_v2_model_rejects_cross_field_authority_and_inconsistent_counts() -> None:
    from familycare_api.decisions.schemas import CoverageDecisionResponse

    example = load_json(EXAMPLE_PATH)
    mutations: list[dict[str, Any]] = []

    claim_ready = copy.deepcopy(example)
    claim_ready["candidates"][2]["claim_start_ready"] = True
    mutations.append(claim_ready)

    missing_calculated_amount = copy.deepcopy(example)
    missing_calculated_amount["candidates"][2]["calculation"]["conditional_amount"] = None
    mutations.append(missing_calculated_amount)

    duplicate_rank = copy.deepcopy(example)
    duplicate_rank["assistance"]["recommendations"].append(
        copy.deepcopy(duplicate_rank["assistance"]["recommendations"][0])
    )
    mutations.append(duplicate_rank)

    invalid_catalog = copy.deepcopy(example)
    invalid_catalog["catalog_coverage"]["blocked_coverage_count"] = 1
    mutations.append(invalid_catalog)

    for mutation in mutations:
        with pytest.raises(ValidationError):
            CoverageDecisionResponse.model_validate(mutation)

"""Contract tests for the transport-neutral Clause search response."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/clause-search.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/clause-search.v1.json"

FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "document_text",
    "full_text",
    "household_space_id",
    "password",
    "policy_number",
    "raw_pdf",
    "raw_query",
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
        keys = []
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


def test_clause_search_example_matches_the_strict_schema() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"] == {"const": "1", "type": "string"}
    assert example["schema_version"] == "1"
    assert example["normalization_version"] == "unicode-nfc-v1"
    assert example["query_matched_count"] >= len(example["hits"])


def test_clause_search_objects_are_strict_and_response_is_bounded() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    assert walk_objects(schema)
    assert all(item.get("additionalProperties") is False for item in walk_objects(schema))
    assert schema["properties"]["hits"]["maxItems"] == 50

    hit = example["hits"][0]
    assert set(hit) == {
        "clause_id",
        "label",
        "excerpt",
        "terms_edition_id",
        "physical_page_start",
        "physical_page_end",
        "evidence",
        "normalization_version",
        "relevance",
    }
    assert 0 <= hit["relevance"] <= 1
    assert 1 <= hit["physical_page_start"] <= hit["physical_page_end"]
    assert len(hit["excerpt"]) <= 320
    assert hit["evidence"]
    assert all(item["page_number"] >= 1 for item in hit["evidence"])
    assert all(len(item["content_sha256"]) == 64 for item in hit["evidence"])


def test_clause_search_schema_and_example_have_no_private_content_boundary() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(schema))
    assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(example))
    assert "Sample Terms" in example["hits"][0]["label"]
    assert "synthetic" in example["hits"][0]["excerpt"].lower()


def test_clause_search_schema_rejects_unknown_fields_and_invalid_bounds() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    mutations = [
        {**example, "raw_query": "synthetic search"},
        {**example, "hits": [{**example["hits"][0], "full_text": "not allowed"}]},
        {
            **example,
            "hits": [
                {
                    **example["hits"][0],
                    "evidence": [{**example["hits"][0]["evidence"][0], "page_number": 0}],
                }
            ],
        },
        {
            **example,
            "hits": [{**example["hits"][0], "excerpt": "x" * 321}],
        },
        {**example, "schema_version": "2"},
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_clause_search_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_clause_search_contract

    assert validate_clause_search_contract() == []

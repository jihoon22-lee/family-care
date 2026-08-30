"""Contract and privacy checks for the private-knowledge detail response."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/private-knowledge.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/private-knowledge.v1.json"

FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "content_sha256",
    "document_version_id",
    "evidence_id",
    "family_member_id",
    "household_space_id",
    "package_digest",
    "password",
    "policy_contract_id",
    "policy_number",
    "raw_pdf",
    "rider_id",
    "source_alias",
    "source_path",
    "source_record",
    "source_record_digest_sha256",
    "source_text_sha256",
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


def test_private_knowledge_example_matches_strict_versioned_schema() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, example)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "1"
    assert example["schema_version"] == "1"


def test_private_knowledge_contract_keeps_decisions_independent_and_bounded() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    assert walk_objects(schema)
    assert all(item.get("additionalProperties") is False for item in walk_objects(schema))
    assert schema["properties"]["coverages"]["maxItems"] == 256
    assert schema["properties"]["terms_assignments"]["maxItems"] == 8
    assert schema["properties"]["coverage_mappings"]["maxItems"] == 256
    assert schema["properties"]["terms_sections"]["maxItems"] == 50
    definitions = schema["$defs"]
    assert (
        definitions["KnowledgeTermsAssignmentResponse"]["properties"]["reason_codes"]["items"][
            "maxLength"
        ]
        == 240
    )
    assert (
        definitions["KnowledgeFactConditionsResponse"]["properties"]["details_ko"]["items"][
            "maxLength"
        ]
        == 8_000
    )
    assert (
        definitions["KnowledgeTermsSectionResponse"]["properties"]["warnings"]["items"]["maxLength"]
        == 240
    )

    contract = example["contract"]
    assert contract["certificate_decision"] == "MATCH"
    assert contract["document_identity_decision"] == "MATCH"
    assert contract["edition_applicability_decision"] == "UNKNOWN"
    assert contract["terms_overall_decision"] == "UNKNOWN"
    mapping = example["coverage_mappings"][0]
    assert mapping["enrollment_decision"] == "MATCH"
    assert mapping["section_mapping_decision"] == "MATCH"
    assert mapping["overall_decision"] == "UNKNOWN"
    assert mapping["executable"] is False
    assert example["terms_sections"][0]["facts"][0]["executable"] is False
    assert example["next_section_cursor"] is None
    assert "source_document_ref" in example["terms_sections"][0]["facts"][0]["citations"][0]


def test_private_knowledge_contract_excludes_private_source_and_binding_fields() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    schema_keys = {key.lower() for _, key in walk_keys(schema)}
    example_keys = {key.lower() for _, key in walk_keys(example)}
    assert not (schema_keys & FORBIDDEN_FIELDS)
    assert not (example_keys & FORBIDDEN_FIELDS)
    serialized = json.dumps(example, sort_keys=True).lower()
    assert "/mnt/" not in serialized
    assert "synthetic" in serialized


def test_private_knowledge_schema_rejects_unsafe_or_unbounded_mutations() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)
    validate_schema_instance = load_schema_validator()

    mutations = []
    executable_fact = deepcopy(example)
    executable_fact["terms_sections"][0]["facts"][0]["executable"] = True
    mutations.append(executable_fact)
    missing_independent_decision = deepcopy(example)
    del missing_independent_decision["coverage_mappings"][0]["edition_applicability_decision"]
    mutations.append(missing_independent_decision)
    private_binding = deepcopy(example)
    private_binding["contract"]["policy_contract_id"] = "00000000-0000-4000-8000-000000000001"
    mutations.append(private_binding)
    too_many_assignments = deepcopy(example)
    too_many_assignments["terms_assignments"] = [example["terms_assignments"][0]] * 9
    mutations.append(too_many_assignments)
    wrong_version = {**example, "schema_version": "2"}
    mutations.append(wrong_version)

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_private_knowledge_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_private_knowledge_contract

    assert validate_private_knowledge_contract() == []

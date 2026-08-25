"""Contract tests for the transport-neutral ClaimCase workflow boundary."""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from familycare_api.claims.snapshot import build_claim_snapshot

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


def _synthetic_persistence_values() -> dict[str, Any]:
    """Build the same persisted snapshot shape used by ClaimRepository."""

    evidence_id = UUID("00000000-0000-4000-8000-000000001070")
    rule_id = UUID("00000000-0000-4000-8000-000000001060")
    rider_id = UUID("00000000-0000-4000-8000-000000001040")
    evaluation = {
        "id": UUID("00000000-0000-4000-8000-000000001050"),
        "rider_id": rider_id,
        "rule_version_id": rule_id,
        "result": "MATCH",
        "required": True,
        "reason_code": "RULE_MATCH",
        "fact_paths": ("MedicalEvent.event_date",),
        "missing_fields": (),
        "conflicting_fields": (),
        "evidence_ids": (evidence_id,),
        "evaluator_version": "synthetic-decision-engine-v1",
    }
    result = {
        "run_id": UUID("00000000-0000-4000-8000-000000001020"),
        "medical_event_id": UUID("00000000-0000-4000-8000-000000001002"),
        "event_version": 2,
        "engine_version": "synthetic-decision-engine-v1",
        "rule_set_version": "synthetic-rule-set-v1",
        "policy_snapshot_at": datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
        "stale": False,
        "candidates": [
            {
                "id": UUID("00000000-0000-4000-8000-000000001030"),
                "rider_id": rider_id,
                "decision_run_id": UUID("00000000-0000-4000-8000-000000001020"),
                "rider_type": "fixed",
                "rider_label": "Sample Rider",
                "aggregate_result": "MATCH",
                "version": 2,
                "required_match_count": 1,
                "required_unknown_count": 0,
                "required_no_match_count": 0,
                "hold_reason_codes": (),
                "evaluations": [evaluation],
            }
        ],
        "evaluations": [evaluation],
    }
    policy = {
        "policy_id": UUID("00000000-0000-4000-8000-000000001004"),
        "rider_id": rider_id,
        "effective_status": "active",
        "rider_type": "fixed",
        "rider_label": "Sample Rider",
        "contract_start": date(2020, 1, 1),
        "rider_status": "active",
        "insured_amount": Decimal("1000000"),
        "currency": "KRW",
        "renewable": True,
        "status_checked_at": datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
        "evidence_ids": (evidence_id,),
    }
    rule = {
        "id": rule_id,
        "coverage_rule_id": UUID("00000000-0000-4000-8000-000000001061"),
        "candidate_version_id": UUID("00000000-0000-4000-8000-000000001062"),
        "version_number": 3,
        "schema_version": "coverage-rule-v1",
        "rule_kind": "fixed_amount",
        "required": True,
        "input_field_paths": ("MedicalEvent.event_date",),
        "result_reason_code": "RULE_MATCH",
        "review_state": "USER_CONFIRMED",
        "executable": True,
        "generator_version": "synthetic-generator-v1",
        "verifier_version": "synthetic-verifier-v1",
        "published_at": datetime(2026, 8, 25, 8, 30, tzinfo=UTC),
        "rule_document": {
            "schema_version": "coverage-rule-v1",
            "rule_kind": "fixed_amount",
            "required": True,
            "input_field_paths": ["MedicalEvent.event_date"],
            "calculation": {
                "op": "multiply",
                "args": [
                    {"field": "Rider.insured_amount"},
                    {"value": 0.1},
                ],
            },
            "result_reason_code": "RULE_MATCH",
            "evidence_ids": [evidence_id],
        },
        "evidence": ({"evidence_id": evidence_id},),
    }
    snapshot = build_claim_snapshot(
        result,
        {
            "calculation_id": UUID("00000000-0000-4000-8000-000000001100"),
            "claim_candidate_id": UUID("00000000-0000-4000-8000-000000001030"),
            "rule_version_id": rule_id,
            "kind": "fixed",
            "status": "computed",
            "applied_rate": Decimal("0.8"),
            "engine_version": "synthetic-calculation-v1",
            "confirmed": {"amount": Decimal("120000"), "currency": "KRW"},
            "additional": None,
            "excluded": None,
            "hold_reason_codes": (),
            "evidence_ids": (evidence_id,),
            "version": 2,
        },
        policy_snapshot=(policy,),
        rule_versions=(rule,),
        evidence=(
            {
                "evidence_id": evidence_id,
                "document_version_id": UUID("00000000-0000-4000-8000-000000001080"),
                "extraction_id": UUID("00000000-0000-4000-8000-000000001090"),
                "content_sha256": "a" * 64,
                "physical_page": 3,
                "review_state": "USER_CONFIRMED",
            },
        ),
    )
    return snapshot.persistence_values()


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
    assert "schema_version" in definitions["ClaimCase"]["required"]
    assert "deleted" in definitions["ClaimCase"]["required"]
    assert "deleted_at" not in definitions["ClaimCase"]["properties"]
    assert definitions["CandidateSnapshot"]["properties"]["schema_version"]["const"] == (
        "claim-candidate-snapshot-v1"
    )
    assert "id" in definitions["CandidateSnapshotItem"]["properties"]
    assert "candidate_id" not in definitions["CandidateSnapshotItem"]["properties"]
    assert "versions" in definitions["RuleSnapshot"]["properties"]
    assert "evaluations" not in definitions["RuleSnapshot"]["properties"]
    assert definitions["PolicySnapshot"]["properties"]["schema_version"]["const"] == (
        "claim-policy-snapshot-v1"
    )
    assert "snapshots" in definitions["PolicySnapshot"]["properties"]
    assert "changed_fields" in definitions["TransitionMetadata"]["properties"]
    assert definitions["ClaimTransitionRequest"]["properties"]["metadata"]["$ref"] == (
        "#/$defs/ClaimTransitionMetadata"
    )
    assert definitions["ClaimErrorCode"]["enum"] == [
        "AUTHENTICATION_REQUIRED",
        "CLAIM_NOT_FOUND",
        "CLAIM_CHECKLIST_ITEM_NOT_FOUND",
        "CLAIM_INVALID",
        "INVALID_CLAIM_TRANSITION",
        "INVALID_REQUEST",
        "RESOURCE_LIMIT_EXCEEDED",
        "VERSION_CONFLICT",
    ]
    assert definitions["ClaimHistory"]["properties"]["rider_id"]["$ref"] == "#/$defs/Uuid"
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
        {
            **example,
            "history": [{**example["history"][0], "outcome": "paid", "counted_occurrence": False}],
        },
        {
            **example,
            "history": [{**example["history"][0], "rider_id": None}],
        },
        {
            **example,
            "history": [
                {**example["history"][-1], "outcome": "denied", "counted_occurrence": True}
            ],
        },
    ]

    for mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_claim_workflow_example_snapshot_matches_persistence_shape() -> None:
    example = load_json(EXAMPLE_PATH)
    persisted = _synthetic_persistence_values()

    for section in (
        "candidate_snapshot",
        "rule_snapshot",
        "policy_snapshot",
        "evidence_snapshot",
        "calculation_snapshot",
    ):
        example_section = example["snapshot"][section]
        persisted_section = persisted[section]
        assert set(example_section) == set(persisted_section)
        assert example_section["schema_version"] == persisted_section["schema_version"]

    assert set(example["snapshot"]["candidate_snapshot"]["candidates"][0]) == set(
        persisted["candidate_snapshot"]["candidates"][0]
    )
    assert set(example["snapshot"]["rule_snapshot"]["versions"][0]) == set(
        persisted["rule_snapshot"]["versions"][0]
    )
    assert set(example["snapshot"]["policy_snapshot"]["snapshots"][0]) == set(
        persisted["policy_snapshot"]["snapshots"][0]
    )
    assert set(example["snapshot"]["evidence_snapshot"]["evidence"][0]) == set(
        persisted["evidence_snapshot"]["evidence"][0]
    )
    assert set(example["snapshot"]["calculation_snapshot"]["calculations"][0]) == set(
        persisted["calculation_snapshot"]["calculations"][0]
    )
    assert set(example["snapshot"]["candidate_snapshot"]["candidates"][0]["evaluations"][0]) == set(
        persisted["candidate_snapshot"]["candidates"][0]["evaluations"][0]
    )


def test_claim_workflow_checker_reports_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_claim_workflow_contract

    assert validate_claim_workflow_contract() == []

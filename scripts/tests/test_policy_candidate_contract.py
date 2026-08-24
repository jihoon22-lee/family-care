"""Contract tests for the policy candidate review boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/policy-candidate.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/policy-candidate.v1.json"
GENERATED_PATH = ROOT / "apps/web/src/api/generated.ts"
GENERATED_BUSINESS_PATH = ROOT / "apps/api/src/familycare_api/contracts/generated_business.py"


def load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing contract artifact: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def load_schema_validator() -> Any:
    sys.path.insert(0, str(ROOT))
    from scripts.check_document_contracts import validate_schema_instance

    return validate_schema_instance


def test_candidate_example_is_versioned_and_evidence_is_bounded() -> None:
    candidate = load_json(EXAMPLE_PATH)
    schema = load_json(SCHEMA_PATH)
    validate_schema_instance = load_schema_validator()

    assert not validate_schema_instance(schema, candidate)
    assert candidate["schema_version"] == "1"
    assert len(candidate["candidates"]) == 2
    verified = candidate["candidates"][0]
    assert verified["status"] == "AI_VERIFIED"
    assert verified["candidate_kind"] == "rider"
    assert verified["evidence"][0]["page"] == 1
    assert len(verified["evidence"][0]["bounded_excerpt"]) <= 240
    assert "source_path" not in candidate
    assert "policy_number" not in candidate


def test_candidate_schema_keeps_every_object_strict_and_terms_only_unpublished() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    def walk(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            found = [value] if value.get("type") == "object" else []
            for child in value.values():
                found.extend(walk(child))
            return found
        if isinstance(value, list):
            found: list[dict[str, Any]] = []
            for child in value:
                found.extend(walk(child))
            return found
        return []

    assert walk(schema)
    assert all(item.get("additionalProperties") is False for item in walk(schema))
    terms_only = example["candidates"][1]
    assert terms_only["status"] == "NEEDS_REVIEW"
    assert {issue["code"] for issue in terms_only["issues"]} == {"TERMS_ONLY_RIDER"}


def test_web_generated_types_are_checked_in_and_not_stale() -> None:
    generated = GENERATED_PATH
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")
    assert text.startswith(
        "// GENERATED FILE: do not edit; source packages/contracts/openapi/familycare.v1.json"
    )
    assert "PolicyReviewItem" in text
    assert "CandidateCorrectionRequest" in text
    assert "VERSION_CONFLICT" in text
    assert "bbox: [number, number, number, number] | null;" in text
    assert "Array<unknown>" not in text


def test_candidate_checker_and_web_generator_report_clean_artifacts() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_policy_candidate_contract import validate_policy_candidate_contract
    from scripts.generate_web_contract_types import render_module

    assert validate_policy_candidate_contract() == []
    assert GENERATED_PATH.read_text(encoding="utf-8") == render_module()


def test_api_business_types_include_the_candidate_contract() -> None:
    text = GENERATED_BUSINESS_PATH.read_text(encoding="utf-8")

    assert "CandidateStatus = Literal[" in text
    assert "PolicyCandidateFieldId = Literal[" in text
    assert "class PolicyReviewItem(TypedDict):" in text
    assert "class CandidateCorrectionRequest(TypedDict):" in text

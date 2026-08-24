#!/usr/bin/env python3
"""Check the policy candidate JSON Schema, synthetic examples, and privacy boundary."""

from __future__ import annotations

import argparse
import json
import math
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/policy-candidate.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/policy-candidate.v1.json"

FORBIDDEN_FIELDS = {
    "source_path",
    "absolute_path",
    "policy_number",
    "raw_pdf",
    "password",
    "archive_key",
    "household_space_id",
    "prompt",
    "raw_provider_response",
}
CANDIDATE_STATUSES = ["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED", "rejected"]
CANDIDATE_KINDS = [
    "policy_contract",
    "policy_party",
    "rider",
    "rider_clause",
    "coverage_rule",
]
FIELD_IDS = [
    "insurer",
    "product_name",
    "contract_start",
    "contract_end",
    "policy_status",
    "rider_name",
    "rider_key",
    "benefit_type",
    "sum_assured",
    "currency",
    "coverage_start",
    "coverage_end",
    "renewable",
    "rider_status",
    "rider_id",
    "terms_edition_id",
    "clause_id",
    "link_review_state",
    "rule_kind",
    "rule_operator",
    "fact_field",
    "unit",
    "decimal_boundary",
    "date_boundary",
    "required",
]
ISSUE_CODES = [
    "MISSING_EVIDENCE",
    "CONFLICTING_EVIDENCE",
    "TERMS_ONLY_RIDER",
    "UNSUPPORTED_STRUCTURE",
    "LOW_CONFIDENCE",
    "INVALID_UNIT",
    "INVALID_DATE",
    "WRONG_EDITION",
    "STALE_EVIDENCE",
    "UNSUPPORTED_DSL",
    "COMMON_SPECIAL_TERMS_CONFLICT",
]
ERROR_CODES = [
    "VERSION_CONFLICT",
    "REVIEW_ITEM_NOT_FOUND",
    "INVALID_CANDIDATE_CORRECTION",
]


def _load_schema_validator() -> Any:
    """Load the repository's standard-library JSON Schema subset validator."""

    try:
        module = import_module("scripts.check_document_contracts")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("check_document_contracts")
    return module.validate_schema_instance


validate_schema_instance = _load_schema_validator()


def load_json(path: Path) -> dict[str, Any]:
    """Load a required JSON object with a repository-relative error."""

    if not path.is_file():
        raise ValueError(f"missing contract artifact: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path.relative_to(ROOT)}")
    return value


def forbidden_keys(value: Any, path: str = "$") -> list[str]:
    """Find fields that must never cross the candidate contract boundary."""

    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_FIELDS:
                errors.append(child_path)
            errors.extend(forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(forbidden_keys(child, f"{path}[{index}]"))
    return errors


def _walk_objects(value: Any) -> list[dict[str, Any]]:
    """Return every JSON Schema object declaration below a schema document."""

    if isinstance(value, dict):
        found = [value] if value.get("type") == "object" else []
        for child in value.values():
            found.extend(_walk_objects(child))
        return found
    if isinstance(value, list):
        nested: list[dict[str, Any]] = []
        for child in value:
            nested.extend(_walk_objects(child))
        return nested
    return []


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_evidence(candidate: dict[str, Any], errors: list[str]) -> set[str]:
    evidence_items = candidate.get("evidence")
    if not isinstance(evidence_items, list):
        errors.append("candidate evidence must be an array")
        return set()
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence_items):
        if not isinstance(item, dict):
            continue
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str):
            continue
        if evidence_id in evidence_ids:
            errors.append(f"candidate evidence contains duplicate evidence_id at index {index}")
        evidence_ids.add(evidence_id)
        page = item.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 500:
            errors.append(f"candidate evidence page is invalid at index {index}")
        excerpt = item.get("bounded_excerpt")
        if not isinstance(excerpt, str) or not 1 <= len(excerpt) <= 240:
            errors.append(f"candidate evidence excerpt is not bounded at index {index}")
        elif any(ord(char) < 32 and char not in "\t" for char in excerpt):
            errors.append(
                f"candidate evidence excerpt contains a control character at index {index}"
            )
        bbox = item.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                errors.append(f"candidate evidence bbox is not four coordinates at index {index}")
            elif any(not _is_number(value) or not math.isfinite(float(value)) for value in bbox):
                errors.append(
                    f"candidate evidence bbox contains an invalid coordinate at index {index}"
                )
    return evidence_ids


def _validate_candidate(candidate: Any, index: int, errors: list[str]) -> None:
    if not isinstance(candidate, dict):
        errors.append(f"candidate {index} must be an object")
        return
    if candidate.get("candidate_kind") not in CANDIDATE_KINDS:
        errors.append(f"candidate {index} has an invalid candidate_kind")
    if candidate.get("status") not in CANDIDATE_STATUSES:
        errors.append(f"candidate {index} has an invalid status")
    evidence_ids = _validate_evidence(candidate, errors)
    fields = candidate.get("fields")
    if not isinstance(fields, list):
        errors.append(f"candidate {index} fields must be an array")
        fields = []
    seen_fields: set[str] = set()
    for field_index, field in enumerate(fields):
        if not isinstance(field, dict):
            continue
        field_id = field.get("field_id")
        if field_id in seen_fields:
            errors.append(f"candidate {index} contains duplicate field_id at index {field_index}")
        if isinstance(field_id, str):
            seen_fields.add(field_id)
        field_evidence = field.get("evidence_ids")
        if not isinstance(field_evidence, list):
            continue
        if any(evidence_id not in evidence_ids for evidence_id in field_evidence):
            errors.append(f"candidate {index} field references Evidence outside candidate evidence")
        if candidate.get("status") in {"AI_VERIFIED", "USER_CONFIRMED"} and not field_evidence:
            errors.append(f"candidate {index} verified field is missing Evidence")

    issues = candidate.get("issues")
    if not isinstance(issues, list):
        errors.append(f"candidate {index} issues must be an array")
        issues = []
    issue_codes = {item.get("code") for item in issues if isinstance(item, dict)}
    if "TERMS_ONLY_RIDER" in issue_codes:
        if candidate.get("candidate_kind") != "rider":
            errors.append(f"candidate {index} terms-only issue must belong to a rider")
        if candidate.get("status") == "AI_VERIFIED":
            errors.append(f"candidate {index} terms-only rider cannot be AI_VERIFIED")


def validate_policy_candidate_contract() -> list[str]:
    """Return deterministic schema, example, and privacy errors."""

    try:
        schema = load_json(SCHEMA_PATH)
        example = load_json(EXAMPLE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]

    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("policy-candidate schema must use JSON Schema 2020-12")
    if schema.get("title") != "PolicyCandidateBatch":
        errors.append("policy-candidate schema title changed")
    if schema.get("required") != ["schema_version", "candidates"]:
        errors.append("policy-candidate root required fields changed")
    if schema.get("additionalProperties") is not False:
        errors.append("policy-candidate root must reject additional properties")
    for object_schema in _walk_objects(schema):
        if object_schema.get("additionalProperties") is not False:
            errors.append("policy-candidate nested objects must reject additional properties")
            break
    if forbidden_keys(schema):
        errors.append("policy-candidate schema contains a forbidden field")
    if forbidden_keys(example):
        errors.append("policy-candidate example contains a forbidden field")
    errors.extend(
        f"policy-candidate example schema mismatch: {error}"
        for error in validate_schema_instance(schema, example)
    )

    candidates = example.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        errors.append("policy-candidate example must contain two synthetic candidates")
    else:
        if candidates[0].get("status") != "AI_VERIFIED":
            errors.append("first policy-candidate example must be AI_VERIFIED")
        if candidates[1].get("status") != "NEEDS_REVIEW":
            errors.append("second policy-candidate example must be NEEDS_REVIEW")
        for index, candidate in enumerate(candidates):
            _validate_candidate(candidate, index, errors)

    definitions = schema.get("$defs", {})
    if definitions.get("CandidateStatus", {}).get("enum") != CANDIDATE_STATUSES:
        errors.append("candidate status enum changed")
    if definitions.get("CandidateKind", {}).get("enum") != CANDIDATE_KINDS:
        errors.append("candidate kind enum changed")
    if definitions.get("PolicyCandidateFieldId", {}).get("enum") != FIELD_IDS:
        errors.append("candidate field ID enum changed")
    if definitions.get("CandidateIssueCode", {}).get("enum") != ISSUE_CODES:
        errors.append("candidate issue code enum changed")
    if definitions.get("CandidateErrorCode", {}).get("enum") != ERROR_CODES:
        errors.append("candidate error code enum changed")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


def main() -> int:
    errors = validate_policy_candidate_contract()
    if errors:
        print("\n".join(errors))
        return 1
    print("policy candidate contract checks passed (schema, examples, and privacy policy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

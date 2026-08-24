"""Contract and registration tests for the Phase 2 policy ledger boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from familycare_api.main import create_app

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
SCHEMA_PATH = CONTRACT_ROOT / "schemas/policy-ledger.v1.schema.json"
EXAMPLE_PATH = CONTRACT_ROOT / "examples/policy-ledger.v1.json"
GENERATED_PATH = ROOT / "apps/api/src/familycare_api/contracts/generated_business.py"


def load_contract_tools() -> tuple[Any, Any]:
    """Load script modules after making the repository root importable."""

    sys.path.insert(0, str(ROOT))
    from scripts.check_contracts import validate_openapi
    from scripts.generate_business_contract_types import generate

    return validate_openapi, generate


validate_openapi, generate = load_contract_tools()

FORBIDDEN_FIELDS = {
    "source_path",
    "absolute_path",
    "policy_number",
    "raw_pdf",
    "password",
    "archive_key",
    "document_text",
    "household_space_id",
}


def load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing contract artifact: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def walk_objects(value: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        found: list[tuple[str, dict[str, Any]]] = []
        if value.get("type") == "object":
            found.append((path, value))
        for key, child in value.items():
            found.extend(walk_objects(child, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, child in enumerate(value):
            found.extend(walk_objects(child, f"{path}[{index}]"))
        return found
    return []


def walk_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        found = []
        for key, child in value.items():
            found.append((f"{path}.{key}", str(key)))
            found.extend(walk_keys(child, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, child in enumerate(value):
            found.extend(walk_keys(child, f"{path}[{index}]"))
        return found
    return []


def test_policy_schema_and_example_are_strict_and_wholly_synthetic() -> None:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(["schema_version", "family_member_id", "policy_id", "rider_id", "version"]).issubset(
        schema["required"]
    )
    assert all(
        object_schema.get("additionalProperties") is False
        for _, object_schema in walk_objects(schema)
    )
    assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(schema))
    assert all(key.lower() not in FORBIDDEN_FIELDS for _, key in walk_keys(example))
    assert example["schema_version"] == "1"
    assert example["version"] >= 1
    assert example["status"] in {"active", "inactive", "expired", "cancelled", "unknown"}
    assert example["evidence"]["physical_page"] >= 1
    assert example["evidence"]["content_sha256"] == "a" * 64


def test_business_types_are_generated_deterministically() -> None:
    assert GENERATED_PATH.is_file(), "missing generated policy contract module"
    generated = GENERATED_PATH.read_text(encoding="utf-8")
    assert "class PolicyLedger(TypedDict)" in generated
    assert "PolicyErrorCode = Literal[" in generated
    assert "password" not in generated
    assert "document_text" not in generated

    temporary_api = GENERATED_PATH.with_name("generated_business.tmp.py")
    try:
        generate(temporary_api)
        assert temporary_api.read_text(encoding="utf-8") == generated
    finally:
        temporary_api.unlink(missing_ok=True)


def test_policy_router_is_registered_in_the_canonical_app() -> None:
    paths = create_app(enable_synthetic_ingestion=False).openapi()["paths"]
    assert {
        "/api/v1/family-members",
        "/api/v1/family-members/{member_id}",
        "/api/v1/family-members/{member_id}/restore",
        "/api/v1/policies",
        "/api/v1/policies/{policy_id}",
        "/api/v1/policies/{policy_id}/restore",
        "/api/v1/policies/{policy_id}/riders",
    }.issubset(paths)
    assert "/api/v1/documents/analysis" not in paths


def test_committed_openapi_has_no_contract_drift_and_policy_error_codes_are_fixed() -> None:
    assert validate_openapi() == []
    generated = GENERATED_PATH.read_text(encoding="utf-8")
    for code in (
        "AUTHENTICATION_REQUIRED",
        "EVIDENCE_INVALID",
        "FAMILY_MEMBER_NOT_FOUND",
        "POLICY_NOT_FOUND",
        "POLICY_STATE_CONFLICT",
        "VERSION_CONFLICT",
    ):
        assert code in generated

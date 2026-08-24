#!/usr/bin/env python3
"""Validate committed FamilyCare API and worker contracts."""

from __future__ import annotations

import argparse
import json
import re
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from familycare_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "packages/contracts/openapi/familycare.v1.json"
JOB_SCHEMA_PATH = ROOT / "packages/contracts/schemas/analysis-job.v1.schema.json"
JOB_EXAMPLE_PATH = ROOT / "packages/contracts/examples/analysis-job.v1.json"
DOCUMENT_SCHEMA_PATH = ROOT / "packages/contracts/schemas/document-ingestion.v1.schema.json"
POLICY_SCHEMA_PATH = ROOT / "packages/contracts/schemas/policy-ledger.v1.schema.json"
POLICY_EXAMPLE_PATH = ROOT / "packages/contracts/examples/policy-ledger.v1.json"
CANDIDATE_SCHEMA_PATH = ROOT / "packages/contracts/schemas/policy-candidate.v1.schema.json"
CANDIDATE_EXAMPLE_PATH = ROOT / "packages/contracts/examples/policy-candidate.v1.json"
CLAUSE_SCHEMA_PATH = ROOT / "packages/contracts/schemas/clause-search.v1.schema.json"
CLAUSE_EXAMPLE_PATH = ROOT / "packages/contracts/examples/clause-search.v1.json"
BUSINESS_OUTPUT_PATH = ROOT / "apps/api/src/familycare_api/contracts/generated_business.py"
WEB_OUTPUT_PATH = ROOT / "apps/web/src/api/generated.ts"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POLICY_FORBIDDEN_FIELDS = {
    "absolute_path",
    "archive_key",
    "document_text",
    "household_space_id",
    "password",
    "policy_number",
    "raw_pdf",
    "source_path",
}
CLAUSE_FORBIDDEN_FIELDS = POLICY_FORBIDDEN_FIELDS | {"full_text", "raw_query"}


def _load_document_contract_checker() -> Any:
    """Load the document checker from package or direct-script context."""

    try:
        return import_module("scripts.check_document_contracts")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        return import_module("check_document_contracts")


_DOCUMENT_CONTRACT_CHECKER = _load_document_contract_checker()
is_relative_source_key = _DOCUMENT_CONTRACT_CHECKER.is_relative_source_key
valid_uuid4 = _DOCUMENT_CONTRACT_CHECKER.valid_uuid4
validate_schema_instance = _DOCUMENT_CONTRACT_CHECKER.validate_schema_instance
validate_document_contracts = _DOCUMENT_CONTRACT_CHECKER.validate_document_contracts


def _load_business_generator() -> Any:
    """Load the business generator from package or direct-script context."""

    try:
        module = import_module("scripts.generate_business_contract_types")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("generate_business_contract_types")
    return module.generate


generate_business = _load_business_generator()


def _load_candidate_checker() -> Any:
    """Load the policy candidate checker from package or direct-script context."""

    try:
        module = import_module("scripts.check_policy_candidate_contract")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("check_policy_candidate_contract")
    return module


_CANDIDATE_CHECKER = _load_candidate_checker()
validate_policy_candidate_contract = _CANDIDATE_CHECKER.validate_policy_candidate_contract


def _load_web_generator() -> Any:
    """Load the deterministic Web TypeScript generator."""

    try:
        module = import_module("scripts.generate_web_contract_types")
    except ModuleNotFoundError:  # pragma: no cover - direct-script execution path
        module = import_module("generate_web_contract_types")
    return module


_WEB_GENERATOR = _load_web_generator()
generate_web = _WEB_GENERATOR.generate


def render_openapi() -> str:
    """Render the canonical OpenAPI document deterministically."""

    return (
        json.dumps(
            create_app(enable_synthetic_ingestion=True).openapi(),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def load_json(path: Path) -> dict[str, Any]:
    """Load a required JSON object or raise a useful validation error."""

    if not path.is_file():
        raise ValueError(f"missing contract artifact: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path.relative_to(ROOT)}")
    return value


def validate_openapi() -> list[str]:
    """Return drift errors for the committed API contract."""

    if not OPENAPI_PATH.is_file():
        return [f"missing contract artifact: {OPENAPI_PATH.relative_to(ROOT)}"]
    committed = OPENAPI_PATH.read_text(encoding="utf-8")
    generated = render_openapi()
    errors: list[str] = []
    if committed != generated:
        errors.append("OpenAPI contract drift: regenerate familycare.v1.json")
    document = json.loads(generated)
    paths = document.get("paths", {})
    expected_paths = {
        "/health/live",
        "/health/ready",
        "/api/v1/documents/analysis",
        "/api/v1/analysis-jobs/{job_id}",
        "/api/v1/family-members",
        "/api/v1/family-members/trash",
        "/api/v1/family-members/{member_id}",
        "/api/v1/family-members/{member_id}/restore",
        "/api/v1/policies",
        "/api/v1/policies/trash",
        "/api/v1/policies/{policy_id}",
        "/api/v1/policies/{policy_id}/restore",
        "/api/v1/policies/{policy_id}/riders",
        "/api/v1/policies/{policy_id}/candidate-fields/{field_id}",
        "/api/v1/review-items",
        "/api/v1/review-items/{review_item_id}",
        "/api/v1/review-items/{review_item_id}/candidate-fields/{field_id}",
        "/api/v1/review-items/{review_item_id}/confirm",
        "/api/v1/review-items/{review_item_id}/reject",
        "/api/v1/terms-editions",
        "/api/v1/terms-editions/{terms_edition_id}/clauses",
        "/api/v1/clauses/search",
    }
    if set(paths) != expected_paths:
        errors.append(
            "OpenAPI paths must contain health, policy, Clause, and gated analysis routes"
        )
        return errors

    clause_search = paths["/api/v1/clauses/search"].get("post", {})
    if clause_search.get("parameters"):
        errors.append("Clause search must not expose URL query parameters")
    clause_request = (
        clause_search.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if clause_request.get("$ref") != "#/components/schemas/ClauseSearchQuery":
        errors.append("Clause search must use the strict JSON request body")

    post = paths["/api/v1/documents/analysis"].get("post", {})
    status_get = paths["/api/v1/analysis-jobs/{job_id}"].get("get", {})
    if set(post.get("responses", {})) != {"202", "422", "503"}:
        errors.append("analysis POST responses must be exactly 202, 422, and 503")
    if set(status_get.get("responses", {})) != {"200", "404", "422", "503"}:
        errors.append("analysis status responses must be exactly 200, 404, 422, and 503")
    for operation in (post, status_get):
        description = str(operation.get("description", ""))
        if "synthetic-only" not in description or "not production-safe" not in description:
            errors.append("analysis routes must state their local synthetic-only safety boundary")

    schemas = document.get("components", {}).get("schemas", {})
    request_schema = schemas.get("DocumentAnalysisRequest", {})
    if set(request_schema.get("properties", {})) != {
        "schema_version",
        "source_key",
        "document_kind",
        "extractor_config",
    }:
        errors.append("analysis request exposes fields outside the v1 ingestion contract")
    if request_schema.get("additionalProperties") is not False:
        errors.append("analysis request must reject additional properties")
    request_examples = request_schema.get("examples", [])
    if (
        not isinstance(request_examples, list)
        or len(request_examples) != 1
        or not isinstance(request_examples[0], dict)
        or not str(request_examples[0].get("source_key", "")).startswith("synthetic/")
    ):
        errors.append("analysis request must have one wholly synthetic example")
    document_contract = load_json(DOCUMENT_SCHEMA_PATH)
    contract_properties = document_contract.get("properties", {})
    for field in ("schema_version", "source_key", "document_kind"):
        openapi_field = request_schema.get("properties", {}).get(field, {})
        contract_field = contract_properties.get(field, {})
        for keyword in ("const", "enum", "minLength", "maxLength", "pattern", "type"):
            if openapi_field.get(keyword) != contract_field.get(keyword):
                errors.append(f"analysis request {field} drifted from the v1 JSON Schema")
                break
    extractor_schema = schemas.get("ExtractorConfigRequest", {})
    contract_extractor = document_contract.get("$defs", {}).get("ExtractorConfig", {})
    if extractor_schema.get("additionalProperties") is not False:
        errors.append("analysis extractor config must reject additional properties")
    for field in ("profile", "quality_rule_version", "table_strategy"):
        openapi_field = extractor_schema.get("properties", {}).get(field, {})
        contract_field = contract_extractor.get("properties", {}).get(field, {})
        for keyword in ("const", "enum", "type"):
            if openapi_field.get(keyword) != contract_field.get(keyword):
                errors.append(f"analysis extractor {field} drifted from the v1 JSON Schema")
                break
    accepted_schema = schemas.get("AnalysisAcceptedResponse", {})
    accepted_fields = {
        "schema_version",
        "job_id",
        "state",
        "status_url",
    }
    if set(accepted_schema.get("properties", {})) != accepted_fields:
        errors.append("analysis accepted response exposes unexpected fields")
    if set(accepted_schema.get("required", [])) != accepted_fields:
        errors.append("analysis accepted response fields must all be required")
    status_schema = schemas.get("AnalysisJobStatusResponse", {})
    status_fields = {
        "schema_version",
        "job_id",
        "document_id",
        "state",
        "attempts",
        "error_code",
        "extraction_summary",
    }
    if set(status_schema.get("properties", {})) != status_fields:
        errors.append("analysis status response exposes unexpected fields")
    required_status_fields = status_fields - {"error_code", "extraction_summary"}
    if set(status_schema.get("required", [])) != required_status_fields:
        errors.append("analysis status identity and state fields must all be required")
    if "source_key" in json.dumps(
        [accepted_schema.get("examples", []), status_schema.get("examples", [])],
        sort_keys=True,
    ):
        errors.append("analysis response examples must not expose source keys")
    return errors


def _policy_forbidden_keys(value: Any, path: str = "$") -> list[str]:
    """Return policy-contract paths that cross a prohibited data boundary."""

    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in POLICY_FORBIDDEN_FIELDS:
                errors.append(child_path)
            errors.extend(_policy_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_policy_forbidden_keys(child, f"{path}[{index}]"))
    return errors


def validate_policy_contract() -> list[str]:
    """Validate the policy-ledger schema, example, and generated API consumer."""

    errors: list[str] = []
    try:
        schema = load_json(POLICY_SCHEMA_PATH)
        example = load_json(POLICY_EXAMPLE_PATH)
    except (json.JSONDecodeError, ValueError) as error:
        return [str(error)]

    if schema.get("additionalProperties") is not False:
        errors.append("policy-ledger schema must reject additional properties")
    required = {
        "schema_version",
        "family_member_id",
        "policy_id",
        "rider_id",
        "status",
        "version",
        "evidence",
    }
    if set(schema.get("required", [])) != required:
        errors.append("policy-ledger schema required properties are inconsistent")
    if _policy_forbidden_keys(schema):
        errors.append("policy-ledger schema contains a forbidden field")
    if _policy_forbidden_keys(example):
        errors.append("policy-ledger example contains a forbidden field")
    errors.extend(
        f"policy-ledger example schema mismatch: {error}"
        for error in validate_schema_instance(schema, example)
    )
    policy_error_codes = schema.get("$defs", {}).get("PolicyErrorCode", {}).get("enum")
    if policy_error_codes != [
        "AUTHENTICATION_REQUIRED",
        "EVIDENCE_INVALID",
        "FAMILY_MEMBER_NOT_FOUND",
        "POLICY_NOT_FOUND",
        "POLICY_STATE_CONFLICT",
        "VERSION_CONFLICT",
    ]:
        errors.append("policy error-code enum changed")
    policy_api_error_codes = schema.get("$defs", {}).get("PolicyApiErrorCode", {}).get("enum")
    if policy_api_error_codes != [
        "AUTHENTICATION_REQUIRED",
        "EVIDENCE_INVALID",
        "FAMILY_MEMBER_NOT_FOUND",
        "INVALID_REQUEST",
        "POLICY_NOT_FOUND",
        "POLICY_STATE_CONFLICT",
        "RESOURCE_LIMIT_EXCEEDED",
        "VERSION_CONFLICT",
    ]:
        errors.append("policy API error-code enum changed")

    if not BUSINESS_OUTPUT_PATH.is_file():
        errors.append("generated business contract module is missing")
    else:
        with TemporaryDirectory() as directory:
            generated = Path(directory) / "generated_business.py"
            try:
                generate_business(generated)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"business contract generation failed: {error}")
            else:
                if BUSINESS_OUTPUT_PATH.read_bytes() != generated.read_bytes():
                    errors.append("generated business contract module is stale")
    return errors


def _nested_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """Return every nested object key for contract privacy checks."""

    if isinstance(value, dict):
        keys: list[tuple[str, str]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            keys.append((child_path, str(key)))
            keys.extend(_nested_keys(child, child_path))
        return keys
    if isinstance(value, list):
        keys = []
        for index, child in enumerate(value):
            keys.extend(_nested_keys(child, f"{path}[{index}]"))
        return keys
    return []


def _object_schemas(value: Any) -> list[dict[str, Any]]:
    """Return all object schemas in a JSON Schema document."""

    if isinstance(value, dict):
        schemas = [value] if value.get("type") == "object" else []
        for child in value.values():
            schemas.extend(_object_schemas(child))
        return schemas
    if isinstance(value, list):
        schemas = []
        for child in value:
            schemas.extend(_object_schemas(child))
        return schemas
    return []


def validate_clause_search_contract() -> list[str]:
    """Validate the strict Clause search schema and its synthetic example."""

    try:
        schema = load_json(CLAUSE_SCHEMA_PATH)
        example = load_json(CLAUSE_EXAMPLE_PATH)
    except (json.JSONDecodeError, ValueError) as error:
        return [str(error)]

    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("clause-search schema must use JSON Schema draft 2020-12")
    if schema.get("additionalProperties") is not False:
        errors.append("clause-search schema must reject additional properties")
    if _forbidden_clause_keys(schema):
        errors.append("clause-search schema contains a forbidden field")
    if _forbidden_clause_keys(example):
        errors.append("clause-search example contains a forbidden field")
    if not all(
        object_schema.get("additionalProperties") is False
        for object_schema in _object_schemas(schema)
    ):
        errors.append("clause-search schema object definitions must reject additional properties")

    required = {
        "schema_version",
        "normalization_version",
        "query_matched_count",
        "hits",
    }
    if set(schema.get("required", [])) != required:
        errors.append("clause-search schema required properties are inconsistent")
    hit_required = {
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
    hit_schema = schema.get("$defs", {}).get("ClauseSearchHit", {})
    if set(hit_schema.get("required", [])) != hit_required:
        errors.append("clause-search hit required properties are inconsistent")
    if schema.get("properties", {}).get("hits", {}).get("maxItems") != 50:
        errors.append("clause-search hits must be bounded at 50")

    errors.extend(
        f"clause-search example schema mismatch: {error}"
        for error in validate_schema_instance(schema, example)
    )
    if example.get("schema_version") != "1":
        errors.append("clause-search example schema_version must be 1")
    if example.get("normalization_version") != "unicode-nfc-v1":
        errors.append("clause-search example normalization version is not current")
    hits = example.get("hits")
    if not isinstance(hits, list):
        errors.append("clause-search example hits must be an array")
    else:
        matched_count = example.get("query_matched_count")
        if isinstance(matched_count, int) and matched_count < len(hits):
            errors.append("clause-search matched count cannot be below returned hits")
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            if len(str(hit.get("excerpt", ""))) > 320:
                errors.append("clause-search example excerpt exceeds 320 characters")
            if hit.get("normalization_version") != "unicode-nfc-v1":
                errors.append("clause-search hit normalization version is not current")
            evidence = hit.get("evidence")
            if isinstance(evidence, list) and any(
                not isinstance(item, dict) or item.get("page_number", 0) < 1 for item in evidence
            ):
                errors.append("clause-search evidence page_number must be 1-based")
    return errors


def _forbidden_clause_keys(value: Any, path: str = "$") -> list[str]:
    """Return nested Clause contract paths crossing a prohibited boundary."""

    return [
        child_path
        for child_path, key in _nested_keys(value, path)
        if key.lower() in CLAUSE_FORBIDDEN_FIELDS
    ]


def validate_web_generated_outputs() -> list[str]:
    """Regenerate the Web consumer in a temporary directory and compare bytes."""

    if not WEB_OUTPUT_PATH.is_file():
        return ["generated Web contract is missing"]
    with TemporaryDirectory() as directory:
        generated = Path(directory) / "generated.ts"
        try:
            generate_web(generated)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return [f"Web contract generation failed: {error}"]
        if WEB_OUTPUT_PATH.read_bytes() != generated.read_bytes():
            return ["generated Web contract is stale"]
    return []


def validate_job_contract() -> list[str]:
    """Validate the versioned analyzer job schema and its synthetic example."""

    try:
        schema = load_json(JOB_SCHEMA_PATH)
        example = load_json(JOB_EXAMPLE_PATH)
    except (json.JSONDecodeError, ValueError) as error:
        return [str(error)]

    errors: list[str] = []
    required = {
        "schema_version",
        "job_id",
        "document_id",
        "source_key",
        "settings",
        "extractor_config_hash",
        "state",
    }
    if set(schema.get("required", [])) != required:
        errors.append("analysis job schema required properties are inconsistent")
    if schema.get("additionalProperties") is not False:
        errors.append("analysis job schema must reject additional properties")
    if schema.get("properties", {}).get("document_id") != {"$ref": "#/$defs/DocumentId"}:
        errors.append("analysis job document_id must reference the UUID DocumentId contract")
    if set(example) != required:
        errors.append("analysis job example keys do not match the schema")
    if example.get("schema_version") != "1":
        errors.append("analysis job schema_version must be 1")

    if not valid_uuid4(example.get("job_id")):
        errors.append("analysis job job_id must be a UUID")

    if not valid_uuid4(example.get("document_id")):
        errors.append("analysis job example document_id must be a UUIDv4")
    if not is_relative_source_key(example.get("source_key")):
        errors.append("analysis job source_key must be a relative path")
    if not SHA256_PATTERN.fullmatch(str(example.get("extractor_config_hash", ""))):
        errors.append("analysis job extractor_config_hash must be 64 lowercase hex characters")
    if example.get("state") not in {
        "queued",
        "running",
        "succeeded",
        "retryable_failed",
        "permanently_failed",
        "cancelled",
    }:
        errors.append("analysis job state is not one of the six stable states")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-openapi", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Validate contracts or regenerate only the deterministic OpenAPI artifact."""

    args = parse_args()
    if args.write_openapi:
        OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPENAPI_PATH.write_text(render_openapi(), encoding="utf-8", newline="\n")
        print(f"wrote {OPENAPI_PATH.relative_to(ROOT)}")
        return 0

    errors = [
        *validate_openapi(),
        *validate_job_contract(),
        *validate_document_contracts(),
        *validate_policy_contract(),
        *validate_policy_candidate_contract(),
        *validate_clause_search_contract(),
        *validate_web_generated_outputs(),
    ]
    if errors:
        print("\n".join(errors))
        return 1
    print(
        "contract checks passed (OpenAPI, analysis-job.v1, document ingestion, "
        "policy-ledger.v1, policy-candidate.v1, clause-search.v1, and generated Web contracts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

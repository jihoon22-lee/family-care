#!/usr/bin/env python3
"""Validate committed FamilyCare API and worker contracts."""

from __future__ import annotations

import argparse
import json
import math
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
RIDER_CLAUSE_RULES_SCHEMA_PATH = (
    ROOT / "packages/contracts/schemas/rider-clause-rules.v1.schema.json"
)
RIDER_CLAUSE_RULES_EXAMPLE_PATH = ROOT / "packages/contracts/examples/rider-clause-rules.v1.json"
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
RIDER_CLAUSE_RULES_FORBIDDEN_FIELDS = CLAUSE_FORBIDDEN_FIELDS | {
    "document_path",
    "document_text",
    "extraction_text",
    "file_path",
    "full_text",
    "ocr_text",
    "path",
    "pdf_path",
    "prompt",
    "raw_provider_response",
    "raw_text",
    "source_key",
    "text",
    "url",
}
RIDER_CLAUSE_RULES_FIELD_PATHS = [
    "MedicalEvent.event_date",
    "MedicalEvent.classification",
    "MedicalEvent.admission_days",
    "PolicyContract.contract_start",
    "PolicyContract.contract_end",
    "Rider.status",
    "Rider.insured_amount",
    "ClaimHistory.counted_occurrence",
]
RIDER_CLAUSE_RULE_KINDS = [
    "eligibility",
    "classification",
    "temporal",
    "exclusion",
    "frequency",
    "fixed_amount",
    "rate_amount",
    "indemnity_eligibility",
    "deductible",
    "limit",
    "required_document",
]
RIDER_CLAUSE_RULE_EXPRESSION_OPERATORS = [
    "all",
    "any",
    "not",
    "present",
    "equals",
    "in",
    "range",
    "date_between",
    "days_since",
    "count_before",
]
RIDER_CLAUSE_RULE_CALCULATION_OPERATORS = [
    "add",
    "subtract",
    "multiply",
    "min",
    "max",
    "round",
]
RIDER_CLAUSE_RULE_ROUNDING_MODES = ["half_up", "half_even", "up", "down"]
RIDER_CLAUSE_RULE_REVIEW_STATES = ["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]
RIDER_CLAUSE_LINK_REVIEW_STATES = [
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
    "rejected",
]
RIDER_CLAUSE_LINK_REJECTION_REASONS = [
    "USER_REJECTED",
    "WRONG_CLAUSE",
    "WRONG_EDITION",
    "NOT_APPLICABLE",
]
DECISION_SCHEMA_PATH = ROOT / "packages/contracts/schemas/coverage-decision.v1.schema.json"
DECISION_EXAMPLE_PATH = ROOT / "packages/contracts/examples/coverage-decision.v1.json"
DECISION_FORBIDDEN_FIELDS = RIDER_CLAUSE_RULES_FORBIDDEN_FIELDS | {
    "diagnosis_text",
    "file_id",
    "image_bytes",
    "pdf_bytes",
}
DECISION_TRI_STATES = ["MATCH", "NO_MATCH", "UNKNOWN"]


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
        "/api/v1/review-items/{review_item_id}/fields/{field_id}",
        "/api/v1/review-items/{review_item_id}/confirm",
        "/api/v1/review-items/{review_item_id}/reject",
        "/api/v1/terms-editions",
        "/api/v1/terms-editions/{terms_edition_id}/clauses",
        "/api/v1/clauses/search",
        "/api/v1/riders/{rider_id}/clause-links",
        "/api/v1/rider-clause-links/{link_id}/confirm",
        "/api/v1/rider-clause-links/{link_id}/reject",
        "/api/v1/coverage-rules/{rule_id}/versions",
        "/api/v1/coverage-rules/{rule_id}/publish",
        "/api/v1/medical-events",
        "/api/v1/medical-events/trash",
        "/api/v1/medical-events/{event_id}",
        "/api/v1/medical-events/{event_id}/analyze",
        "/api/v1/medical-events/{event_id}/restore",
        "/api/v1/medical-events/{event_id}/results/{version}",
    }
    if set(paths) != expected_paths:
        errors.append(
            "OpenAPI paths must contain health, policy, Clause, analysis, and decision routes"
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

    schemas = document.get("components", {}).get("schemas", {})
    rule_publish = paths["/api/v1/coverage-rules/{rule_id}/publish"].get("post", {})
    publish_request = (
        rule_publish.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if publish_request.get("$ref") != "#/components/schemas/CoverageRulePublishRequest":
        errors.append("CoverageRule publish must select one stored version")
    publish_properties = schemas.get("CoverageRulePublishRequest", {}).get("properties", {})
    if set(publish_properties) != {"expected_version", "version_id"}:
        errors.append("CoverageRule publish request must not accept an arbitrary rule body")

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

    event_create = paths["/api/v1/medical-events"].get("post", {})
    event_request_ref = (
        event_create.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref")
    )
    if event_request_ref != "#/components/schemas/MedicalEventCreateRequest":
        errors.append("MedicalEvent create must use the strict structured request")
    event_request = schemas.get("MedicalEventCreateRequest", {})
    if event_request.get("additionalProperties") is not False:
        errors.append("MedicalEvent create must reject additional properties")
    if set(event_request.get("properties", {})) != {
        "family_member_id",
        "mode",
        "event_date",
        "visit_date",
        "facts",
    }:
        errors.append("MedicalEvent create exposes fields outside the structured event boundary")
    decision_response = schemas.get("CoverageDecisionResponse", {})
    if decision_response.get("additionalProperties") is not False:
        errors.append("coverage decision response must reject additional properties")
    expected_decision_fields = {
        "schema_version",
        "run_id",
        "medical_event_id",
        "event_version",
        "engine_version",
        "rule_set_version",
        "policy_snapshot_at",
        "stale",
        "candidates",
        "evaluations",
    }
    if set(decision_response.get("properties", {})) != expected_decision_fields:
        errors.append("coverage decision response fields drifted from v1")
    if (
        "amount"
        in json.dumps(
            [
                event_request,
                schemas.get("MedicalEventUpdateRequest", {}),
                decision_response,
                schemas.get("ClaimCandidateResponse", {}),
                schemas.get("RuleEvaluationResponse", {}),
            ],
            sort_keys=True,
        ).lower()
    ):
        errors.append("MedicalEvent and decision OpenAPI must not expose amount fields")
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


def _schema_values(value: Any) -> list[Any]:
    """Return every nested JSON Schema node for generic shape checks."""

    if isinstance(value, dict):
        nodes: list[Any] = [value]
        for child in value.values():
            nodes.extend(_schema_values(child))
        return nodes
    if isinstance(value, list):
        nodes = []
        for child in value:
            nodes.extend(_schema_values(child))
        return nodes
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


def _forbidden_rider_clause_rules_keys(value: Any, path: str = "$") -> list[str]:
    """Return Rider-Clause contract paths crossing a private data boundary."""

    return [
        child_path
        for child_path, key in _nested_keys(value, path)
        if key.lower() in RIDER_CLAUSE_RULES_FORBIDDEN_FIELDS
    ]


def _rider_clause_rules_string_values(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """Return example string values so path-like data cannot cross the contract boundary."""

    if isinstance(value, dict):
        strings: list[tuple[str, str]] = []
        for key, child in value.items():
            strings.extend(_rider_clause_rules_string_values(child, f"{path}.{key}"))
        return strings
    if isinstance(value, list):
        strings = []
        for index, child in enumerate(value):
            strings.extend(_rider_clause_rules_string_values(child, f"{path}[{index}]"))
        return strings
    if isinstance(value, str):
        return [(path, value)]
    return []


def _validate_synthetic_uuid(value: Any, path: str, errors: list[str]) -> None:
    """Require example identifiers to use the repository's obvious synthetic UUID range."""

    if not valid_uuid4(value):
        errors.append(f"rider-clause-rules {path} must be a UUIDv4")
    elif not str(value).startswith("00000000-0000-4000-8000-"):
        errors.append(f"rider-clause-rules {path} must use a synthetic UUID")


def _validate_rule_evidence_example(evidence: Any, path: str, errors: list[str]) -> set[str]:
    """Validate evidence identity, page, bbox, and synthetic content-hash bounds."""

    if not isinstance(evidence, list):
        errors.append(f"rider-clause-rules {path} must be an array")
        return set()
    if not 2 <= len(evidence) <= 16:
        errors.append(f"rider-clause-rules {path} must contain 2 to 16 items")
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            continue
        evidence_id = item.get("evidence_id")
        if evidence_id in evidence_ids:
            errors.append(f"rider-clause-rules {item_path}.evidence_id is duplicated")
        if isinstance(evidence_id, str):
            evidence_ids.add(evidence_id)
        _validate_synthetic_uuid(evidence_id, f"{item_path}.evidence_id", errors)
        _validate_synthetic_uuid(
            item.get("document_version_id"), f"{item_path}.document_version_id", errors
        )
        content_sha256 = item.get("content_sha256")
        if not isinstance(content_sha256, str) or SHA256_PATTERN.fullmatch(content_sha256) is None:
            errors.append(f"rider-clause-rules {item_path}.content_sha256 is not a SHA-256 value")
        elif len(set(content_sha256)) != 1 or content_sha256 not in {"a" * 64, "b" * 64}:
            errors.append(f"rider-clause-rules {item_path}.content_sha256 must be synthetic")
        page = item.get("physical_page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 500:
            errors.append(f"rider-clause-rules {item_path}.physical_page must be 1 through 500")
        bbox = item.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                errors.append(f"rider-clause-rules {item_path}.bbox must contain four coordinates")
            elif any(
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not math.isfinite(float(coordinate))
                or not 0 <= coordinate <= 1_000_000
                for coordinate in bbox
            ):
                errors.append(f"rider-clause-rules {item_path}.bbox contains an invalid coordinate")
    return evidence_ids


def _validate_rider_clause_rules_example(example: dict[str, Any]) -> list[str]:
    """Validate semantic and synthetic-only invariants not covered by the local schema subset."""

    errors: list[str] = []
    if example.get("schema_version") != "1":
        errors.append("rider-clause-rules example schema_version must be 1")
    links = example.get("rider_clause_links")
    if not isinstance(links, list) or not 1 <= len(links) <= 64:
        errors.append("rider-clause-rules example links must contain 1 to 64 items")
    else:
        for index, link in enumerate(links):
            path = f"$.rider_clause_links[{index}]"
            if not isinstance(link, dict):
                continue
            for field in (
                "id",
                "rider_id",
                "terms_edition_id",
                "clause_id",
                "candidate_version_id",
            ):
                _validate_synthetic_uuid(link.get(field), f"{path}.{field}", errors)
            for field in ("rider_label", "clause_label", "terms_edition_label"):
                if field in link and (
                    not isinstance(link[field], str) or not link[field].startswith("Synthetic ")
                ):
                    errors.append(f"rider-clause-rules {path}.{field} must be synthetic")
            _validate_rule_evidence_example(link.get("evidence"), f"{path}.evidence", errors)

    versions = example.get("coverage_rule_versions")
    if not isinstance(versions, list) or not 1 <= len(versions) <= 64:
        errors.append("rider-clause-rules example rule versions must contain 1 to 64 items")
    else:
        for index, version in enumerate(versions):
            path = f"$.coverage_rule_versions[{index}]"
            if not isinstance(version, dict):
                continue
            for field in ("id", "coverage_rule_id", "candidate_version_id"):
                _validate_synthetic_uuid(version.get(field), f"{path}.{field}", errors)
            expression_present = "expression" in version
            calculation_present = "calculation" in version
            if expression_present == calculation_present:
                errors.append(
                    f"rider-clause-rules {path} must contain exactly one expression or calculation"
                )
            if version.get("executable") is True and version.get("review_state") not in {
                "AI_VERIFIED",
                "USER_CONFIRMED",
            }:
                errors.append(
                    f"rider-clause-rules {path}.executable requires a verified review_state"
                )
            for field in ("generator_version", "verifier_version"):
                if not isinstance(version.get(field), str) or not version[field].startswith(
                    "synthetic-"
                ):
                    errors.append(f"rider-clause-rules {path}.{field} must be synthetic")
            reason_code = version.get("result_reason_code")
            if not isinstance(reason_code, str) or not reason_code.startswith("SYNTHETIC_"):
                errors.append(f"rider-clause-rules {path}.result_reason_code must be synthetic")
            input_paths = version.get("input_field_paths")
            if isinstance(input_paths, list) and len(input_paths) != len(set(input_paths)):
                errors.append(f"rider-clause-rules {path}.input_field_paths must be unique")
            _validate_rule_evidence_example(version.get("evidence"), f"{path}.evidence", errors)
    return errors


def validate_rider_clause_rules_contract() -> list[str]:
    """Validate Rider-Clause links, data-only rules, and their synthetic example."""

    try:
        schema = load_json(RIDER_CLAUSE_RULES_SCHEMA_PATH)
        example = load_json(RIDER_CLAUSE_RULES_EXAMPLE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]

    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("rider-clause-rules schema must use JSON Schema draft 2020-12")
    if schema.get("title") != "RiderClauseRules":
        errors.append("rider-clause-rules schema title changed")
    if schema.get("type") != "object":
        errors.append("rider-clause-rules schema root must be an object")
    if schema.get("required") != [
        "schema_version",
        "rider_clause_links",
        "coverage_rule_versions",
    ]:
        errors.append("rider-clause-rules root required fields changed")
    if schema.get("additionalProperties") is not False:
        errors.append("rider-clause-rules root must reject additional properties")
    object_schemas = _object_schemas(schema)
    if not object_schemas or any(
        object_schema.get("additionalProperties") is not False for object_schema in object_schemas
    ):
        errors.append("rider-clause-rules nested objects must reject additional properties")
    if _forbidden_rider_clause_rules_keys(schema):
        errors.append("rider-clause-rules schema contains a forbidden field")
    if _forbidden_rider_clause_rules_keys(example):
        errors.append("rider-clause-rules example contains a forbidden field")
    for path, value in _rider_clause_rules_string_values(example):
        if (
            "\n" in value
            or "\r" in value
            or "\\" in value
            or "://" in value
            or value.startswith(("/", "~/"))
            or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        ):
            errors.append(f"rider-clause-rules example contains a path-like value at {path}")
    errors.extend(
        f"rider-clause-rules example schema mismatch: {error}"
        for error in validate_schema_instance(schema, example)
    )

    definitions = schema.get("$defs", {})
    enum_expectations = {
        "FieldPath": RIDER_CLAUSE_RULES_FIELD_PATHS,
        "RuleKind": RIDER_CLAUSE_RULE_KINDS,
        "CalculationOperator": RIDER_CLAUSE_RULE_CALCULATION_OPERATORS,
        "RoundingMode": RIDER_CLAUSE_RULE_ROUNDING_MODES,
        "RuleReviewState": RIDER_CLAUSE_RULE_REVIEW_STATES,
        "LinkReviewState": RIDER_CLAUSE_LINK_REVIEW_STATES,
    }
    for definition_name, expected in enum_expectations.items():
        if definitions.get(definition_name, {}).get("enum") != expected:
            errors.append(f"rider-clause-rules {definition_name} enum changed")
    reject_reason = (
        definitions.get("RejectRiderClauseLinkRequest", {})
        .get("properties", {})
        .get("reason_code", {})
    )
    if reject_reason.get("enum") != RIDER_CLAUSE_LINK_REJECTION_REASONS:
        errors.append("rider-clause-rules link rejection reason enum changed")

    expression = definitions.get("RuleExpression", {})
    expression_branches = expression.get("anyOf", [])
    expression_operators = [
        branch.get("properties", {}).get("op", {}).get("const")
        for branch in expression_branches
        if isinstance(branch, dict)
    ]
    if expression_operators != RIDER_CLAUSE_RULE_EXPRESSION_OPERATORS:
        errors.append("rider-clause-rules expression operator allowlist changed")
    if len(expression_branches) != len(RIDER_CLAUSE_RULE_EXPRESSION_OPERATORS):
        errors.append("rider-clause-rules expression branches changed")
    literal = definitions.get("RuleLiteral", {})
    literal_branches = literal.get("anyOf", [])
    if len(literal_branches) != 3:
        errors.append("rider-clause-rules literals must be string, number, or boolean only")
    elif (
        literal_branches[0].get("type") != "string"
        or literal_branches[0].get("minLength") != 1
        or literal_branches[0].get("maxLength") != 160
        or literal_branches[1].get("type") != "number"
        or literal_branches[1].get("minimum") != 0
        or literal_branches[1].get("maximum") != 1_000_000_000_000_000
        or literal_branches[2].get("type") != "boolean"
    ):
        errors.append("rider-clause-rules literal bounds or types changed")

    for array_schema in (
        candidate
        for candidate in _schema_values(schema)
        if isinstance(candidate, dict) and candidate.get("type") == "array"
    ):
        if not isinstance(array_schema.get("maxItems"), int):
            errors.append("rider-clause-rules every array must have a maxItems bound")
            break
        if array_schema["maxItems"] > 64:
            errors.append("rider-clause-rules array maxItems must not exceed 64")
            break

    page_number = definitions.get("PageNumber", {})
    if page_number.get("minimum") != 1 or page_number.get("maximum") != 500:
        errors.append("rider-clause-rules page numbers must be bounded from 1 through 500")
    bbox = definitions.get("BoundingBox", {})
    bbox_item = bbox.get("items", {})
    if (
        bbox.get("minItems") != 4
        or bbox.get("maxItems") != 4
        or bbox_item.get("minimum") != 0
        or bbox_item.get("maximum") != 1_000_000
    ):
        errors.append("rider-clause-rules bounding boxes must be four bounded coordinates")
    for definition_name in ("RiderClauseLink", "CoverageRuleVersion"):
        evidence_schema = (
            definitions.get(definition_name, {}).get("properties", {}).get("evidence", {})
        )
        if evidence_schema.get("minItems") != 2 or evidence_schema.get("maxItems") != 16:
            errors.append(f"rider-clause-rules {definition_name} evidence bounds changed")
    input_paths = (
        definitions.get("CoverageRuleVersion", {})
        .get("properties", {})
        .get("input_field_paths", {})
    )
    if (
        input_paths.get("minItems") != 1
        or input_paths.get("maxItems") != 8
        or input_paths.get("uniqueItems") is not True
    ):
        errors.append("rider-clause-rules input field paths must be unique and bounded")
    for property_name, max_items in (("rider_clause_links", 64), ("coverage_rule_versions", 64)):
        property_schema = schema.get("properties", {}).get(property_name, {})
        if property_schema.get("maxItems") != max_items:
            errors.append(f"rider-clause-rules {property_name} must be bounded at {max_items}")

    errors.extend(_validate_rider_clause_rules_example(example))
    return errors


def _forbidden_decision_keys(value: Any, path: str = "$") -> list[str]:
    """Return decision-contract paths crossing the private-data boundary."""

    return [
        child_path
        for child_path, key in _nested_keys(value, path)
        if key.lower() in DECISION_FORBIDDEN_FIELDS or key.lower() == "amount"
    ]


def _validate_decision_example_uuid(value: Any, path: str, errors: list[str]) -> None:
    """Require decision example identifiers to use the obvious synthetic range."""

    if not valid_uuid4(value):
        errors.append(f"coverage-decision {path} must be a UUIDv4")
    elif not str(value).startswith("00000000-0000-4000-8000-"):
        errors.append(f"coverage-decision {path} must use a synthetic UUID")


def _validate_decision_example_evidence(evidence: Any, path: str, errors: list[str]) -> None:
    """Validate bounded Evidence identity without accepting document content."""

    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16:
        errors.append(f"coverage-decision {path} must contain 1 to 16 items")
        return
    for index, item in enumerate(evidence):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            continue
        for field in ("evidence_id", "document_version_id", "extraction_id"):
            _validate_decision_example_uuid(item.get(field), f"{item_path}.{field}", errors)
        content_sha256 = item.get("content_sha256")
        if not isinstance(content_sha256, str) or len(content_sha256) != 64:
            errors.append(f"coverage-decision {item_path}.content_sha256 is not a SHA-256 value")
        elif content_sha256 not in {"a" * 64, "b" * 64}:
            errors.append(f"coverage-decision {item_path}.content_sha256 must be synthetic")
        page = item.get("physical_page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 500:
            errors.append(f"coverage-decision {item_path}.physical_page is out of bounds")


def _validate_decision_example_ids(value: Any, path: str, errors: list[str]) -> None:
    """Check every identifier in the example without traversing raw payload values."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "run_id" or key.endswith("_id") or key == "id":
                _validate_decision_example_uuid(child, child_path, errors)
            _validate_decision_example_ids(child, child_path, errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_decision_example_ids(child, f"{path}[{index}]", errors)


def validate_decision_contract() -> list[str]:
    """Validate the strict deterministic decision response and synthetic example."""

    try:
        schema = load_json(DECISION_SCHEMA_PATH)
        example = load_json(DECISION_EXAMPLE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]

    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("coverage-decision schema must use JSON Schema draft 2020-12")
    if schema.get("title") != "CoverageDecision":
        errors.append("coverage-decision schema title changed")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        errors.append("coverage-decision root must be a strict object")
    expected_required = [
        "schema_version",
        "run_id",
        "medical_event_id",
        "event_version",
        "engine_version",
        "rule_set_version",
        "policy_snapshot_at",
        "stale",
        "candidates",
        "evaluations",
    ]
    if schema.get("required") != expected_required:
        errors.append("coverage-decision root required fields changed")
    object_schemas = _object_schemas(schema)
    if not object_schemas or any(
        item.get("additionalProperties") is not False for item in object_schemas
    ):
        errors.append("coverage-decision nested objects must reject additional properties")
    if _forbidden_decision_keys(schema):
        errors.append("coverage-decision schema contains a forbidden field")
    if _forbidden_decision_keys(example):
        errors.append("coverage-decision example contains a forbidden field")
    errors.extend(
        f"coverage-decision example schema mismatch: {error}"
        for error in validate_schema_instance(schema, example)
    )

    definitions = schema.get("$defs", {})
    if definitions.get("TriState", {}).get("enum") != DECISION_TRI_STATES:
        errors.append("coverage-decision TriState enum changed")
    evaluation_required = set(definitions.get("RuleEvaluation", {}).get("required", []))
    if not {
        "rule_version_id",
        "result",
        "reason_code",
        "evidence",
        "engine_version",
    }.issubset(evaluation_required):
        errors.append("coverage-decision RuleEvaluation lineage fields are incomplete")
    if "amount" in json.dumps(schema, sort_keys=True):
        errors.append("coverage-decision schema must not expose amount in v1")

    _validate_decision_example_ids(example, "$", errors)

    evaluations = example.get("evaluations")
    if isinstance(evaluations, list):
        for index, evaluation in enumerate(evaluations):
            if not isinstance(evaluation, dict):
                continue
            _validate_decision_example_evidence(
                evaluation.get("evidence"), f"$.evaluations[{index}].evidence", errors
            )
            if evaluation.get("engine_version") != example.get("engine_version"):
                errors.append("coverage-decision evaluation engine version must match the run")
    return errors


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
        *validate_rider_clause_rules_contract(),
        *validate_decision_contract(),
        *validate_web_generated_outputs(),
    ]
    if errors:
        print("\n".join(errors))
        return 1
    print(
        "contract checks passed (OpenAPI, analysis-job.v1, document ingestion, "
        "policy-ledger.v1, policy-candidate.v1, clause-search.v1, "
        "rider-clause-rules.v1, coverage-decision.v1, and generated Web contracts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

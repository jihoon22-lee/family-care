from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "packages/contracts"


def load_contract_checker() -> Any:
    """Load the repository checker when pytest's importlib mode omits the root."""

    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "familycare_document_contract_checker",
        ROOT / "scripts/check_document_contracts.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT_CHECKER = load_contract_checker()
validate_schema_instance = CONTRACT_CHECKER.validate_schema_instance


def load_json(relative_path: str) -> dict[str, Any]:
    path = CONTRACTS / relative_path
    assert path.is_file(), f"missing contract artifact: {relative_path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_document_request_has_relative_source_key_only() -> None:
    request = load_json("examples/document-ingestion.v1.json")
    schema = load_json("schemas/document-ingestion.v1.schema.json")

    assert request["schema_version"] == "1"
    assert request["source_key"] == "synthetic/policy-001.pdf"
    assert not request["source_key"].startswith("/")
    assert ".." not in Path(request["source_key"]).parts
    assert request["document_kind"] == "policy"
    assert "extractor_config" in request
    assert "password" not in request
    assert "absolute_path" not in request
    assert "raw_pdf" not in request
    assert "external_url" not in request
    assert "extractor_config_hash" not in schema["properties"]


def test_pre_intake_analysis_job_is_password_free_and_has_no_content_hash() -> None:
    schema = load_json("schemas/analysis-job.v1.schema.json")
    example = load_json("examples/analysis-job.v1.json")

    assert set(schema["required"]) == {
        "schema_version",
        "job_id",
        "document_id",
        "source_key",
        "settings",
        "extractor_config_hash",
        "state",
    }
    assert set(example) == set(schema["required"])
    assert "content_sha256" not in schema["properties"]
    assert "content_sha256" not in example
    assert "password" not in schema["properties"]
    assert "password" not in example
    assert example["source_key"] == "synthetic/policy-001.pdf"
    assert example["state"] == "queued"
    assert example["document_id"] == "00000000-0000-4000-8000-000000000003"


def test_generated_api_contract_types_are_checked_in() -> None:
    path = ROOT / "apps/api/src/familycare_api/documents/generated_contracts.py"
    assert path.is_file(), "missing generated API contract module"
    text = path.read_text(encoding="utf-8")

    for class_name in (
        "DocumentIngestionRequest",
        "DocumentStatus",
        "AnalysisJob",
        "ExtractionResult",
    ):
        assert f"class {class_name}(TypedDict)" in text
    assert "password" not in text
    assert "absolute_path" not in text
    assert "DocumentId = str" in text
    assert "document_id: DocumentId" in text


def test_document_status_is_versioned_and_referentially_complete() -> None:
    schema = load_json("schemas/document-ingestion.v1.schema.json")
    status = schema["$defs"]["DocumentStatus"]

    assert status["required"] == ["schema_version", "job_id", "document_id", "state"]
    assert status["properties"]["schema_version"] == {"const": "1", "type": "string"}
    assert status["properties"]["job_id"] == {"$ref": "#/$defs/JobId"}
    assert status["properties"]["document_id"] == {"$ref": "#/$defs/DocumentId"}


def test_document_and_job_schemas_reject_forbidden_request_mutations() -> None:
    document_schema = load_json("schemas/document-ingestion.v1.schema.json")
    document_example = load_json("examples/document-ingestion.v1.json")
    job_schema = load_json("schemas/analysis-job.v1.schema.json")
    job_example = load_json("examples/analysis-job.v1.json")

    mutations = [
        (document_schema, {**document_example, "source_key": "/outside/synthetic.pdf"}),
        (document_schema, {**document_example, "source_key": "synthetic/../policy.pdf"}),
        (document_schema, {**document_example, "source_key": "C:\\synthetic.pdf"}),
        (document_schema, {**document_example, "source_key": "\\\\server\\synthetic.pdf"}),
        (document_schema, {**document_example, "source_key": "synthetic/policy\n001.pdf"}),
        (document_schema, {**document_example, "extractor_config_hash": "synthetic"}),
        (document_schema, {**document_example, "password": True}),
        (document_schema, {**document_example, "job_id": "synthetic"}),
        (document_schema, {**document_example, "state": "queued"}),
        (job_schema, {**job_example, "content_sha256": "synthetic"}),
        (job_schema, {**job_example, "password": True}),
    ]
    for schema, mutation in mutations:
        assert validate_schema_instance(schema, mutation), mutation


def test_analysis_job_document_id_is_a_uuid_fk() -> None:
    schema = load_json("schemas/analysis-job.v1.schema.json")
    example = load_json("examples/analysis-job.v1.json")

    assert schema["properties"]["document_id"] == {"$ref": "#/$defs/DocumentId"}
    assert not validate_schema_instance(schema, example)
    invalid = {**example, "document_id": "synthetic-policy-001"}
    assert validate_schema_instance(schema, invalid)

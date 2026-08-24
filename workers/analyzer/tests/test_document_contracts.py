from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
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


def test_extraction_result_uses_versioned_quality_and_evidence_coordinates() -> None:
    schema = load_json("schemas/extraction-result.v1.schema.json")
    result = load_json("examples/extraction-result.v1.json")

    assert result["schema_version"] == "1"
    assert result["quality_rule_version"] == "quality-v1"
    assert result["pages"][0]["page_number"] == 1
    assert result["pages"][0]["quality"]["rule_version"] == "quality-v1"
    assert result["pages"][0]["quality"]["classification"] == "TEXT_SUFFICIENT"
    assert result["pages"][0]["blocks"][0]["reading_order"] == 0
    assert result["pages"][0]["blocks"][0]["bbox"] == [10.0, 20.0, 30.0, 40.0]
    assert result["pages"][0]["tables"][0]["cells"][0]["bbox"] == [10.0, 40.0, 30.0, 60.0]
    assert result["evidence"][0]["document_version_id"].endswith("0002")
    assert result["evidence"][0]["content_sha256"] == result["content_sha256"]
    assert result["evidence"][0]["review_state"] == "candidate"
    assert schema["$defs"]["BoundingBox"]["minItems"] == 4
    assert schema["$defs"]["BoundingBox"]["maxItems"] == 4
    assert schema["$defs"]["TextBlock"]["properties"]["reading_order"]["minimum"] == 0


def test_generated_worker_contract_types_are_checked_in() -> None:
    path = ROOT / "workers/analyzer/src/familycare_worker/generated_contracts.py"
    assert path.is_file(), "missing generated Worker contract module"
    text = path.read_text(encoding="utf-8")

    for class_name in (
        "AnalysisJob",
        "ExtractionResult",
        "ExtractionPage",
        "TextBlock",
        "ExtractionTable",
        "ExtractionCell",
        "Evidence",
    ):
        assert f"class {class_name}(TypedDict)" in text
    assert "password" not in text
    assert "absolute_path" not in text
    assert "document_id: DocumentId" in text


def test_extraction_schema_rejects_coordinate_and_shape_mutations() -> None:
    schema = load_json("schemas/extraction-result.v1.schema.json")
    example = load_json("examples/extraction-result.v1.json")

    bad_bbox = deepcopy(example)
    bad_bbox["pages"][0]["blocks"][0]["bbox"][0] = 10.0011
    bad_page = deepcopy(example)
    bad_page["pages"][0]["page_number"] = 0
    bad_order = deepcopy(example)
    bad_order["pages"][0]["blocks"][0]["reading_order"] = -1
    bad_extra = deepcopy(example)
    bad_extra["pages"][0]["blocks"][0]["unexpected"] = "synthetic"

    for mutation in (bad_bbox, bad_page, bad_order, bad_extra):
        assert validate_schema_instance(schema, mutation), mutation


def test_quality_v1_thresholds_have_exact_boundary_behavior() -> None:
    classify_quality = CONTRACT_CHECKER.classify_quality
    sufficient = {
        "non_whitespace_chars": 20,
        "alphanumeric_ratio": 0.25,
        "replacement_character_ratio": 0.05,
        "maximum_repeated_character_run": 20,
    }
    assert classify_quality(**sufficient) == "TEXT_SUFFICIENT"

    for field, value in (
        ("non_whitespace_chars", 19),
        ("alphanumeric_ratio", 0.249),
        ("replacement_character_ratio", 0.051),
        ("maximum_repeated_character_run", 21),
    ):
        mutation = {**sufficient, field: value}
        assert classify_quality(**mutation) == "OCR_REQUIRED"


def test_contract_checker_rejects_quality_classification_mismatch() -> None:
    document = load_json("examples/document-ingestion.v1.json")
    extraction = load_json("examples/extraction-result.v1.json")
    job = load_json("examples/analysis-job.v1.json")
    mutation = deepcopy(extraction)
    mutation["pages"][0]["quality"]["non_whitespace_chars"] = 19

    errors = CONTRACT_CHECKER.validate_examples(document, mutation, job)

    assert "extraction page quality classification violates quality-v1" in errors

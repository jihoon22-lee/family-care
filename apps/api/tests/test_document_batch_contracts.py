"""Transport-neutral contracts for encrypted document batches."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "packages/contracts"
EXAMPLE_ROOT = CONTRACT_ROOT / "examples"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
API_GENERATED = ROOT / "apps/api/src/familycare_api/documents/generated_batch_contracts.py"
WORKER_GENERATED = ROOT / "workers/analyzer/src/familycare_worker/generated_batch_contracts.py"

SYNTHETIC_FAMILY_MEMBER_ID = "00000000-0000-4000-8000-000000000004"
SYNTHETIC_BATCH_ID = "00000000-0000-4000-8000-000000000005"
SYNTHETIC_SOURCE_ID_A = "a" * 64
SYNTHETIC_SOURCE_ID_B = "b" * 64


def load_example(name: str) -> dict[str, Any]:
    value = json.loads((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_batch_request_has_one_family_member_and_no_secret_fields() -> None:
    request = load_example("document-batch.v1.json")

    assert request["schema_version"] == "1"
    assert request["family_member_id"] == SYNTHETIC_FAMILY_MEMBER_ID
    assert request["source_ids"] == [SYNTHETIC_SOURCE_ID_A, SYNTHETIC_SOURCE_ID_B]
    forbidden = {"password", "absolute_path", "raw_pdf", "archive_master_key"}
    assert forbidden.isdisjoint(request)
    assert all(field not in json.dumps(request) for field in forbidden)


def test_batch_status_contains_only_bounded_source_projection() -> None:
    status = load_example("document-batch-status.v1.json")

    assert status["schema_version"] == "1"
    assert status["batch_id"] == SYNTHETIC_BATCH_ID
    assert status["family_member_id"] == SYNTHETIC_FAMILY_MEMBER_ID
    assert {item["source_id"] for item in status["items"]} == {
        SYNTHETIC_SOURCE_ID_A,
        SYNTHETIC_SOURCE_ID_B,
    }
    forbidden = {
        "absolute_path",
        "archive_master_key",
        "password",
        "raw_pdf",
        "source_key",
    }
    assert forbidden.isdisjoint(status)
    assert all(field not in item for item in status["items"] for field in forbidden)
    assert all(len(item["display_label"]) <= 160 for item in status["items"])
    assert all(
        set(item["ocr_warning_codes"]) <= {"LOW_CONFIDENCE", "NO_TEXT_DETECTED"}
        and len(item["ocr_warning_codes"]) <= 8
        and len(item["ocr_warning_codes"]) == len(set(item["ocr_warning_codes"]))
        for item in status["items"]
    )
    assert all(0 <= item["ocr_pages_processed"] <= 500 for item in status["items"])


def test_batch_schemas_are_strict_and_define_stable_states() -> None:
    request_schema = json.loads(
        (SCHEMA_ROOT / "document-batch.v1.schema.json").read_text(encoding="utf-8")
    )
    status_schema = json.loads(
        (SCHEMA_ROOT / "document-batch-status.v1.schema.json").read_text(encoding="utf-8")
    )

    assert request_schema["additionalProperties"] is False
    assert status_schema["additionalProperties"] is False
    assert request_schema["$defs"]["SourceId"]["pattern"] == "^[a-f0-9]{64}$"
    assert status_schema["$defs"]["BatchState"]["enum"] == [
        "created",
        "running",
        "partial",
        "succeeded",
        "failed",
        "cancelled",
    ]
    assert status_schema["$defs"]["BatchItemState"]["enum"] == [
        "queued",
        "running",
        "succeeded",
        "password_required",
        "retryable_failed",
        "permanently_failed",
        "cancelled",
    ]
    label_pattern = "^[^\\u0000-\\u001f\\u007f-\\u009f]+$"
    assert (
        status_schema["$defs"]["DocumentBatchItem"]["properties"]["display_label"]["pattern"]
        == label_pattern
    )
    assert (
        status_schema["$defs"]["ImportSource"]["properties"]["display_label"]["pattern"]
        == label_pattern
    )


def test_batch_status_defines_bounded_ocr_progress_projection() -> None:
    status_schema = json.loads(
        (SCHEMA_ROOT / "document-batch-status.v1.schema.json").read_text(encoding="utf-8")
    )
    item = status_schema["$defs"]["DocumentBatchItem"]
    definitions = status_schema["$defs"]

    assert item["properties"]["ocr_state"] == {"$ref": "#/$defs/OcrState"}
    assert item["properties"]["ocr_pages_processed"] == {
        "minimum": 0,
        "maximum": 500,
        "type": "integer",
    }
    assert item["properties"]["ocr_warning_codes"] == {
        "items": {"$ref": "#/$defs/OcrWarningCode"},
        "maxItems": 8,
        "type": "array",
        "uniqueItems": True,
    }
    assert {"ocr_state", "ocr_pages_processed", "ocr_warning_codes"}.issubset(item["required"])
    assert definitions["OcrState"]["enum"] == [
        "pending",
        "native_only",
        "running",
        "completed",
        "warning",
        "failed",
    ]
    assert definitions["OcrWarningCode"]["enum"] == [
        "LOW_CONFIDENCE",
        "NO_TEXT_DETECTED",
    ]
    assert {
        "OCR_FAILED",
        "OCR_TIMEOUT",
        "OCR_UNAVAILABLE",
        "OCR_OUTPUT_LIMIT_EXCEEDED",
    }.issubset(set(definitions["BatchErrorCode"]["enum"]))


def test_batch_checker_and_generated_consumers_are_current() -> None:
    checker = ROOT / "scripts/check_batch_contracts.py"
    result = subprocess.run(
        [sys.executable, str(checker)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert API_GENERATED.read_bytes() == WORKER_GENERATED.read_bytes()


@pytest.mark.parametrize(
    ("name", "expected_title"),
    [
        ("document-batch.v1.schema.json", "DocumentBatchRequest"),
        ("document-batch-status.v1.schema.json", "DocumentBatchStatus"),
    ],
)
def test_batch_schema_titles_are_versioned(name: str, expected_title: str) -> None:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == expected_title

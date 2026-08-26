"""Transport-neutral contract tests for the separate OCR provenance layer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "packages/contracts/schemas/ocr-result.v1.schema.json"
EXAMPLE_PATH = ROOT / "packages/contracts/examples/ocr-result.v1.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_ocr_result_preserves_separate_layer_and_evidence() -> None:
    value = _load(EXAMPLE_PATH)

    assert value["schema_version"] == "1"
    assert value["source_layer"] == "ocr"
    assert value["language_codes"] == ["kor", "eng"]
    assert value["quality_rule_version"] == "quality-v1"
    assert value["pages"][0]["page_number"] == 1
    assert value["pages"][0]["selected_classification"] == "OCR_REQUIRED"
    assert value["pages"][0]["evidence"]["page_number"] == 1
    assert value["pages"][0]["blocks"][0]["source_layer"] == "ocr"

    serialized = json.dumps(value, sort_keys=True)
    for forbidden in (
        "absolute_path",
        "image_path",
        "password",
        "pdf_bytes",
        "source_key",
        "tsv_path",
    ):
        assert forbidden not in serialized


def test_ocr_schema_is_strict_and_bounds_local_engine_output() -> None:
    schema = _load(SCHEMA_PATH)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "OcrResult"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["source_layer"]["const"] == "ocr"
    assert schema["properties"]["language_codes"]["prefixItems"] == [
        {"const": "kor"},
        {"const": "eng"},
    ]
    assert schema["$defs"]["OcrPage"]["properties"]["rendered_dpi"]["const"] == 300
    assert schema["$defs"]["OcrBlock"]["properties"]["confidence"] == {
        "maximum": 100,
        "minimum": 0,
        "type": "number",
    }
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_ocr_checker_is_wired_into_the_root_contract_gate() -> None:
    checker = ROOT / "scripts/check_ocr_contracts.py"
    result = subprocess.run(
        [sys.executable, str(checker)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    root_checker = (ROOT / "scripts/check_contracts.py").read_text(encoding="utf-8")
    assert "check_ocr_contracts" in root_checker

#!/usr/bin/env python3
"""Generate shared local metadata proposal types and explicit source vocabulary."""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, cast


def _load_renderer() -> Callable[[list[dict[str, Any]]], str]:
    try:
        module = import_module("scripts.generate_document_contract_types")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        module = import_module("generate_document_contract_types")
    return cast(Callable[[list[dict[str, Any]]], str], module.render_module)


render_module = _load_renderer()

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "packages/contracts/schemas/document-metadata-proposal.v1.schema.json"
OUTPUTS = (
    ROOT / "apps/api/src/familycare_api/documents/generated_metadata.py",
    ROOT / "workers/analyzer/src/familycare_worker/generated_metadata.py",
)


def render() -> str:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    result = render_module([schema])
    for name, key in (
        ("METADATA_ROLE_TITLES", "x-role-titles"),
        ("METADATA_FIELD_LABELS", "x-field-labels"),
    ):
        result += f"\n\n{name}: dict[str, tuple[str, ...]] = {{\n"
        for field, labels in schema[key].items():
            result += f"    {json.dumps(field)}: (\n"
            for label in labels:
                result += f"        {json.dumps(label, ensure_ascii=False)},\n"
            result += "    ),\n"
        result += "}\n"
    return result


def validate() -> list[str]:
    expected = render()
    return [
        f"document metadata consumer drift: {path.relative_to(ROOT)}"
        for path in OUTPUTS
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]


def main() -> int:
    rendered = render()
    for path in OUTPUTS:
        path.write_text(rendered, encoding="utf-8")
    print("generated document metadata consumers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

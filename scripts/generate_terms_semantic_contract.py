#!/usr/bin/env python3
"""Generate strict API/Worker models from the neutral semantic knowledge schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "packages/contracts/schemas/terms-semantic-knowledge.v1.schema.json"
OUTPUTS = (
    ROOT / "apps/api/src/familycare_api/terms_knowledge/generated_contracts.py",
    ROOT / "workers/analyzer/src/familycare_worker/generated_terms_semantic.py",
)


def _type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "const" in schema:
        return f"Literal[{schema['const']!r}]"
    if "enum" in schema:
        return "Literal[" + ", ".join(repr(value) for value in schema["enum"]) + "]"
    if "anyOf" in schema:
        return " | ".join(_type(item) for item in schema["anyOf"])
    if schema["type"] == "array":
        return f"list[{_annotation(schema['items'])}]"
    return {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "null": "None",
    }[schema["type"]]


def _annotation(schema: dict[str, Any]) -> str:
    value = _type(schema)
    constraints = []
    for key, target in [
        ("minLength", "min_length"),
        ("maxLength", "max_length"),
        ("minItems", "min_length"),
        ("maxItems", "max_length"),
        ("minimum", "ge"),
        ("maximum", "le"),
        ("pattern", "pattern"),
    ]:
        if key in schema:
            constraints.append(f"{target}={schema[key]!r}")
    if constraints:
        return f"Annotated[{value}, Field({', '.join(constraints)})]"
    return value


def render() -> str:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    lines = [
        '"""Generated from terms-semantic-knowledge.v1.schema.json; do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Annotated, Literal",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
        "",
        "class SemanticContract(BaseModel):",
        '    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,'
        " allow_inf_nan=False, hide_input_in_errors=True)",
        "",
    ]
    definitions = {**schema["$defs"], schema["title"]: schema}
    for name, definition in sorted(definitions.items()):
        lines.extend(["", f"class {name}(SemanticContract):"])
        for key, value in sorted(definition["properties"].items()):
            lines.append(f"    {key}: {_annotation(value)}")
        lines.append("")
    lines.extend(["", *(f"{name}.model_rebuild()" for name in sorted(definitions)), ""])
    # Match the repository formatter deterministically without runtime code generation.
    import subprocess

    result = subprocess.run(
        ["ruff", "format", "--stdin-filename", "generated_contracts.py", "-"],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def validate() -> list[str]:
    expected = render()
    return [
        f"terms semantic consumer drift: {path.relative_to(ROOT)}"
        for path in OUTPUTS
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        errors = validate()
        print("\n".join(errors) if errors else "terms semantic consumers are current")
        return int(bool(errors))
    for path in OUTPUTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(), encoding="utf-8")
    print("generated terms semantic consumers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

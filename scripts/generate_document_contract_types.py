#!/usr/bin/env python3
"""Generate deterministic TypedDict consumers for document contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATHS = (
    ROOT / "packages/contracts/schemas/analysis-job.v1.schema.json",
    ROOT / "packages/contracts/schemas/document-ingestion.v1.schema.json",
    ROOT / "packages/contracts/schemas/extraction-result.v1.schema.json",
)
DEFAULT_API_OUTPUT = ROOT / "apps/api/src/familycare_api/documents/generated_contracts.py"
DEFAULT_WORKER_OUTPUT = ROOT / "workers/analyzer/src/familycare_worker/generated_contracts.py"

Schema = dict[str, Any]
ClassSpec = tuple[Schema, frozenset[str]]
LINE_LENGTH = 100


def load_schemas() -> list[Schema]:
    """Load the source schemas in a stable path order."""

    schemas: list[Schema] = []
    for path in SCHEMA_PATHS:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"schema must be an object: {path.relative_to(ROOT)}")
        schemas.append(value)
    return schemas


def _literal(value: Any) -> str:
    """Render a JSON scalar as a deterministic Python literal."""

    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    return repr(value)


def _python_type(schema: Schema) -> str:
    """Map the subset of JSON Schema used by the contracts to Python types."""

    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "const" in schema:
        return f"Literal[{_literal(schema['const'])}]"
    if "enum" in schema:
        values = ", ".join(_literal(value) for value in sorted(schema["enum"], key=str))
        return f"Literal[{values}]"
    if "anyOf" in schema:
        members = [_python_type(member) for member in schema["anyOf"]]
        return " | ".join(dict.fromkeys(members))

    schema_type = schema.get("type")
    if schema_type == "null":
        return "None"
    if schema_type == "array":
        return f"list[{_python_type(schema.get('items', {}))}]"
    if schema_type == "object":
        return "dict[str, object]"
    return {
        "boolean": "bool",
        "integer": "int",
        "number": "float",
        "string": "str",
    }.get(str(schema_type), "object")


def collect_classes(schemas: list[Schema]) -> dict[str, ClassSpec]:
    """Collect top-level and definition objects, rejecting conflicting names."""

    classes: dict[str, ClassSpec] = {}
    for schema in schemas:
        objects = [(str(schema["title"]), schema)]
        objects.extend(
            (str(name), definition) for name, definition in sorted(schema.get("$defs", {}).items())
        )
        for name, definition in objects:
            if definition.get("type") != "object":
                continue
            spec = (definition, frozenset(str(value) for value in definition.get("required", [])))
            previous = classes.get(name)
            if previous is not None:
                previous_json = json.dumps(previous[0], sort_keys=True)
                current_json = json.dumps(definition, sort_keys=True)
                if previous_json != current_json or previous[1] != spec[1]:
                    raise ValueError(f"conflicting schema definition: {name}")
                continue
            classes[name] = spec
    return classes


def collect_aliases(schemas: list[Schema]) -> dict[str, str]:
    """Collect non-object definitions such as the four-number bounding box."""

    aliases: dict[str, str] = {}
    for schema in schemas:
        for name, definition in sorted(schema.get("$defs", {}).items()):
            if definition.get("type") == "object":
                continue
            rendered = _python_type(definition)
            previous = aliases.get(str(name))
            if previous is not None and previous != rendered:
                raise ValueError(f"conflicting schema alias: {name}")
            aliases[str(name)] = rendered
    return aliases


def _render_field(field_name: str, annotation: str) -> list[str]:
    line = f"    {field_name}: {annotation}"
    if len(line) <= LINE_LENGTH or not annotation.startswith("Literal["):
        return [line]
    values = annotation[len("Literal[") : -1]
    if len(values) + 8 <= LINE_LENGTH:
        return [
            f"    {field_name}: Literal[",
            f"        {values}",
            "    ]",
        ]
    return [
        f"    {field_name}: Literal[",
        *(f"        {value}," for value in values.split(", ")),
        "    ]",
    ]


def render_module(schemas: list[Schema]) -> str:
    """Render both service consumers from the same sorted schema model."""

    classes = collect_classes(schemas)
    aliases = collect_aliases(schemas)
    names = sorted((*classes, *aliases))
    uses_not_required = any(
        field_name not in required
        for definition, required in classes.values()
        for field_name in definition.get("properties", {})
    )
    typing_imports = (
        "Literal, NotRequired, TypedDict" if uses_not_required else "Literal, TypedDict"
    )
    lines = [
        '"""Generated from packages/contracts/schemas; do not edit manually."""',
        "",
        "from __future__ import annotations",
        "",
        f"from typing import {typing_imports}",
        "",
        "__all__ = [",
        *(f'    "{name}",' for name in names),
        "]",
        "",
        "",
    ]
    for name in sorted(aliases):
        alias = aliases[name]
        if alias.startswith("Literal["):
            values = alias[len("Literal[") : -1].split(", ")
            lines.append(f"{name} = Literal[")
            lines.extend(f"    {value}," for value in values)
            lines.append("]")
        else:
            lines.append(f"{name} = {alias}")
        lines.extend(("", ""))
    for index, name in enumerate(names):
        if name in aliases:
            continue
        definition, required = classes[name]
        lines.append(f"class {name}(TypedDict):")
        properties = definition.get("properties", {})
        if not properties:
            lines.append("    pass")
        else:
            for field_name in sorted(properties):
                annotation = _python_type(properties[field_name])
                if field_name not in required:
                    annotation = f"NotRequired[{annotation}]"
                lines.extend(_render_field(str(field_name), annotation))
        if index != len(names) - 1:
            lines.extend(("", ""))
    return "\n".join(lines) + "\n"


def generate(
    api_output: Path = DEFAULT_API_OUTPUT, worker_output: Path = DEFAULT_WORKER_OUTPUT
) -> str:
    """Write both generated modules and return their shared content."""

    rendered = render_module(load_schemas())
    for output in (api_output, worker_output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-output", type=Path, default=DEFAULT_API_OUTPUT)
    parser.add_argument("--worker-output", type=Path, default=DEFAULT_WORKER_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generate(args.api_output, args.worker_output)
    print("generated document contract TypedDict modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

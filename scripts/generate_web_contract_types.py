#!/usr/bin/env python3
"""Generate deterministic TypeScript consumers for the Web contract boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "packages/contracts/openapi/familycare.v1.json"
CANDIDATE_SCHEMA_PATH = ROOT / "packages/contracts/schemas/policy-candidate.v1.schema.json"
DEFAULT_OUTPUT = ROOT / "apps/web/src/api/generated.ts"
HEADER = "// GENERATED FILE: do not edit; source packages/contracts/openapi/familycare.v1.json"

Schema = dict[str, Any]
PRINT_WIDTH = 80


def _display_path(path: Path) -> str:
    """Render repository-relative paths while keeping temporary paths usable."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> Schema:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path.relative_to(ROOT)}")
    return value


def load_contracts() -> tuple[Schema, Schema]:
    """Load canonical OpenAPI and the versioned candidate schema."""

    return load_json(OPENAPI_PATH), load_json(CANDIDATE_SCHEMA_PATH)


def _literal(value: Any) -> str:
    """Render a JSON scalar as a valid TypeScript literal."""

    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _reference_name(reference: str) -> str:
    return reference.rsplit("/", 1)[-1]


def _inline_object(schema: Schema) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return "Record<string, unknown>"
    required = {str(value) for value in schema.get("required", [])}
    members: list[str] = []
    for name in sorted(properties):
        optional = "" if name in required else "?"
        members.append(f"{_field_name(str(name))}{optional}: {_ts_type(properties[name])}")
    return "{ " + "; ".join(members) + " }"


def _ts_type(schema: Any) -> str:
    """Map the supported OpenAPI/JSON Schema subset to TypeScript."""

    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return _reference_name(str(schema["$ref"]))
    if "const" in schema:
        return _literal(schema["const"])
    if "enum" in schema:
        values = schema.get("enum", [])
        return " | ".join(_literal(value) for value in values) or "never"
    if "anyOf" in schema:
        members = [_ts_type(member) for member in schema["anyOf"]]
        return " | ".join(dict.fromkeys(members)) or "never"
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(str(_ts_type({"type": item})) for item in schema_type)
    if schema_type == "array":
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list) and prefix_items:
            return "[" + ", ".join(_ts_type(item) for item in prefix_items) + "]"
        return f"Array<{_ts_type(schema.get('items', {}))}>"
    if schema_type == "object":
        return _inline_object(schema)
    return {
        "boolean": "boolean",
        "integer": "number",
        "number": "number",
        "string": "string",
        "null": "null",
    }.get(str(schema_type), "unknown")


def _field_name(name: str) -> str:
    """Render an object key in the style used by the checked-in formatter."""

    if (
        name
        and (name[0].isalpha() or name[0] in "_$")
        and all(character.isalnum() or character in "_$" for character in name)
    ):
        return name
    return json.dumps(name, ensure_ascii=False)


def _render_union_alias(name: str, union: str) -> list[str]:
    """Render a union alias with stable line wrapping at the Web print width."""

    one_line = f"export type {name} = {union};"
    if len(one_line) <= PRINT_WIDTH:
        return [one_line]
    if len(union) <= PRINT_WIDTH:
        return [f"export type {name} =", f"  {union};"]
    members = union.split(" | ")
    return [
        f"export type {name} =",
        *(f"  | {member}" for member in members[:-1]),
        f"  | {members[-1]};",
    ]


def _render_property(name: str, optional: bool, type_name: str, terminator: str = ";") -> list[str]:
    """Render one interface/object property with deterministic type wrapping."""

    rendered_name = f"{_field_name(name)}{'?' if optional else ''}"
    one_line = f"{rendered_name}: {type_name}{terminator}"
    if " | " not in type_name or len(one_line) + 2 <= PRINT_WIDTH:
        return [one_line]
    if len(type_name) <= PRINT_WIDTH:
        return [f"{rendered_name}:", f"  {type_name}{terminator}"]
    members = type_name.split(" | ")
    return [
        f"{rendered_name}:",
        *(f"  | {member}" for member in members[:-1]),
        f"  | {members[-1]}{terminator}",
    ]


def _collect_component_schemas(openapi: Schema, candidate_schema: Schema) -> dict[str, Schema]:
    """Collect named OpenAPI components and candidate schema definitions."""

    components: dict[str, Schema] = {}
    openapi_components = openapi.get("components", {}).get("schemas", {})
    if isinstance(openapi_components, dict):
        for name, schema in sorted(openapi_components.items()):
            if isinstance(schema, dict):
                components[str(name)] = schema

    root_title = candidate_schema.get("title")
    if isinstance(root_title, str):
        components[root_title] = candidate_schema
    definitions = candidate_schema.get("$defs", {})
    if isinstance(definitions, dict):
        for name, schema in sorted(definitions.items()):
            if isinstance(schema, dict):
                # Once Task 3 registers candidate routes, FastAPI's component is
                # canonical; before then this schema supplies the Web-only type.
                components.setdefault(str(name), schema)
    return components


def _render_component(name: str, schema: Schema) -> list[str]:
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        required = {str(value) for value in schema.get("required", [])}
        lines = [f"export interface {name} {{"]
        for field_name in sorted(schema["properties"]):
            field_schema = schema["properties"][field_name]
            lines.extend(
                f"  {line}"
                for line in _render_property(
                    str(field_name), field_name not in required, _ts_type(field_schema)
                )
            )
        lines.append("}")
        return lines
    return _render_union_alias(name, _ts_type(schema))


def _path_operations(openapi: Schema) -> tuple[list[str], list[tuple[str, str, str]]]:
    paths = openapi.get("paths", {})
    if not isinstance(paths, dict):
        return [], []
    path_names = sorted(str(path) for path in paths)
    operations: list[tuple[str, str, str]] = []
    for path in path_names:
        path_item = paths[path]
        if not isinstance(path_item, dict):
            continue
        for method in sorted(
            str(key).lower()
            for key in path_item
            if str(key).lower()
            in {
                "delete",
                "get",
                "patch",
                "post",
                "put",
            }
        ):
            operation = path_item[method]
            operation_id = (
                str(operation.get("operationId", "")) if isinstance(operation, dict) else ""
            )
            operations.append((method.upper(), path, operation_id))
    return path_names, operations


def _error_code_aliases(openapi: Schema, candidate_schema: Schema) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    schemas = openapi.get("components", {}).get("schemas", {})
    if isinstance(schemas, dict):
        for name, schema in sorted(schemas.items()):
            if not isinstance(schema, dict):
                continue
            error_code = schema.get("properties", {}).get("error_code", {})
            values = error_code.get("enum") if isinstance(error_code, dict) else None
            if isinstance(values, list) and values:
                aliases[f"{name}ErrorCode"] = [str(value) for value in values]
    return aliases


def render_module(openapi: Schema | None = None, candidate_schema: Schema | None = None) -> str:
    """Render the checked-in TypeScript module without filesystem side effects."""

    if openapi is None or candidate_schema is None:
        loaded_openapi, loaded_candidate = load_contracts()
        openapi = loaded_openapi if openapi is None else openapi
        candidate_schema = loaded_candidate if candidate_schema is None else candidate_schema

    paths, operations = _path_operations(openapi)
    components = _collect_component_schemas(openapi, candidate_schema)
    error_aliases = _error_code_aliases(openapi, candidate_schema)
    lines = [
        HEADER,
        "// Candidate schemas remain available before Task 3 registers their API routes.",
        "",
        "export const API_PATHS = [",
        *(f"  {_literal(path)}," for path in paths),
        "] as const;",
        "",
        "export type ApiPath = (typeof API_PATHS)[number];",
        "",
        "export const API_OPERATIONS = [",
        *(
            line
            for method, path, operation_id in operations
            for line in (
                "  {",
                f"    method: {_literal(method)},",
                f"    path: {_literal(path)},",
                *(
                    [
                        "    operationId:",
                        f"      {_literal(operation_id)},",
                    ]
                    if len(f"    operationId: {_literal(operation_id)},") > PRINT_WIDTH
                    else [f"    operationId: {_literal(operation_id)},"]
                ),
                "  },",
            )
        ),
        "] as const;",
        "",
        "export type ApiOperation = (typeof API_OPERATIONS)[number];",
        "",
    ]
    for alias_name in sorted(error_aliases):
        values = error_aliases[alias_name]
        lines.extend(
            _render_union_alias(alias_name, " | ".join(_literal(value) for value in values))
        )
        lines.append("")
    for name in sorted(components):
        lines.extend(_render_component(name, components[name]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(output: Path = DEFAULT_OUTPUT) -> str:
    """Write the generated Web consumer and return its content."""

    rendered = render_module()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail when the checked-in output is stale"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rendered = render_module()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"generated Web contract is stale: {_display_path(args.output)}")
            return 1
        print("Web contract generation check passed")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {_display_path(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

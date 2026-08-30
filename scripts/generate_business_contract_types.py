#!/usr/bin/env python3
"""Generate the deterministic API consumer for business contracts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATHS = (
    ROOT / "packages/contracts/schemas/policy-ledger.v1.schema.json",
    ROOT / "packages/contracts/schemas/policy-candidate.v1.schema.json",
    ROOT / "packages/contracts/schemas/private-knowledge.v1.schema.json",
)
DEFAULT_OUTPUT = ROOT / "apps/api/src/familycare_api/contracts/generated_business.py"


def _load_render_module() -> Callable[[list[dict[str, Any]]], str]:
    """Load the shared deterministic renderer in package or script context."""

    try:
        module = import_module("scripts.generate_document_contract_types")
    except ModuleNotFoundError:  # pragma: no cover - direct script execution path
        module = import_module("generate_document_contract_types")
    return cast(Callable[[list[dict[str, Any]]], str], module.render_module)


render_module = _load_render_module()


def load_schemas(paths: tuple[Path, ...] = SCHEMA_PATHS) -> list[dict[str, Any]]:
    """Load canonical business-contract schemas in a stable order."""

    schemas: list[dict[str, Any]] = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"schema must be an object: {path.relative_to(ROOT)}")
        schemas.append(value)
    return schemas


def generate(output: Path = DEFAULT_OUTPUT) -> str:
    """Render and write the business contract consumer deterministically."""

    rendered = render_module(load_schemas())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generate(args.output)
    print(f"generated {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

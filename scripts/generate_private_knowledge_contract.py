#!/usr/bin/env python3
"""Generate the deterministic private-knowledge detail JSON Schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from familycare_api.private_knowledge.schemas import KnowledgeContractDetailResponse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "packages/contracts/schemas/private-knowledge.v1.schema.json"
SCHEMA_ID = "https://familycare.local/contracts/private-knowledge.v1.schema.json"


def render_schema() -> str:
    """Render the strict transport schema from the canonical response model."""

    generated: dict[str, Any] = KnowledgeContractDetailResponse.model_json_schema(
        mode="serialization"
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        **generated,
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def generate(output: Path = DEFAULT_OUTPUT) -> str:
    """Write the deterministic schema and return its content."""

    rendered = render_schema()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rendered = render_schema()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print("private-knowledge contract is stale")
            return 1
        print("private-knowledge contract is current")
        return 0
    generate(args.output)
    print(f"generated {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

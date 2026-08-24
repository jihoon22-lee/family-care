#!/usr/bin/env python3
"""Validate FamilyCare container definitions without building images."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "infra/compose/compose.yaml"
ENV_PATH = ROOT / ".env.example"
EXPECTED_SERVICES = {"api", "db", "web", "worker"}
DOCKERFILES = {
    "web": (ROOT / "infra/containers/web.Dockerfile", "node:24.19.0-alpine", "1.31.2-alpine3.23"),
    "api": (ROOT / "infra/containers/api.Dockerfile", "python:3.14.7-slim", "uv:0.12.5"),
    "worker": (
        ROOT / "infra/containers/worker.Dockerfile",
        "python:3.14.7-slim",
        "uv:0.12.5",
    ),
}


def final_stage(dockerfile: str) -> str:
    """Return the final Dockerfile stage."""

    starts = [match.start() for match in re.finditer(r"(?m)^FROM\s+", dockerfile)]
    return dockerfile[starts[-1] :] if starts else ""


def validate_dockerfile(name: str, path: Path, required_images: tuple[str, ...]) -> list[str]:
    """Validate one Dockerfile's pinned inputs and unprivileged runtime."""

    relative = path.relative_to(ROOT)
    if not path.is_file():
        return [f"missing container definition: {relative}"]

    content = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if re.search(r"(?mi)^COPY\s+(?:--\S+\s+)*\.\s+\.$", content):
        errors.append(f"{relative}: repository-wide COPY . . is forbidden")
    copy_lines = "\n".join(
        line for line in content.splitlines() if line.lstrip().upper().startswith("COPY ")
    )
    for forbidden_source in (".env", ".git", "fixtures"):
        if forbidden_source in copy_lines:
            errors.append(f"{relative}: COPY must not include {forbidden_source}")
    for image in required_images:
        if image not in content:
            errors.append(f"{relative}: exact image reference {image!r} is required")

    runtime = final_stage(content)
    user_match = re.search(r"(?mi)^USER\s+([^\s]+)", runtime)
    if user_match is None or user_match.group(1) in {"0", "root", "0:0"}:
        errors.append(f"{relative}: final stage must set a non-root USER")
    if name == "web" and "USER 101:101" not in runtime:
        errors.append(f"{relative}: Web runtime must use UID 101")
    return errors


def validate_compose() -> list[str]:
    """Validate Compose syntax and its required service set."""

    if not COMPOSE_PATH.is_file():
        return [f"missing container definition: {COMPOSE_PATH.relative_to(ROOT)}"]

    command = [
        "docker",
        "compose",
        "--env-file",
        str(ENV_PATH),
        "-f",
        str(COMPOSE_PATH),
        "config",
        "--services",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return [f"Compose validation failed: {detail}"]

    services = set(result.stdout.splitlines())
    if services != EXPECTED_SERVICES:
        return [f"Compose services must be {sorted(EXPECTED_SERVICES)}, found {sorted(services)}"]
    return []


def main() -> int:
    """Run all static container-definition checks."""

    errors: list[str] = []
    for name, (path, *required_images) in DOCKERFILES.items():
        errors.extend(validate_dockerfile(name, path, tuple(required_images)))
    errors.extend(validate_compose())

    if errors:
        print("\n".join(errors))
        return 1
    print("container definitions passed (3 images, 4 Compose services)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

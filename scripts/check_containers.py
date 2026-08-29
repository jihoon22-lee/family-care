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
    "web": ROOT / "infra/containers/web.Dockerfile",
    "api": ROOT / "infra/containers/api.Dockerfile",
    "worker": ROOT / "infra/containers/worker.Dockerfile",
}
_VERSION_PART = r"(?:0|[1-9][0-9]*)"
IMAGE_REQUIREMENTS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "web": (
        (
            "fully pinned Node 24 Alpine tag",
            re.compile(rf"node:24\.{_VERSION_PART}\.{_VERSION_PART}-alpine"),
        ),
        (
            "approved nginx runtime tag",
            re.compile(r"nginxinc/nginx-unprivileged:1\.31\.2-alpine3\.23"),
        ),
    ),
    "api": (
        (
            "fully pinned uv 0.12 tag",
            re.compile(rf"ghcr\.io/astral-sh/uv:0\.12\.{_VERSION_PART}"),
        ),
        (
            "fully pinned Python 3.14 slim tag",
            re.compile(rf"python:3\.14\.{_VERSION_PART}-slim"),
        ),
        (
            "fully pinned Python 3.14 slim tag",
            re.compile(rf"python:3\.14\.{_VERSION_PART}-slim"),
        ),
    ),
    "worker": (
        (
            "fully pinned uv 0.12 tag",
            re.compile(rf"ghcr\.io/astral-sh/uv:0\.12\.{_VERSION_PART}"),
        ),
        (
            "fully pinned Python 3.14 slim tag",
            re.compile(rf"python:3\.14\.{_VERSION_PART}-slim"),
        ),
        (
            "fully pinned Python 3.14 slim tag",
            re.compile(rf"python:3\.14\.{_VERSION_PART}-slim"),
        ),
    ),
}
FROM_IMAGE_PATTERN = re.compile(r"(?mi)^FROM[ \t]+(?P<image>\S+)(?:[ \t]+AS[ \t]+\S+)?[ \t]*$")


def final_stage(dockerfile: str) -> str:
    """Return the final Dockerfile stage."""

    starts = [match.start() for match in re.finditer(r"(?m)^FROM\s+", dockerfile)]
    return dockerfile[starts[-1] :] if starts else ""


def validate_image_references(name: str, dockerfile: str) -> list[str]:
    """Validate the ordered image stages against an approved pinned-tag policy."""

    requirements = IMAGE_REQUIREMENTS.get(name)
    if requirements is None:
        return [f"no approved image policy is configured for {name!r}"]

    images = tuple(match.group("image") for match in FROM_IMAGE_PATTERN.finditer(dockerfile))
    if len(images) != len(requirements):
        return [f"expected {len(requirements)} approved image stages, found {len(images)}"]

    errors: list[str] = []
    for index, (image, (description, pattern)) in enumerate(
        zip(images, requirements, strict=True), start=1
    ):
        if pattern.fullmatch(image) is None:
            errors.append(f"stage {index} image {image!r} must use a {description}")
    return errors


def validate_dockerfile(name: str, path: Path) -> list[str]:
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
    errors.extend(f"{relative}: {error}" for error in validate_image_references(name, content))

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
    for name, path in DOCKERFILES.items():
        errors.extend(validate_dockerfile(name, path))
    errors.extend(validate_compose())

    if errors:
        print("\n".join(errors))
        return 1
    print("container definitions passed (3 images, 4 Compose services)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

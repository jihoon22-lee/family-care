#!/usr/bin/env python3
"""Render public GitHub Release notes from one validated CHANGELOG section."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import NoReturn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_audit import ReleaseImageDigest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{0,38})/[a-z0-9](?:[a-z0-9_.-]{0,99})$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_HEADER_RE = re.compile(
    r"^## \[(?P<label>[^\]]+)\](?: - (?P<released>[^\n]+))?$",
    re.MULTILINE,
)
CATEGORY_RE = re.compile(r"^### (?P<category>[^\n]+)$", re.MULTILINE)
ATX_HEADING_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<marker>#{1,6})(?:[ \t]+.*)?$",
    re.MULTILINE,
)
ALLOWED_CATEGORIES = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
EXPECTED_COMPONENTS = ("web", "api", "worker")
EVIDENCE_SCHEMA = "release-image-evidence.v1"


class ReleaseNotesError(ValueError):
    """A stable, public-safe release-note validation failure."""


@dataclass(frozen=True)
class ReleaseNotesEvidence:
    version: str
    commit_sha: str
    repository: str
    workflow_url: str
    images: tuple[ReleaseImageDigest, ...]


def _validate_version(version: str) -> None:
    if VERSION_RE.fullmatch(version) is None:
        raise ReleaseNotesError("invalid semantic version")


def _validate_section(section: str) -> None:
    if r"\n" in section:
        raise ReleaseNotesError("literal backslash-n is not allowed")

    for heading in ATX_HEADING_RE.finditer(section):
        if heading.group("indent") or CATEGORY_RE.fullmatch(heading.group(0)) is None:
            raise ReleaseNotesError("unsupported Markdown heading")

    categories = list(CATEGORY_RE.finditer(section))
    if not categories:
        raise ReleaseNotesError("version section has no categories")
    if section[: categories[0].start()].strip():
        raise ReleaseNotesError("content before first category")

    previous_index = -1
    seen: set[str] = set()
    for index, match in enumerate(categories):
        category = match.group("category")
        if category not in ALLOWED_CATEGORIES:
            raise ReleaseNotesError(f"unsupported category: {category}")
        if category in seen:
            raise ReleaseNotesError(f"duplicate category: {category}")
        category_index = ALLOWED_CATEGORIES.index(category)
        if category_index <= previous_index:
            raise ReleaseNotesError(f"category out of order: {category}")
        previous_index = category_index
        seen.add(category)

        body_start = match.end()
        body_end = categories[index + 1].start() if index + 1 < len(categories) else len(section)
        category_body = section[body_start:body_end]
        if re.search(r"^- \S", category_body, re.MULTILINE) is None:
            raise ReleaseNotesError(f"empty category: {category}")


def extract_changelog_section(changelog: str, version: str) -> str:
    """Return one validated version body without its ``## [version]`` header."""

    _validate_version(version)
    headers = list(RELEASE_HEADER_RE.finditer(changelog))
    matches = [match for match in headers if match.group("label") == version]
    if not matches:
        raise ReleaseNotesError("version section not found")
    if len(matches) != 1:
        raise ReleaseNotesError("duplicate version section")

    selected = matches[0]
    released = selected.group("released")
    if released is None or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", released) is None:
        raise ReleaseNotesError("invalid release date")
    try:
        date.fromisoformat(released)
    except ValueError as exc:
        raise ReleaseNotesError("invalid release date") from exc

    selected_index = headers.index(selected)
    body_start = selected.end()
    if changelog[body_start : body_start + 1] == "\n":
        body_start += 1
    body_end = (
        headers[selected_index + 1].start() if selected_index + 1 < len(headers) else len(changelog)
    )
    section = changelog[body_start:body_end].strip("\n") + "\n"
    _validate_section(section)
    return section


def _validate_evidence(evidence: ReleaseNotesEvidence) -> None:
    _validate_version(evidence.version)
    if COMMIT_RE.fullmatch(evidence.commit_sha) is None:
        raise ReleaseNotesError("invalid commit SHA")
    if REPOSITORY_RE.fullmatch(evidence.repository) is None:
        raise ReleaseNotesError("invalid repository")
    expected_url = re.compile(
        rf"^https://github\.com/{re.escape(evidence.repository)}/actions/runs/[0-9]+$"
    )
    if expected_url.fullmatch(evidence.workflow_url) is None:
        raise ReleaseNotesError("invalid workflow URL")
    if tuple(item.component for item in evidence.images) != EXPECTED_COMPONENTS:
        raise ReleaseNotesError("image evidence must contain ordered web, api, worker components")
    if any(DIGEST_RE.fullmatch(item.digest) is None for item in evidence.images):
        raise ReleaseNotesError("invalid image digest")
    if len({item.digest for item in evidence.images}) != len(EXPECTED_COMPONENTS):
        raise ReleaseNotesError("release evidence requires distinct image digests")


def render_release_notes(section: str, evidence: ReleaseNotesEvidence) -> str:
    """Combine an exact CHANGELOG section with bounded public release evidence."""

    _validate_section(section)
    _validate_evidence(evidence)
    image_lines = "\n".join(
        f"- `{item.component}`: `ghcr.io/{evidence.repository}-{item.component}@{item.digest}`"
        for item in evidence.images
    )
    return (
        "## Changes\n\n"
        f"{section}\n"
        "## Release evidence\n\n"
        f"- Commit: `{evidence.commit_sha}`\n"
        f"- Workflow: {evidence.workflow_url}\n"
        f"{image_lines}\n\n"
        "## Privacy and deployment boundary\n\n"
        "This release publishes the verified Web, API, and Worker images to GHCR. "
        "It does not by itself deploy FamilyCare or verify private insurance documents.\n"
    )


def _load_image_evidence(
    path: Path,
    version: str,
    commit_sha: str,
) -> tuple[ReleaseImageDigest, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseNotesError("image evidence is unreadable") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "version",
        "commit_sha",
        "images",
    }:
        raise ReleaseNotesError("invalid image evidence shape")
    if raw["schema_version"] != EVIDENCE_SCHEMA:
        raise ReleaseNotesError("invalid image evidence schema")
    if raw["version"] != version or raw["commit_sha"] != commit_sha:
        raise ReleaseNotesError("image evidence identity mismatch")
    raw_images = raw["images"]
    if not isinstance(raw_images, list):
        raise ReleaseNotesError("invalid image evidence shape")

    images: list[ReleaseImageDigest] = []
    for item in raw_images:
        if not isinstance(item, dict) or set(item) != {"component", "digest"}:
            raise ReleaseNotesError("invalid image evidence shape")
        component = item["component"]
        digest = item["digest"]
        if not isinstance(component, str) or not isinstance(digest, str):
            raise ReleaseNotesError("invalid image evidence shape")
        images.append(ReleaseImageDigest(component=component, digest=digest))
    return tuple(images)


def _validated_output_path(path: Path) -> Path:
    if not path.is_absolute():
        raise ReleaseNotesError("output path must be absolute")
    if os.path.lexists(path):
        raise ReleaseNotesError("output path already exists")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise ReleaseNotesError("output path must be outside the repository")
    if not resolved.parent.is_dir():
        raise ReleaseNotesError("output parent does not exist")
    return resolved


def _write_private_new_file(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as output:
            file_descriptor = -1
            output.write(content)
    except BaseException:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        path.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-url", required=True)
    parser.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument("--image-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _fail(message: str) -> NoReturn:
    print(f"release-notes-error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = _validated_output_path(args.output)
        changelog = args.changelog.read_text(encoding="utf-8")
        section = extract_changelog_section(changelog, args.version)
        images = _load_image_evidence(args.image_evidence, args.version, args.commit_sha)
        evidence = ReleaseNotesEvidence(
            version=args.version,
            commit_sha=args.commit_sha,
            repository=args.repository,
            workflow_url=args.workflow_url,
            images=images,
        )
        notes = render_release_notes(section, evidence)
        _write_private_new_file(output, notes)
    except ReleaseNotesError as exc:
        _fail(str(exc))
    except OSError, UnicodeError:
        _fail("release input or output is unavailable")
    print("release-notes-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

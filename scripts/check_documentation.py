#!/usr/bin/env python3
"""Validate the required FamilyCare documentation contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_HEADINGS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "# FamilyCare",
        "## Privacy boundary",
        "## Quick start",
    ),
    "AGENTS.md": (
        "# FamilyCare development instructions",
        "## Non-negotiable privacy rules",
        "## Branch and commit conventions",
        "## Required verification",
    ),
    "CHANGELOG.md": ("# Changelog", "## [Unreleased]"),
    "CONTRIBUTING.md": ("# Contributing to FamilyCare",),
    "SECURITY.md": ("# Security policy",),
    "docs/architecture.md": (
        "# FamilyCare architecture",
        "## Trust boundaries",
        "## Runtime components",
    ),
    "docs/guide.md": (
        "# FamilyCare guide",
        "## Local development",
        "## Safe data handling",
    ),
    "docs/glossary.md": ("# FamilyCare glossary",),
    "docs/design/project-foundation.md": ("# FamilyCare 프로젝트 기반 설계",),
    "docs/design/data-model.md": ("# Data model design",),
    "docs/design/pdf-ingestion.md": ("# PDF ingestion design",),
    "docs/design/coverage-decision-engine.md": ("# Coverage decision engine design",),
    "docs/design/security-privacy.md": ("# Security and privacy design",),
    "docs/design/test-strategy.md": ("# Test strategy",),
    "docs/adr/0001-modular-monolith.md": ("# ADR 0001: Modular monolith",),
    "docs/adr/0002-public-repository-data-boundary.md": (
        "# ADR 0002: Public repository data boundary",
    ),
    "docs/adr/0003-postgresql-job-queue.md": ("# ADR 0003: PostgreSQL job queue",),
    "docs/adr/0004-evidence-first-tristate-decisions.md": (
        "# ADR 0004: Evidence-first tri-state decisions",
    ),
    "docs/adr/0005-ghcr-only-continuous-delivery.md": (
        "# ADR 0005: GHCR-only continuous delivery",
    ),
    "docs/plan/000-project-roadmap.md": ("# FamilyCare 프로젝트 로드맵",),
    "docs/plan/001-project-foundation.md": ("# Project Foundation Implementation Plan",),
}

UNFINISHED_MARKERS = ("T" + "BD", "T" + "ODO", "FIX" + "ME")


def validate_document(path: Path, headings: tuple[str, ...]) -> list[str]:
    """Return contract violations for one Markdown document."""

    if not path.is_file():
        return [f"missing required document: {path.relative_to(ROOT)}"]

    text = path.read_text(encoding="utf-8")
    errors = [
        f"{path.relative_to(ROOT)}: missing heading {heading}"
        for heading in headings
        if heading not in text
    ]
    errors.extend(
        f"{path.relative_to(ROOT)}: contains unfinished marker {marker}"
        for marker in UNFINISHED_MARKERS
        if marker in text
    )
    return errors


def main() -> int:
    """Validate every required document and return a process exit code."""

    errors: list[str] = []
    for relative_path, headings in REQUIRED_HEADINGS.items():
        errors.extend(validate_document(ROOT / relative_path, headings))

    if errors:
        print("\n".join(errors))
        return 1

    print(f"documentation contract passed ({len(REQUIRED_HEADINGS)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

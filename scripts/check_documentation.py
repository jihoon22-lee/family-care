#!/usr/bin/env python3
"""Validate the required FamilyCare documentation contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_EVIDENCE_PATH = ROOT / "docs/release/v0.1.0-verification.md"

RELEASE_EVIDENCE_HEADINGS = (
    "# FamilyCare v0.1.0 verification boundary",
    "## Release scope",
    "## Merged prerequisites",
    "## Pre-tag verification status",
    "## Immutable image slots",
    "## Private runtime acceptance",
    "## Unverified boundaries",
    "## Post-tag recording fields",
)
IMAGE_ROW_PATTERN = re.compile(
    r"(?m)^\|\s*(?P<label>Web|API|Worker)\s*\|\s*"
    r"`ghcr\.io/<repository>-(?P<component>web|api|worker):0\.1\.0`\s*\|\s*"
    r"`PENDING`\s*\|\s*"
    r"`ghcr\.io/<repository>-(?P=component):sha-<12 lowercase hexadecimal characters>`\s*\|\s*"
    r"`PENDING`\s*\|\s*$"
)
DIGEST_FORMAT = "sha256:<64 lowercase hexadecimal characters>"
REQUIRED_PENDING_FIELDS = frozenset(
    {
        "tag-workflow-run",
        "web-version-digest",
        "web-commit-digest",
        "api-version-digest",
        "api-commit-digest",
        "worker-version-digest",
        "worker-commit-digest",
    }
)
PRIVATE_EVIDENCE_PATTERNS = (
    ("absolute-path", re.compile(r"(?i)(?:/mnt/|/home/|/tmp/|[A-Za-z]:\\|\\\\wsl\$)")),
    ("drive-identifier", re.compile(r"(?i)drive\.google|docs\.google")),
    ("credential-value", re.compile(r"(?i)(?:sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,})")),
    ("assigned-secret", re.compile(r"(?i)\b(?:password|secret|token)\s*[:=]\s*\S+")),
)


@dataclass(frozen=True)
class ReleaseEvidence:
    """Strict, non-sensitive shape of the pre-tag release evidence document."""

    image_components: tuple[str, ...]
    digest_format: str
    pending_fields: frozenset[str]
    statuses: frozenset[str]
    no_latest_tag: bool
    no_cloud_run: bool
    private_data_findings: tuple[str, ...]


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
    "docs/design/v0.1-product.md": ("# FamilyCare v0.1 product design",),
    "docs/design/ai-document-analysis.md": ("# AI document analysis design",),
    "docs/design/authentication.md": ("# Local authentication design",),
    "docs/design/claim-workflow.md": ("# Claim workflow design",),
    "docs/design/clause-linking-search.md": ("# Clause linking and search design",),
    "docs/design/event-result-pwa.md": ("# Event and result PWA design",),
    "docs/design/insurance-document-inventory.md": (
        "# Insurance document inventory design",
    ),
    "docs/design/policy-ledger.md": ("# Policy ledger design",),
    "docs/design/private-data-runtime.md": ("# Private data and local runtime design",),
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
    "docs/plan/003-v0.1-implementation-index.md": ("# FamilyCare v0.1 Implementation Plan",),
    "docs/plan/004-policy-ledger.md": ("# Policy Ledger Implementation Plan",),
    "docs/plan/005-policy-candidate-review.md": ("# Policy Candidate Review Implementation Plan",),
    "docs/plan/006-clause-search.md": ("# Clause Search Implementation Plan",),
    "docs/plan/007-rider-clause-rules.md": ("# Rider-Clause Rules Implementation Plan",),
    "docs/plan/008-coverage-decision-engine.md": (
        "# Coverage Decision Engine Implementation Plan",
    ),
    "docs/plan/009-benefit-calculations.md": ("# Benefit Calculations Implementation Plan",),
    "docs/plan/010-event-result-pwa.md": ("# Event and Result PWA Implementation Plan",),
    "docs/plan/011-claim-workflow.md": ("# Claim Workflow Implementation Plan",),
    "docs/plan/012-local-authentication.md": ("# Local Authentication Implementation Plan",),
    "docs/plan/013-encrypted-document-import.md": (
        "# Encrypted Document Import Implementation Plan",
    ),
    "docs/plan/014-selective-ocr.md": ("# Selective OCR Implementation Plan",),
    "docs/plan/014a-private-import-reliability.md": (
        "# Private Import Reliability Implementation Plan",
    ),
    "docs/plan/015-private-local-runtime.md": ("# Private Local Runtime Implementation Plan",),
    "docs/plan/016-v0.1-release.md": ("# FamilyCare v0.1 Release Plan",),
    "docs/plan/017-private-policy-structuring.md": (
        "# Private policy structuring implementation plan",
    ),
    "docs/plan/018-insurance-document-inventory.md": (
        "# Insurance Document Inventory Implementation Plan",
    ),
    "docs/release/v0.1.0-verification.md": RELEASE_EVIDENCE_HEADINGS,
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


def parse_release_evidence(path: Path) -> ReleaseEvidence:
    """Parse the pre-tag evidence shape without accepting private values or fake results."""

    if not path.is_file():
        raise ValueError("evidence document is missing")
    text = path.read_text(encoding="utf-8")
    missing_headings = [heading for heading in RELEASE_EVIDENCE_HEADINGS if heading not in text]
    if missing_headings:
        raise ValueError(f"missing evidence headings: {', '.join(missing_headings)}")

    image_matches = tuple(IMAGE_ROW_PATTERN.finditer(text))
    if len(image_matches) != 3:
        raise ValueError("evidence must contain exactly three immutable image slots")
    image_components = tuple(match.group("component") for match in image_matches)
    image_labels = tuple(match.group("label").lower() for match in image_matches)
    if image_components != ("web", "api", "worker") or image_labels != image_components:
        raise ValueError("evidence image slots must be Web, API, Worker in order")

    if f"`{DIGEST_FORMAT}`" not in text:
        raise ValueError("evidence must state the OCI digest format")
    pending_fields = frozenset(
        field for field in REQUIRED_PENDING_FIELDS if f"`{field}`: `PENDING`" in text
    )
    if pending_fields != REQUIRED_PENDING_FIELDS:
        missing = ", ".join(sorted(REQUIRED_PENDING_FIELDS - pending_fields))
        raise ValueError(f"evidence must keep future fields pending: {missing}")

    statuses = frozenset(re.findall(r"\b(?:PASSED|FAILED|UNVERIFIED|PENDING)\b", text))
    required_statuses = frozenset({"PASSED", "FAILED", "UNVERIFIED", "PENDING"})
    if not required_statuses <= statuses:
        missing = ", ".join(sorted(required_statuses - statuses))
        raise ValueError(f"evidence must define every status category: {missing}")

    no_latest_tag = "No `latest` tag is produced for this release." in text
    if not no_latest_tag:
        raise ValueError("evidence must state that latest is not produced")
    no_cloud_run = "Cloud Run is outside this release scope." in text
    if not no_cloud_run:
        raise ValueError("evidence must state that Cloud Run is out of scope")

    private_data_findings = tuple(
        category for category, pattern in PRIVATE_EVIDENCE_PATTERNS if pattern.search(text)
    )
    if private_data_findings:
        raise ValueError("evidence contains a private-data pattern")

    return ReleaseEvidence(
        image_components=image_components,
        digest_format=DIGEST_FORMAT,
        pending_fields=pending_fields,
        statuses=statuses,
        no_latest_tag=no_latest_tag,
        no_cloud_run=no_cloud_run,
        private_data_findings=private_data_findings,
    )


def validate_release_evidence(path: Path = RELEASE_EVIDENCE_PATH) -> list[str]:
    """Return a stable documentation error for an invalid release evidence document."""

    try:
        parse_release_evidence(path)
    except ValueError as exc:
        return [f"{path.relative_to(ROOT)}: {exc}"]
    return []


def main() -> int:
    """Validate every required document and return a process exit code."""

    errors: list[str] = []
    for relative_path, headings in REQUIRED_HEADINGS.items():
        errors.extend(validate_document(ROOT / relative_path, headings))
    errors.extend(validate_release_evidence())

    if errors:
        print("\n".join(errors))
        return 1

    print(f"documentation contract passed ({len(REQUIRED_HEADINGS)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

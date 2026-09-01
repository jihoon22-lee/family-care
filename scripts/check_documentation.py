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
    r"`ghcr\.io/(?P<repository>[a-z0-9][a-z0-9._/-]*)-(?P<component>web|api|worker):0\.1\.0`\s*\|\s*"
    r"`(?P<version_digest>sha256:[0-9a-f]{64})`\s*\|\s*"
    r"`ghcr\.io/(?P=repository)-(?P=component):sha-(?P<commit_prefix>[0-9a-f]{12})`\s*\|\s*"
    r"`(?P<commit_digest>sha256:[0-9a-f]{64})`\s*\|\s*$"
)
DIGEST_FORMAT = "sha256:<64 lowercase hexadecimal characters>"
REQUIRED_RECORDED_FIELDS = frozenset(
    {
        "tag-workflow-run",
        "tag-head-sha",
        "web-version-digest",
        "web-commit-digest",
        "api-version-digest",
        "api-commit-digest",
        "worker-version-digest",
        "worker-commit-digest",
    }
)
WORKFLOW_RUN_PATTERN = re.compile(
    r"(?m)^- `tag-workflow-run`: \[(?P<run_id>[0-9]+)\]"
    r"\(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/(?P=run_id)\), `success`$"
)
HEAD_SHA_PATTERN = re.compile(r"(?m)^- `tag-head-sha`: `(?P<sha>[0-9a-f]{40})`$")
GITHUB_RELEASE_PATTERN = re.compile(
    r"(?m)^- GitHub Release metadata: \[`v0\.1\.0`\]"
    r"\(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/tag/v0\.1\.0\), "
    r"published [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
PRIVATE_EVIDENCE_PATTERNS = (
    ("absolute-path", re.compile(r"(?i)(?:/mnt/|/home/|/tmp/|[A-Za-z]:\\|\\\\wsl\$)")),
    ("drive-identifier", re.compile(r"(?i)drive\.google|docs\.google")),
    ("credential-value", re.compile(r"(?i)(?:sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,})")),
    ("assigned-secret", re.compile(r"(?i)\b(?:password|secret|token)\s*[:=]\s*\S+")),
)


@dataclass(frozen=True)
class ReleaseEvidence:
    """Strict, non-sensitive shape of the completed release evidence document."""

    image_components: tuple[str, ...]
    image_digest_pairs: tuple[tuple[str, str], ...]
    digest_format: str
    recorded_fields: frozenset[str]
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
    "docs/design/insurance-document-inventory.md": ("# Insurance document inventory design",),
    "docs/design/insurance-ledger-reconciliation.md": ("# Insurance ledger reconciliation design",),
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
    "docs/plan/020-insurance-ledger-reconciliation.md": (
        "# Insurance Ledger Reconciliation Implementation Plan",
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
    """Parse completed release evidence without accepting private values or fake results."""

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
    image_repositories = tuple(match.group("repository") for match in image_matches)
    if len(set(image_repositories)) != 1:
        raise ValueError("evidence image slots must share one repository prefix")
    image_digest_pairs = tuple(
        (match.group("version_digest"), match.group("commit_digest")) for match in image_matches
    )
    if any(version != commit for version, commit in image_digest_pairs):
        raise ValueError("each version and commit image reference must share one digest")
    commit_prefixes = tuple(match.group("commit_prefix") for match in image_matches)
    if len(set(commit_prefixes)) != 1:
        raise ValueError("evidence image slots must share one commit prefix")

    if f"`{DIGEST_FORMAT}`" not in text:
        raise ValueError("evidence must state the OCI digest format")
    workflow_run_match = WORKFLOW_RUN_PATTERN.search(text)
    head_sha_match = HEAD_SHA_PATTERN.search(text)
    if workflow_run_match is None or head_sha_match is None:
        raise ValueError("evidence must record a successful tag workflow and full head SHA")
    if not head_sha_match.group("sha").startswith(commit_prefixes[0]):
        raise ValueError("image commit tags must match the recorded head SHA")

    recorded_digest_values: dict[str, str] = {}
    for component, (version_digest, commit_digest) in zip(
        image_components, image_digest_pairs, strict=True
    ):
        for suffix, expected in (
            ("version-digest", version_digest),
            ("commit-digest", commit_digest),
        ):
            field = f"{component}-{suffix}"
            match = re.search(
                rf"(?m)^- `{re.escape(field)}`: `(?P<digest>sha256:[0-9a-f]{{64}})`$",
                text,
            )
            if match is None or match.group("digest") != expected:
                raise ValueError(f"evidence must record the table digest for {field}")
            recorded_digest_values[field] = match.group("digest")

    recorded_fields = frozenset(
        {
            "tag-workflow-run",
            "tag-head-sha",
            *recorded_digest_values,
        }
    )
    if recorded_fields != REQUIRED_RECORDED_FIELDS:
        missing = ", ".join(sorted(REQUIRED_RECORDED_FIELDS - recorded_fields))
        raise ValueError(f"evidence must contain completed release fields: {missing}")
    if GITHUB_RELEASE_PATTERN.search(text) is None:
        raise ValueError("evidence must record published GitHub Release metadata")

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
        image_digest_pairs=image_digest_pairs,
        digest_format=DIGEST_FORMAT,
        recorded_fields=recorded_fields,
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

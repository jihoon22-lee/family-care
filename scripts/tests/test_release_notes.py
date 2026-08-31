from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_audit import ReleaseImageDigest
from scripts.release_notes import (
    ReleaseNotesError,
    ReleaseNotesEvidence,
    extract_changelog_section,
    render_release_notes,
)

VERSION = "1.2.3"
COMMIT_SHA = "a" * 40
REPOSITORY = "example/family-care"
WORKFLOW_URL = "https://github.com/example/family-care/actions/runs/123456"
ROOT = Path(__file__).resolve().parents[2]

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Pending change.

## [1.2.3] - 2026-08-31

### Added

- Added one user-facing capability with a deliberately wrapped
  continuation line.

### Fixed

- Fixed one release issue.

### Security

- Kept public release metadata synthetic-only.

## [1.2.2] - 2026-08-30

### Fixed

- Older fix.
"""

EXPECTED_SECTION = """### Added

- Added one user-facing capability with a deliberately wrapped
  continuation line.

### Fixed

- Fixed one release issue.

### Security

- Kept public release metadata synthetic-only.
"""


def _images() -> tuple[ReleaseImageDigest, ...]:
    return tuple(
        ReleaseImageDigest(component=component, digest=f"sha256:{character * 64}")
        for component, character in (("web", "b"), ("api", "c"), ("worker", "d"))
    )


def _evidence() -> ReleaseNotesEvidence:
    return ReleaseNotesEvidence(
        version=VERSION,
        commit_sha=COMMIT_SHA,
        repository=REPOSITORY,
        workflow_url=WORKFLOW_URL,
        images=_images(),
    )


def test_extract_changelog_section_preserves_the_exact_version_body() -> None:
    section = extract_changelog_section(CHANGELOG, VERSION)

    assert section == EXPECTED_SECTION
    assert "Pending change" not in section
    assert "Older fix" not in section


def test_extract_changelog_section_rejects_missing_or_duplicate_version() -> None:
    with pytest.raises(ReleaseNotesError, match="version section not found"):
        extract_changelog_section(CHANGELOG, "9.9.9")

    duplicate = f"{CHANGELOG}\n## [{VERSION}] - 2026-09-01\n\n### Fixed\n\n- Duplicate.\n"
    with pytest.raises(ReleaseNotesError, match="duplicate version section"):
        extract_changelog_section(duplicate, VERSION)


def test_extract_changelog_section_rejects_empty_or_unknown_categories() -> None:
    empty = CHANGELOG.replace("- Fixed one release issue.\n", "")
    with pytest.raises(ReleaseNotesError, match="empty category: Fixed"):
        extract_changelog_section(empty, VERSION)

    unknown = CHANGELOG.replace("### Fixed", "### Internals")
    with pytest.raises(ReleaseNotesError, match="unsupported category: Internals"):
        extract_changelog_section(unknown, VERSION)


@pytest.mark.parametrize(
    "heading",
    (
        "## Release evidence",
        "#### Internal details",
        "# Replacement title",
        " ## Indented release evidence",
        "##\tTab-separated release evidence",
        "##",
    ),
)
def test_extract_changelog_section_rejects_other_markdown_headings(heading: str) -> None:
    injected = CHANGELOG.replace(
        "- Fixed one release issue.",
        f"- Fixed one release issue.\n\n{heading}\n\n- Injected content.",
    )

    with pytest.raises(ReleaseNotesError, match="unsupported Markdown heading"):
        extract_changelog_section(injected, VERSION)


def test_extract_changelog_section_rejects_literal_backslash_n() -> None:
    escaped = CHANGELOG.replace(
        "- Fixed one release issue.",
        r"- Fixed one release issue.\n- This is not a real line break.",
    )

    with pytest.raises(ReleaseNotesError, match="literal backslash-n"):
        extract_changelog_section(escaped, VERSION)


def test_render_release_notes_includes_exact_changes_and_verified_evidence() -> None:
    notes = render_release_notes(EXPECTED_SECTION, _evidence())

    assert notes.startswith(f"## Changes\n\n{EXPECTED_SECTION}\n## Release evidence\n")
    assert f"Commit: `{COMMIT_SHA}`" in notes
    assert f"Workflow: {WORKFLOW_URL}" in notes
    assert notes.count("ghcr.io/example/family-care-") == 3
    assert "ghcr.io/example/family-care-web@sha256:" + "b" * 64 in notes
    assert "ghcr.io/example/family-care-api@sha256:" + "c" * 64 in notes
    assert "ghcr.io/example/family-care-worker@sha256:" + "d" * 64 in notes
    assert "## Privacy and deployment boundary" in notes
    assert r"\n" not in notes


def test_render_release_notes_rejects_invalid_or_incomplete_evidence() -> None:
    invalid = ReleaseNotesEvidence(
        version=VERSION,
        commit_sha="short",
        repository=REPOSITORY,
        workflow_url=WORKFLOW_URL,
        images=_images()[:2],
    )

    with pytest.raises(ReleaseNotesError, match="commit SHA"):
        render_release_notes(EXPECTED_SECTION, invalid)

    duplicate_digest = "sha256:" + "e" * 64
    duplicated = ReleaseNotesEvidence(
        version=VERSION,
        commit_sha=COMMIT_SHA,
        repository=REPOSITORY,
        workflow_url=WORKFLOW_URL,
        images=tuple(
            ReleaseImageDigest(component=component, digest=duplicate_digest)
            for component in ("web", "api", "worker")
        ),
    )
    with pytest.raises(ReleaseNotesError, match="distinct image digests"):
        render_release_notes(EXPECTED_SECTION, duplicated)


def test_cli_writes_a_new_mode_0600_markdown_file(tmp_path: Path) -> None:
    changelog_path = tmp_path / "CHANGELOG.md"
    evidence_path = tmp_path / "release-image-evidence.json"
    output_path = tmp_path / "release-notes.md"
    changelog_path.write_text(CHANGELOG, encoding="utf-8")
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "release-image-evidence.v1",
                "version": VERSION,
                "commit_sha": COMMIT_SHA,
                "images": [
                    {"component": item.component, "digest": item.digest} for item in _images()
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/release_notes.py",
            "--version",
            VERSION,
            "--commit-sha",
            COMMIT_SHA,
            "--repository",
            REPOSITORY,
            "--workflow-url",
            WORKFLOW_URL,
            "--changelog",
            str(changelog_path),
            "--image-evidence",
            str(evidence_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "release-notes-ok\n"
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert output_path.read_text(encoding="utf-8") == render_release_notes(
        EXPECTED_SECTION,
        _evidence(),
    )


def test_cli_does_not_replace_an_existing_output(tmp_path: Path) -> None:
    output_path = tmp_path / "release-notes.md"
    output_path.write_text("keep me", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/release_notes.py",
            "--version",
            VERSION,
            "--commit-sha",
            COMMIT_SHA,
            "--repository",
            REPOSITORY,
            "--workflow-url",
            WORKFLOW_URL,
            "--changelog",
            str(tmp_path / "CHANGELOG.md"),
            "--image-evidence",
            str(tmp_path / "release-image-evidence.json"),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "release-notes-error: output path already exists" in result.stderr
    assert output_path.read_text(encoding="utf-8") == "keep me"


def test_current_changelog_renders_every_published_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert r"\n" not in changelog

    for version in ("0.1.0", "0.2.0", "0.3.0", "0.3.1", "0.3.2"):
        section = extract_changelog_section(changelog, version)
        notes = render_release_notes(
            section,
            ReleaseNotesEvidence(
                version=version,
                commit_sha=COMMIT_SHA,
                repository=REPOSITORY,
                workflow_url=WORKFLOW_URL,
                images=_images(),
            ),
        )

        assert notes.startswith(f"## Changes\n\n{section}")
        assert r"\n" not in notes

#!/usr/bin/env python3
"""Validate FamilyCare branch names and Conventional Commit subjects."""

from __future__ import annotations

import argparse
import re
import subprocess

BRANCH_TYPES = ("build", "chore", "ci", "docs", "feat", "fix", "refactor", "release", "test")
COMMIT_TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)
BRANCH_PATTERN = re.compile(rf"^(?:{'|'.join(BRANCH_TYPES)})/[a-z0-9]+(?:-[a-z0-9]+)*$")
DEPENDABOT_PATTERN = re.compile(
    r"^dependabot/(?:docker|github_actions|npm_and_yarn|pip)/[a-z0-9][a-z0-9._/-]*$"
)
COMMIT_PATTERN = re.compile(
    rf"^(?:{'|'.join(COMMIT_TYPES)})(?:\([a-z0-9][a-z0-9-]*\))?!?: \S(?:.*\S)?$"
)


def validate_branch_name(branch: str) -> list[str]:
    """Return branch naming errors."""

    if branch == "main" or BRANCH_PATTERN.fullmatch(branch) or DEPENDABOT_PATTERN.fullmatch(branch):
        return []
    return [f"invalid branch {branch!r}: use <type>/<kebab-case> without an agent prefix"]


def validate_commit_subject(subject: str) -> list[str]:
    """Return Conventional Commit subject errors."""

    errors: list[str] = []
    if len(subject) > 72:
        errors.append(f"commit subject exceeds 72 characters ({len(subject)}): {subject}")
    if subject.endswith("."):
        errors.append(f"commit subject must not end with a period: {subject}")
    if COMMIT_PATTERN.fullmatch(subject) is None:
        errors.append(f"invalid Conventional Commit subject: {subject}")
    return errors


def git_output(*arguments: str) -> str:
    """Run a read-only Git query and return stripped output."""

    result = subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"Git query failed: {detail}")
    return result.stdout.strip()


def commit_subjects(revision_range: str) -> list[str]:
    """Return commit subjects in chronological order for a Git range."""

    output = git_output("log", "--reverse", "--format=%s", revision_range)
    return output.splitlines() if output else []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", help="branch name; defaults to the current branch")
    parser.add_argument("--range", dest="revision_range", help="Git revision range to inspect")
    return parser.parse_args()


def main() -> int:
    """Validate the requested branch and commit range."""

    args = parse_args()
    try:
        branch = args.branch or git_output("branch", "--show-current")
        subjects = commit_subjects(args.revision_range) if args.revision_range else []
    except ValueError as error:
        print(error)
        return 1

    errors = validate_branch_name(branch)
    if args.revision_range and not subjects:
        errors.append(f"commit range contains no commits: {args.revision_range}")
    for subject in subjects:
        errors.extend(validate_commit_subject(subject))

    if errors:
        print("\n".join(errors))
        return 1
    print(f"Git conventions passed (branch {branch}, {len(subjects)} commit subjects)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

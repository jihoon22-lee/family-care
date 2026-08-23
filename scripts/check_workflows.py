#!/usr/bin/env python3
"""Validate immutable and least-privilege GitHub Actions policy."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github/workflows/ci.yml"
REQUIRED_CI_JOBS = {"containers", "integration", "python", "repository-safety", "web"}
EXPECTED_ACTIONS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-node": ("820762786026740c76f36085b0efc47a31fe5020", "v7.0.0"),
    "astral-sh/setup-uv": ("20cfd1bf945f4377ade1205e4dbc17946fc9a30d", "v10.0.1"),
    "docker/build-push-action": ("53b7df96c91f9c12dcc8a07bcb9ccacbed38856a", "v7.3.0"),
    "docker/setup-buildx-action": ("37fe631027851001ddb9b187196cc803df7f5f0e", "v4.3.0"),
    "gitleaks/gitleaks-action": ("e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e", "v3.0.0"),
    "pnpm/action-setup": ("0977fd99725f1db4007ccb2928dbb4e90d06cc86", "v6.0.10"),
}
PINNED_USE = re.compile(r"(?m)^\s+(?:-\s+)?uses:\s+([^\s#]+)\s+#\s+(v[0-9][0-9A-Za-z.-]*)\s*$")
ALL_USE = re.compile(r"(?m)^\s+(?:-\s+)?uses:\s+([^\s#]+)")


def validate_action_pins(content: str, relative: Path) -> list[str]:
    """Require immutable action SHAs and human-readable release comments."""

    errors: list[str] = []
    all_uses = ALL_USE.findall(content)
    pinned_uses = PINNED_USE.findall(content)
    if len(all_uses) != len(pinned_uses):
        errors.append(f"{relative}: every uses entry needs a release comment")
    for action, release in pinned_uses:
        action_name, reference = action.rsplit("@", 1)
        if re.fullmatch(r"[0-9a-f]{40}", reference) is None:
            errors.append(f"{relative}: action must use a 40-character lowercase SHA: {action}")
            continue
        expected = EXPECTED_ACTIONS.get(action_name)
        if expected is None:
            errors.append(f"{relative}: unapproved action: {action_name}")
        elif (reference, release) != expected:
            errors.append(
                f"{relative}: {action_name} must use {expected[0]} with comment {expected[1]}"
            )
    return errors


def validate_ci(content: str) -> list[str]:
    """Validate CI triggers, permissions, jobs, and build-only boundaries."""

    relative = CI_PATH.relative_to(ROOT)
    errors = validate_action_pins(content, relative)
    required_fragments = {
        "pull_request trigger": "  pull_request:",
        "main push trigger": "      - main",
        "read-only permissions": "permissions:\n  contents: read",
        "sequential container builds": "max-parallel: 1",
        "repository safety command": "scripts/check_repository_safety.py",
        "documentation command": "scripts/check_documentation.py",
        "workflow policy command": "scripts/check_workflows.py",
        "Git convention command": "scripts/check_git_conventions.py",
        "frozen pnpm install": "pnpm install --frozen-lockfile",
        "frozen uv sync": "uv sync --frozen",
        "integration marker": "pytest -m integration",
        "build-only containers": "push: false",
    }
    for label, fragment in required_fragments.items():
        if fragment not in content:
            errors.append(f"{relative}: missing {label}")

    if "pull_request_target" in content:
        errors.append(f"{relative}: pull_request_target is forbidden")
    if re.search(r"\$\{\{\s*secrets\.", content):
        errors.append(f"{relative}: CI must not reference repository secrets")
    if re.search(r"(?m)^\s+push:\s+true\s*$", content):
        errors.append(f"{relative}: CI container builds must not push")

    jobs = set(re.findall(r"(?m)^  ([a-z][a-z0-9-]+):\s*$", content))
    missing_jobs = REQUIRED_CI_JOBS - jobs
    if missing_jobs:
        errors.append(f"{relative}: missing jobs {sorted(missing_jobs)}")
    return errors


def main() -> int:
    """Run workflow policy validation."""

    if not CI_PATH.is_file():
        print(f"missing workflow: {CI_PATH.relative_to(ROOT)}")
        return 1
    errors = validate_ci(CI_PATH.read_text(encoding="utf-8"))
    if errors:
        print("\n".join(errors))
        return 1
    print("workflow policy passed (immutable actions, least privilege, build-only CI)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate immutable and least-privilege GitHub Actions policy."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github/workflows/ci.yml"
RELEASE_PATH = ROOT / ".github/workflows/release.yml"
DEPENDABOT_PATH = ROOT / ".github/dependabot.yml"
REQUIRED_CI_JOBS = {"containers", "integration", "python", "repository-safety", "web"}
REQUIRED_DEPENDABOT_IGNORES = {
    ("npm", "/", "typescript"): frozenset({"version-update:semver-major"}),
    ("docker", "/infra/containers", "node"): frozenset({"version-update:semver-major"}),
    ("docker", "/infra/compose", "postgres"): frozenset({"version-update:semver-major"}),
    ("docker", "/infra/containers", "python"): frozenset(
        {"version-update:semver-minor", "version-update:semver-major"}
    ),
}
EXPECTED_ACTIONS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-node": ("820762786026740c76f36085b0efc47a31fe5020", "v7.0.0"),
    "astral-sh/setup-uv": ("20cfd1bf945f4377ade1205e4dbc17946fc9a30d", "v10.0.1"),
    "docker/build-push-action": ("53b7df96c91f9c12dcc8a07bcb9ccacbed38856a", "v7.3.0"),
    "docker/login-action": ("dbcb813823bdd20940b903addbd779551569679f", "v4.6.0"),
    "docker/metadata-action": ("dc802804100637a589fabce1cb79ff13a1411302", "v6.2.0"),
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


def _dependabot_ignored_update_types(
    content: str,
) -> dict[tuple[str, str, str], frozenset[str]]:
    """Extract dependency ignore policies from the small Dependabot YAML shape we use."""

    policies: dict[tuple[str, str, str], frozenset[str]] = {}
    package_pattern = re.compile(
        r"(?ms)^  - package-ecosystem:\s*(?P<ecosystem>[^\s#]+)\s*$\n"
        r"(?P<body>.*?)(?=^  - package-ecosystem:|\Z)"
    )
    ignore_pattern = re.compile(
        r"(?ms)^      - dependency-name:\s*(?P<name>\"[^\"]+\"|'[^']+'|[^\s#]+)\s*$\n"
        r"(?P<body>.*?)(?=^      - dependency-name:|\Z)"
    )
    update_type_pattern = re.compile(r"(?m)^\s+-\s*(?P<update_type>\"[^\"]+\"|'[^']+'|[^\s#]+)\s*$")

    for package_match in package_pattern.finditer(content):
        ecosystem = package_match.group("ecosystem")
        package_body = package_match.group("body")
        directory_match = re.search(r"(?m)^    directory:\s*([^\s#]+)\s*$", package_body)
        directory = directory_match.group(1).strip("\"'") if directory_match else ""
        for ignore_match in ignore_pattern.finditer(package_body):
            name = ignore_match.group("name").strip("\"'")
            types = frozenset(
                match.group("update_type").strip("\"'")
                for match in update_type_pattern.finditer(ignore_match.group("body"))
            )
            policies[(ecosystem, directory, name)] = types
    return policies


def validate_dependabot(content: str) -> list[str]:
    """Require the documented semver ceilings for risky dependency majors/minors."""

    relative = DEPENDABOT_PATH.relative_to(ROOT)
    policies = _dependabot_ignored_update_types(content)
    errors: list[str] = []
    for (ecosystem, directory, dependency), expected in REQUIRED_DEPENDABOT_IGNORES.items():
        actual = policies.get((ecosystem, directory, dependency))
        if actual != expected:
            expected_text = ", ".join(sorted(expected))
            actual_text = ", ".join(sorted(actual or ())) or "none"
            errors.append(
                f"{relative}: {ecosystem} dependency {dependency!r} in {directory} must ignore "
                f"{expected_text}; found {actual_text}"
            )
    return errors


def validate_release(content: str) -> list[str]:
    """Validate the semantic-tag-only GHCR publishing boundary."""

    relative = RELEASE_PATH.relative_to(ROOT)
    errors = validate_action_pins(content, relative)
    required_fragments = {
        "semantic-version tags": '      - "v[0-9]+.[0-9]+.[0-9]+"',
        "read-only top-level permissions": "permissions:\n  contents: read",
        "package write permission": "packages: write",
        "validation dependency": "needs: [validate-tag, validate-foundation]",
        "Web image": "- name: web",
        "API image": "- name: api",
        "Worker image": "- name: worker",
        "GHCR registry": "registry: ghcr.io",
        "automatic GitHub token": "password: ${{ github.token }}",
        "semantic image tag": "type=semver,pattern={{version}}",
        "12-character SHA tag": 'DOCKER_METADATA_SHORT_SHA_LENGTH: "12"',
        "no automatic latest tag": "latest=false",
        "container publication": "push: true",
    }
    for label, fragment in required_fragments.items():
        if fragment not in content:
            errors.append(f"{relative}: missing {label}")

    if content.count("packages: write") != 1:
        errors.append(f"{relative}: packages: write must appear only on the publish job")
    if "pull_request" in content or re.search(r"(?m)^\s+branches:\s*$", content):
        errors.append(f"{relative}: release must trigger only from semantic-version tags")
    if re.search(r"\$\{\{\s*secrets\.", content):
        errors.append(f"{relative}: release must use only the automatic GitHub token")
    if re.search(r"(?i)\b(cloud\s*run|gcloud|kubectl|kubernetes|ssh)\b", content):
        errors.append(f"{relative}: production deployment commands are out of scope")

    validate_index = content.find("  validate-foundation:")
    publish_index = content.find("  publish:")
    login_index = content.find("docker/login-action@")
    push_index = content.find("push: true")
    if min(validate_index, publish_index, login_index, push_index) < 0 or not (
        validate_index < publish_index < login_index < push_index
    ):
        errors.append(f"{relative}: validation must complete before registry login and publish")
    return errors


def main() -> int:
    """Run workflow policy validation."""

    missing = [path for path in (CI_PATH, RELEASE_PATH, DEPENDABOT_PATH) if not path.is_file()]
    if missing:
        print("\n".join(f"missing workflow: {path.relative_to(ROOT)}" for path in missing))
        return 1
    errors = [
        *validate_ci(CI_PATH.read_text(encoding="utf-8")),
        *validate_release(RELEASE_PATH.read_text(encoding="utf-8")),
        *validate_dependabot(DEPENDABOT_PATH.read_text(encoding="utf-8")),
    ]
    if errors:
        print("\n".join(errors))
        return 1
    print("workflow policy passed (immutable CI and semantic-tag-only GHCR release)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

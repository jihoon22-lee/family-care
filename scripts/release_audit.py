#!/usr/bin/env python3
"""Audit immutable FamilyCare release identity and OCI manifest evidence."""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SEMVER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REPOSITORY_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9._-]{0,98}[a-z0-9])?/[a-z0-9](?:[a-z0-9._-]{0,98}[a-z0-9])?"
)
IMAGE_COMPONENTS = ("web", "api", "worker")
OCI_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)

ManifestGet = Callable[[str, Mapping[str, str]], tuple[int, Mapping[str, str], bytes]]


@dataclass(frozen=True)
class ReleaseIdentity:
    """Expected release coordinates with an exact image boundary."""

    version: str
    tag: str
    commit_sha: str
    image_names: tuple[str, str, str]


@dataclass(frozen=True)
class ReleaseFinding:
    """Stable, non-sensitive release audit result."""

    code: str
    detail: str


@dataclass(frozen=True)
class ReleaseImageDigest:
    """One verified immutable digest for a release image component."""

    component: str
    digest: str


def _python_version(path: Path) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"))
    versions = [
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(versions) != 1:
        raise ValueError("invalid Python version metadata")
    return versions[0]


def _metadata_versions(repository_root: Path) -> tuple[str, ...]:
    api_metadata = tomllib.loads(
        (repository_root / "apps/api/pyproject.toml").read_text(encoding="utf-8")
    )
    worker_metadata = tomllib.loads(
        (repository_root / "workers/analyzer/pyproject.toml").read_text(encoding="utf-8")
    )
    web_metadata = json.loads(
        (repository_root / "apps/web/package.json").read_text(encoding="utf-8")
    )
    return (
        str(api_metadata["project"]["version"]),
        str(worker_metadata["project"]["version"]),
        str(web_metadata["version"]),
        _python_version(repository_root / "apps/api/src/familycare_api/__init__.py"),
        _python_version(repository_root / "workers/analyzer/src/familycare_worker/__init__.py"),
    )


def _workflow_images(repository_root: Path) -> tuple[str, ...]:
    content = (repository_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    return tuple(re.findall(r"(?m)^          - name: ([a-z][a-z0-9-]*)\s*$", content))


def check_release_identity(
    repository_root: Path,
    version: str,
    commit_sha: str,
) -> tuple[ReleaseFinding, ...]:
    """Check package versions, tag shape, commit shape, and exact image names."""

    findings: list[ReleaseFinding] = []
    if SEMVER_PATTERN.fullmatch(version) is None:
        findings.append(ReleaseFinding("tag-shape", "release version is not semantic"))
    if COMMIT_PATTERN.fullmatch(commit_sha) is None:
        findings.append(ReleaseFinding("commit-shape", "release commit is not immutable"))

    try:
        versions = _metadata_versions(repository_root)
    except FileNotFoundError:
        findings.append(ReleaseFinding("missing-evidence", "release metadata is incomplete"))
    except KeyError, TypeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError:
        findings.append(ReleaseFinding("malformed-evidence", "release metadata is invalid"))
    else:
        if any(candidate != version for candidate in versions):
            findings.append(ReleaseFinding("version-mismatch", "product versions are not aligned"))

    try:
        image_names = _workflow_images(repository_root)
    except FileNotFoundError:
        findings.append(ReleaseFinding("missing-evidence", "release workflow is unavailable"))
    else:
        if image_names != IMAGE_COMPONENTS:
            findings.append(
                ReleaseFinding("image-set", "release image set is not web, api, worker")
            )
    return tuple(findings)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def inspect_image_digests(
    registry: str,
    repository: str,
    version: str,
    commit_sha: str,
    http_get: ManifestGet,
) -> tuple[tuple[ReleaseImageDigest, ...], tuple[ReleaseFinding, ...]]:
    """Return ordered immutable digests only when all release tags verify."""

    input_findings: list[ReleaseFinding] = []
    if registry != "ghcr.io":
        input_findings.append(ReleaseFinding("invalid-registry", "registry is not approved"))
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        input_findings.append(
            ReleaseFinding("invalid-repository", "repository coordinate is invalid")
        )
    if SEMVER_PATTERN.fullmatch(version) is None:
        input_findings.append(ReleaseFinding("invalid-version", "version is invalid"))
    if COMMIT_PATTERN.fullmatch(commit_sha) is None:
        input_findings.append(ReleaseFinding("invalid-commit", "commit is not immutable"))
    if input_findings:
        return (), tuple(input_findings)

    tags = (("version", version), ("commit", f"sha-{commit_sha[:12]}"))
    findings: list[ReleaseFinding] = []
    digests: dict[tuple[str, str], str] = {}
    for component in IMAGE_COMPONENTS:
        for tag_class, tag in tags:
            url = f"https://{registry}/v2/{repository}-{component}/manifests/{tag}"
            try:
                status, response_headers, _response_body = http_get(url, {"Accept": OCI_ACCEPT})
            except Exception:  # The CLI converts transport details to a stable category.
                findings.append(ReleaseFinding("manifest-request", f"{component}:{tag_class}"))
                continue
            if status != 200:
                findings.append(ReleaseFinding("manifest-status", f"{component}:{tag_class}"))
                continue
            digest = _header(response_headers, "Docker-Content-Digest")
            if digest is None or DIGEST_PATTERN.fullmatch(digest) is None:
                findings.append(ReleaseFinding("manifest-digest", f"{component}:{tag_class}"))
                continue
            digests[(component, tag_class)] = digest

    for component in IMAGE_COMPONENTS:
        version_digest = digests.get((component, "version"))
        commit_digest = digests.get((component, "commit"))
        if (
            version_digest is not None
            and commit_digest is not None
            and version_digest != commit_digest
        ):
            findings.append(ReleaseFinding("digest-mismatch", component))

    version_digests = [
        digests[(component, "version")]
        for component in IMAGE_COMPONENTS
        if (component, "version") in digests
    ]
    if len(version_digests) == len(IMAGE_COMPONENTS) and len(set(version_digests)) != len(
        version_digests
    ):
        findings.append(ReleaseFinding("cross-image-digest", "release images share a manifest"))
    if findings:
        return (), tuple(findings)
    return (
        tuple(
            ReleaseImageDigest(component=component, digest=digests[(component, "version")])
            for component in IMAGE_COMPONENTS
        ),
        (),
    )


def verify_image_digests(
    registry: str,
    repository: str,
    version: str,
    commit_sha: str,
    http_get: ManifestGet,
) -> tuple[ReleaseFinding, ...]:
    """Compatibility wrapper returning only OCI verification findings."""

    _digests, findings = inspect_image_digests(
        registry,
        repository,
        version,
        commit_sha,
        http_get,
    )
    return findings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit FamilyCare release identity")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release identity audit without importing application code."""

    args = _parser().parse_args(argv)
    findings = check_release_identity(args.repository_root, args.version, args.commit_sha)
    if findings:
        for finding in findings:
            print(f"{finding.code}: {finding.detail}")
        return 1
    print("release-identity-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

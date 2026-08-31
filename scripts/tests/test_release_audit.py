import json
from pathlib import Path

from scripts.release_audit import (
    ReleaseImageDigest,
    check_release_identity,
    inspect_image_digests,
)

VERSION = "0.1.0"
COMMIT_SHA = "a" * 40


def _write_release_tree(root: Path, *, version: str = VERSION) -> None:
    files = {
        "apps/api/pyproject.toml": f'[project]\nname = "familycare-api"\nversion = "{version}"\n',
        "workers/analyzer/pyproject.toml": (
            f'[project]\nname = "familycare-worker"\nversion = "{version}"\n'
        ),
        "apps/web/package.json": json.dumps({"name": "@familycare/web", "version": version}),
        "apps/api/src/familycare_api/__init__.py": (
            f'"""Synthetic API package."""\n\n__version__ = "{version}"\n'
        ),
        "workers/analyzer/src/familycare_worker/__init__.py": (
            f'"""Synthetic Worker package."""\n\n__version__ = "{version}"\n'
        ),
        ".github/workflows/release.yml": """
jobs:
  publish:
    strategy:
      matrix:
        include:
          - name: web
            dockerfile: infra/containers/web.Dockerfile
          - name: api
            dockerfile: infra/containers/api.Dockerfile
          - name: worker
            dockerfile: infra/containers/worker.Dockerfile
""",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _codes(root: Path, *, version: str = VERSION, commit_sha: str = COMMIT_SHA) -> set[str]:
    return {
        finding.code
        for finding in check_release_identity(root, version=version, commit_sha=commit_sha)
    }


def test_release_identity_accepts_aligned_metadata(tmp_path: Path) -> None:
    _write_release_tree(tmp_path)

    assert check_release_identity(tmp_path, VERSION, COMMIT_SHA) == ()


def test_release_identity_rejects_each_metadata_mismatch(tmp_path: Path) -> None:
    _write_release_tree(tmp_path)
    (tmp_path / "apps/web/package.json").write_text(
        json.dumps({"name": "@familycare/web", "version": "0.1.1"}),
        encoding="utf-8",
    )

    assert "version-mismatch" in _codes(tmp_path)


def test_release_identity_rejects_missing_metadata_without_disclosing_path(tmp_path: Path) -> None:
    _write_release_tree(tmp_path)
    missing = tmp_path / "apps/api/pyproject.toml"
    missing.unlink()

    findings = check_release_identity(tmp_path, VERSION, COMMIT_SHA)

    assert {finding.code for finding in findings} == {"missing-evidence"}
    assert all(str(tmp_path) not in finding.detail for finding in findings)


def test_release_identity_rejects_invalid_version_and_commit_shapes(tmp_path: Path) -> None:
    _write_release_tree(tmp_path)

    codes = _codes(tmp_path, version="0.1", commit_sha="synthetic-commit")

    assert {"tag-shape", "commit-shape"} <= codes


def test_release_identity_requires_exact_image_set(tmp_path: Path) -> None:
    _write_release_tree(tmp_path)
    workflow = tmp_path / ".github/workflows/release.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "          - name: worker\n", "          - name: exporter\n"
        ),
        encoding="utf-8",
    )

    assert "image-set" in _codes(tmp_path)


def test_image_inspection_returns_only_ordered_verified_version_digests() -> None:
    version = "0.1.0"
    commit_sha = "b" * 40
    expected = {
        "web": "sha256:" + "1" * 64,
        "api": "sha256:" + "2" * 64,
        "worker": "sha256:" + "3" * 64,
    }

    def success_get(url: str, _headers: object) -> tuple[int, dict[str, str], bytes]:
        component = next(name for name in expected if f"-repo-{name}/" in url)
        return 200, {"Docker-Content-Digest": expected[component]}, b"ignored"

    digests, findings = inspect_image_digests(
        "ghcr.io",
        "synthetic-owner/synthetic-repo",
        version,
        commit_sha,
        success_get,
    )

    assert findings == ()
    assert digests == tuple(
        ReleaseImageDigest(component=component, digest=expected[component])
        for component in ("web", "api", "worker")
    )


def test_image_inspection_returns_no_publishable_digests_on_any_finding() -> None:
    version = "0.1.0"
    commit_sha = "b" * 40

    def mismatched_get(url: str, _headers: object) -> tuple[int, dict[str, str], bytes]:
        component = next(name for name in ("web", "api", "worker") if f"-{name}/" in url)
        character = {"web": "1", "api": "2", "worker": "3"}[component]
        if component == "api" and url.endswith(f"sha-{commit_sha[:12]}"):
            character = "4"
        return 200, {"Docker-Content-Digest": "sha256:" + character * 64}, b"ignored"

    digests, findings = inspect_image_digests(
        "ghcr.io",
        "synthetic-owner/synthetic-repo",
        version,
        commit_sha,
        mismatched_get,
    )

    assert digests == ()
    assert {finding.code for finding in findings} == {"digest-mismatch"}

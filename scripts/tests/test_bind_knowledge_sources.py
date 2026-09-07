"""The local source-manifest command never guesses aliases or prints private inputs."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest


def _manifest(path: Path) -> str:
    value = {
        "schema_version": "knowledge-source-bindings-v1",
        "import_run_id": "00000000-0000-4000-8000-000000000001",
        "package_digest_sha256": "a" * 64,
        "entries": [
            {
                "document_binding_id": "00000000-0000-4000-8000-000000000002",
                "source_alias_digest_sha256": "b" * 64,
                "document_version_id": "00000000-0000-4000-8000-000000000003",
                "evidence_id": "00000000-0000-4000-8000-000000000004",
                "content_sha256": "c" * 64,
                "page_count": 2,
                "document_kind": "policy",
                "expected_current_binding_id": None,
            }
        ],
    }
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "digest",
        "repository",
        "relative",
        "symlink",
        "large",
        "duplicate_keys",
        "deep_json",
    ],
)
def test_loader_accepts_only_exact_bounded_external_manifests(tmp_path: Path, case: str) -> None:
    from familycare_api.insurance_reconciliation.source_bindings import KnowledgeSourceBindingError

    from scripts.bind_knowledge_sources import load_manifest

    repository = tmp_path / "repo"
    repository.mkdir()
    path = (repository if case == "repository" else tmp_path) / "synthetic-binding.json"
    digest = _manifest(path)
    if case == "digest":
        digest = "f" * 64
    elif case == "relative":
        path = Path(path.name)
    elif case == "symlink":
        link = tmp_path / "synthetic-link.json"
        link.symlink_to(path)
        path = link
    elif case == "large":
        path.write_bytes(b" " * (1024 * 1024 + 1))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    elif case == "duplicate_keys":
        raw = path.read_bytes().replace(
            b'{"schema_version":', b'{"schema_version":"ambiguous-version","schema_version":', 1
        )
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    if case == "deep_json":
        raw = b"[" * 20_000 + b"0" + b"]" * 20_000
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    if case == "valid":
        assert len(load_manifest(path, digest, repository_root=repository).entries) == 1
    else:
        with pytest.raises(KnowledgeSourceBindingError, match="^KNOWLEDGE_SOURCE_BINDING_INVALID$"):
            load_manifest(path, digest, repository_root=repository)


def test_command_reports_only_counts_or_fixed_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import bind_knowledge_sources as command

    path = tmp_path / "synthetic-binding.json"
    digest = _manifest(path)
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic-only")

    class Repository:
        def __init__(self, database_url: str) -> None:
            assert database_url == "postgresql://synthetic-only"

        def apply_manifest(self, scope: object, manifest: object) -> tuple[UUID, ...]:
            return (UUID(int=1),)

    monkeypatch.setattr(command, "KnowledgeSourceBindingRepository", Repository)
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_SOURCE_MANIFEST_PATH", str(path))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_SOURCE_MANIFEST_SHA256", digest)
    monkeypatch.setenv(
        "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID", "00000000-0000-4000-8000-000000000005"
    )
    assert command.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"state": "APPLIED", "binding_count": 1}
    monkeypatch.setenv(
        "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID", "synthetic-invalid-private-value"
    )
    assert command.main([]) == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err == "KNOWLEDGE_SOURCE_BINDING_INVALID\n"
    assert command.main(["--manifest", "synthetic-private-path"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err == "KNOWLEDGE_SOURCE_BINDING_INVALID\n"

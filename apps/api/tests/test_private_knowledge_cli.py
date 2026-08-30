"""Environment-only and sanitized private-knowledge operator CLI."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.private_knowledge import cli
from familycare_api.private_knowledge.repository import KnowledgeSnapshotSummary

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001951")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000001952")
RUN_ID = UUID("00000000-0000-4000-8000-000000001953")

_PRIVATE_ENVIRONMENT = (
    "FAMILYCARE_DATABASE_URL",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_REPORT_PATH",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_REPOSITORY_ROOT",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_ACTOR_ID",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST",
)


def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PRIVATE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def test_validate_uses_only_environment_paths_and_sanitizes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_environment(monkeypatch)
    package_root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT", str(package_root))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_REPOSITORY_ROOT", str(repository_root))

    assert cli.main(["validate"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status=VALIDATED" in captured.out
    assert "contracts=1" in captured.out
    for private_value in (
        str(package_root),
        str(repository_root),
        "Family Member A",
        "Sample Policy",
        "synthetic-certificate-source",
    ):
        assert private_value not in captured.out


def test_missing_environment_and_unknown_argv_return_stable_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_environment(monkeypatch)

    assert cli.main(["validate"]) == 2
    missing = capsys.readouterr()
    assert missing.out == ""
    assert missing.err == "familycare-private-knowledge: ENVIRONMENT_REQUIRED\n"

    with pytest.raises(SystemExit) as invalid:
        cli.main(["validate", "--package", "private-value"])
    assert invalid.value.code == 2
    rejected = capsys.readouterr()
    assert "private-value" not in rejected.err


def test_verify_outputs_only_opaque_run_and_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://private-dsn")
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID", str(HOUSEHOLD_ID))

    class _Repository:
        def __init__(self, database_url: str) -> None:
            assert database_url == "postgresql://private-dsn"

        def verify_current(self, household_space_id: UUID) -> KnowledgeSnapshotSummary:
            assert household_space_id == HOUSEHOLD_ID
            from familycare_api.private_knowledge.reconciliation import (
                KnowledgeEntityCounts,
            )

            return KnowledgeSnapshotSummary(
                run_id=RUN_ID,
                package_digest_sha256="a" * 64,
                state="APPLIED",
                is_current=True,
                counts=KnowledgeEntityCounts(
                    subjects=1,
                    contracts=1,
                    coverages=1,
                    terms_assignments=1,
                    terms_assignment_sources=1,
                    terms_sections=1,
                    source_clauses=1,
                    semantic_reviews=1,
                    facts=1,
                    fact_citations=1,
                    coverage_terms_mappings=1,
                    document_bindings=2,
                ),
                executable_fact_count=0,
                executable_mapping_count=0,
                unsafe_operational_binding_count=0,
            )

    monkeypatch.setattr(cli, "PostgresPrivateKnowledgeRepository", _Repository)

    assert cli.main(["verify"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"run_id={RUN_ID}" in captured.out
    assert "contracts=1" in captured.out
    assert "private-dsn" not in captured.out

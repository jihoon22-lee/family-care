"""Environment-only and sanitized private-knowledge operator CLI."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.private_knowledge import cli
from familycare_api.private_knowledge.confirmations import (
    AppliedConfirmationSet,
    ConfirmationDryRunReport,
)
from familycare_api.private_knowledge.publication_models import PublicationCounts
from familycare_api.private_knowledge.publication_reconciliation import (
    DispositionCounts,
    PublicationBlockCounts,
    RulePublicationDryRunReport,
)
from familycare_api.private_knowledge.publication_repository import (
    AppliedRulePublication,
    RulePublicationSummary,
)
from familycare_api.private_knowledge.repository import KnowledgeSnapshotSummary

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)
from apps.api.tests.private_knowledge_publication_fixtures import (
    write_synthetic_rule_publication_package,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001951")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000001952")
RUN_ID = UUID("00000000-0000-4000-8000-000000001953")

_PRIVATE_ENVIRONMENT = (
    "FAMILYCARE_DATABASE_URL",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_REPORT_PATH",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_ACTOR_ID",
    "FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST",
    "FAMILYCARE_PRIVATE_CONFIRMATION_MANIFEST_PATH",
    "FAMILYCARE_PRIVATE_CONFIRMATION_REPORT_PATH",
    "FAMILYCARE_PRIVATE_RULE_PACKAGE_ROOT",
    "FAMILYCARE_PRIVATE_RULE_REPORT_PATH",
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
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT", str(package_root))

    assert cli.main(["validate"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status=VALIDATED" in captured.out
    assert "contracts=1" in captured.out
    for private_value in (
        str(package_root),
        "Family Member A",
        "Sample Policy",
        "synthetic-certificate-source",
    ):
        assert private_value not in captured.out


def test_validate_ignores_caller_repository_override_and_protects_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_environment(monkeypatch)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    virtual_environment = runtime_root / ".venv"
    virtual_environment.mkdir()
    package_root = write_synthetic_private_knowledge_package(runtime_root / "private-package")
    caller_root = tmp_path / "caller-selected-root"
    caller_root.mkdir()
    monkeypatch.setattr(cli.sys, "prefix", str(virtual_environment))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT", str(package_root))
    monkeypatch.setenv(
        "FAMILYCARE_PRIVATE_KNOWLEDGE_REPOSITORY_ROOT",
        str(caller_root),
    )

    assert cli.main(["validate"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "familycare-private-knowledge: ROOT_INSIDE_REPOSITORY\n"
    assert str(package_root) not in captured.err


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


def test_confirmation_commands_use_protected_environment_inputs_and_safe_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_environment(monkeypatch)
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    manifest_path = private_root / "confirmation.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest_path.chmod(0o600)
    report_path = private_root / "confirmation-report.json"
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://private-dsn")
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID", str(HOUSEHOLD_ID))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST", "d" * 64)
    monkeypatch.setenv("FAMILYCARE_PRIVATE_CONFIRMATION_MANIFEST_PATH", str(manifest_path))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_CONFIRMATION_REPORT_PATH", str(report_path))

    report = ConfirmationDryRunReport(
        schema_version="private-knowledge-confirmation-dry-run.v1",
        manifest_digest_sha256="a" * 64,
        package_digest_sha256="b" * 64,
        household_space_id=HOUSEHOLD_ID,
        current_run_id=RUN_ID,
        baseline_digest_sha256="c" * 64,
        operation="APPLY",
        subject_count=6,
        contract_count=52,
        binding_change_count=6,
        confirmation_insert_count=52,
        confirmation_supersede_count=0,
        report_digest_sha256="d" * 64,
    )
    applied = AppliedConfirmationSet(
        run_id=RUN_ID,
        package_digest_sha256="b" * 64,
        subject_count=6,
        contract_count=52,
        current_confirmation_count=52,
    )

    class _Repository:
        def __init__(self, database_url: str) -> None:
            assert database_url == "postgresql://private-dsn"

    monkeypatch.setattr(cli, "PostgresPrivateKnowledgeRepository", _Repository)
    monkeypatch.setattr(cli, "prepare_confirmation_dry_run", lambda **_: report)
    monkeypatch.setattr(cli, "apply_confirmation_manifest", lambda **_: applied)

    assert cli.main(["confirmation-dry-run"]) == 0
    dry_run = capsys.readouterr()
    assert dry_run.err == ""
    assert "status=CONFIRMATION_DRY_RUN_APPLY" in dry_run.out
    assert "subjects=6" in dry_run.out
    assert "contracts=52" in dry_run.out

    assert cli.main(["confirmation-apply"]) == 0
    applied_output = capsys.readouterr()
    assert applied_output.err == ""
    assert "status=CONFIRMATIONS_APPLIED" in applied_output.out
    assert "contracts=52" in applied_output.out
    for private_value in (str(manifest_path), str(report_path), "private-dsn"):
        assert private_value not in dry_run.out + applied_output.out


def test_publication_commands_use_separate_paths_and_print_only_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_environment(monkeypatch)
    package_root = write_synthetic_rule_publication_package(tmp_path / "rule-package")
    report_path = tmp_path / "rule-report.json"
    monkeypatch.setenv("FAMILYCARE_PRIVATE_RULE_PACKAGE_ROOT", str(package_root))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_RULE_REPORT_PATH", str(report_path))
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://private-dsn")
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID", str(HOUSEHOLD_ID))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_ACTOR_ID", str(ACTOR_ID))
    monkeypatch.setenv("FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST", "d" * 64)

    counts = PublicationCounts(
        subject_count=1,
        contract_count=1,
        coverage_count=1,
        disposition_count=1,
        published_disposition_count=1,
        blocked_disposition_count=0,
        not_applicable_disposition_count=0,
        status_interval_count=1,
        fact_normalizer_count=1,
        rule_publication_count=1,
        rule_citation_count=1,
        calculation_publication_count=1,
        calculation_citation_count=1,
    )
    dispositions = DispositionCounts(published=1, blocked=0, not_applicable=0)
    report = RulePublicationDryRunReport(
        schema_version="private-knowledge-rule-dry-run.v1",
        package_schema_version="private-knowledge-rule-publication.sol-v1",
        package_digest_sha256="a" * 64,
        knowledge_package_digest_sha256="b" * 64,
        knowledge_snapshot_digest_sha256="c" * 64,
        baseline_digest_sha256="e" * 64,
        operation="CREATE",
        input_counts=counts,
        expected_insert_counts=counts,
        expected_current_counts=counts,
        dispositions=dispositions,
        expected_current_dispositions=dispositions,
        block_counts=PublicationBlockCounts(
            snapshot_mismatch=0,
            historical_digest_conflict=0,
            disposition_closure_mismatch=0,
            missing_current_confirmation=0,
            subject_binding_mismatch=0,
            coverage_authority_mismatch=0,
            citation_mismatch=0,
        ),
        apply_block_count=0,
        report_digest_sha256="d" * 64,
    )
    applied = AppliedRulePublication(
        run_id=RUN_ID,
        package_digest_sha256="a" * 64,
        state="APPLIED",
        is_current=True,
        counts=counts,
        dispositions=dispositions,
    )
    verified = RulePublicationSummary.model_validate(applied.model_dump())

    class _Repository:
        def __init__(self, database_url: str) -> None:
            assert database_url == "postgresql://private-dsn"

        def verify_current(self, household_space_id: UUID) -> RulePublicationSummary:
            assert household_space_id == HOUSEHOLD_ID
            return verified

    monkeypatch.setattr(cli, "PostgresRulePublicationRepository", _Repository)
    monkeypatch.setattr(cli, "prepare_rule_publication_dry_run", lambda **_: report)
    monkeypatch.setattr(cli, "apply_rule_publication_package", lambda **_: applied)

    assert cli.main(["publication-validate"]) == 0
    assert cli.main(["publication-dry-run"]) == 0
    assert cli.main(["publication-apply"]) == 0
    assert cli.main(["publication-verify"]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert "status=PUBLICATION_VALIDATED" in output.out
    assert "status=PUBLICATION_DRY_RUN_CREATE" in output.out
    assert "status=PUBLICATION_APPLIED" in output.out
    assert "status=PUBLICATION_VERIFIED" in output.out
    assert "published=1" in output.out
    for private_value in (str(package_root), str(report_path), "private-dsn", "a" * 64):
        assert private_value not in output.out

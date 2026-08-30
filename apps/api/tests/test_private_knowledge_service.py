"""Private knowledge dry-run orchestration without database mutation."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.private_knowledge.reconciliation import (
    BaselineCounts,
    KnowledgeDatabaseBaseline,
    KnowledgeEntityCounts,
    load_dry_run_report,
)
from familycare_api.private_knowledge.repository import (
    AppliedKnowledgeSnapshot,
    PrivateKnowledgeRepositoryError,
    PrivateKnowledgeRepositoryErrorCode,
)
from familycare_api.private_knowledge.service import (
    apply_private_knowledge_snapshot,
    prepare_private_knowledge_dry_run,
)

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001921")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000001922")


class _BaselineReader:
    def __init__(self, baseline: KnowledgeDatabaseBaseline) -> None:
        self.baseline = baseline
        self.requested: list[UUID] = []

    def read_baseline(self, household_space_id: UUID) -> KnowledgeDatabaseBaseline:
        self.requested.append(household_space_id)
        return self.baseline


class _SnapshotApplier:
    def __init__(self) -> None:
        self.calls = []

    def apply_snapshot(
        self,
        package,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report,
    ) -> AppliedKnowledgeSnapshot:
        self.calls.append(
            (package.package_digest_sha256, household_space_id, actor_id, approved_report)
        )
        return AppliedKnowledgeSnapshot(
            run_id=UUID("00000000-0000-4000-8000-000000001923"),
            package_digest_sha256=package.package_digest_sha256,
            state="APPLIED",
            is_current=True,
            counts=approved_report.expected_current_counts,
            executable_fact_count=0,
            executable_mapping_count=0,
            unsafe_operational_binding_count=0,
        )


def test_prepare_dry_run_validates_reads_reconciles_and_persists(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    package_root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    report_root = tmp_path / "private-reports"
    report_root.mkdir()
    report_root.chmod(0o700)
    report_path = report_root / "dry-run.json"
    reader = _BaselineReader(
        KnowledgeDatabaseBaseline(
            household_space_id=HOUSEHOLD_ID,
            baseline_digest_sha256="b" * 64,
            current_run_id=None,
            current_package_digest_sha256=None,
            known_package_digests=(),
            counts=BaselineCounts(
                family_members=0,
                policy_contracts=0,
                riders=0,
                document_versions=0,
                evidence=0,
                import_runs=0,
                current_import_runs=0,
            ),
            current_snapshot_counts=KnowledgeEntityCounts.zero(),
            policy_label_key_counts=(),
            coverage_label_key_counts=(),
        )
    )

    report = prepare_private_knowledge_dry_run(
        package_root=package_root,
        report_path=report_path,
        repository_root=repository_root,
        household_space_id=HOUSEHOLD_ID,
        baseline_reader=reader,
    )

    assert reader.requested == [HOUSEHOLD_ID]
    assert report.operation == "CREATE"
    assert report.apply_block_count == 0
    assert load_dry_run_report(report_path, repository_root=repository_root) == report


def test_apply_service_reloads_and_authenticates_package_and_report(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    package_root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    report_root = tmp_path / "private-reports"
    report_root.mkdir()
    report_root.chmod(0o700)
    report_path = report_root / "dry-run.json"
    baseline = _BaselineReader(
        KnowledgeDatabaseBaseline(
            household_space_id=HOUSEHOLD_ID,
            baseline_digest_sha256="b" * 64,
            current_run_id=None,
            current_package_digest_sha256=None,
            known_package_digests=(),
            counts=BaselineCounts(
                family_members=0,
                policy_contracts=0,
                riders=0,
                document_versions=0,
                evidence=0,
                import_runs=0,
                current_import_runs=0,
            ),
            current_snapshot_counts=KnowledgeEntityCounts.zero(),
            policy_label_key_counts=(),
            coverage_label_key_counts=(),
        )
    )
    report = prepare_private_knowledge_dry_run(
        package_root=package_root,
        report_path=report_path,
        repository_root=repository_root,
        household_space_id=HOUSEHOLD_ID,
        baseline_reader=baseline,
    )
    applier = _SnapshotApplier()

    applied = apply_private_knowledge_snapshot(
        package_root=package_root,
        report_path=report_path,
        repository_root=repository_root,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report_digest_sha256=report.report_digest_sha256,
        snapshot_applier=applier,
    )

    assert applied.counts == report.expected_current_counts
    assert len(applier.calls) == 1
    assert applier.calls[0][1:] == (HOUSEHOLD_ID, ACTOR_ID, report)

    with pytest.raises(PrivateKnowledgeRepositoryError) as unapproved:
        apply_private_knowledge_snapshot(
            package_root=package_root,
            report_path=report_path,
            repository_root=repository_root,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report_digest_sha256="0" * 64,
            snapshot_applier=applier,
        )
    assert unapproved.value.code is PrivateKnowledgeRepositoryErrorCode.APPROVAL_INVALID
    assert len(applier.calls) == 1

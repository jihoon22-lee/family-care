"""Private knowledge dry-run orchestration without database mutation."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from familycare_api.private_knowledge.reconciliation import (
    BaselineCounts,
    KnowledgeDatabaseBaseline,
    KnowledgeEntityCounts,
    load_dry_run_report,
)
from familycare_api.private_knowledge.service import prepare_private_knowledge_dry_run

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001921")


class _BaselineReader:
    def __init__(self, baseline: KnowledgeDatabaseBaseline) -> None:
        self.baseline = baseline
        self.requested: list[UUID] = []

    def read_baseline(self, household_space_id: UUID) -> KnowledgeDatabaseBaseline:
        self.requested.append(household_space_id)
        return self.baseline


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
    assert load_dry_run_report(report_path) == report

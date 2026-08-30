"""Application services for private knowledge validation and dry runs."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.reconciliation import (
    KnowledgeDatabaseBaseline,
    KnowledgeDryRunReport,
    build_dry_run_report,
    write_dry_run_report,
)
from familycare_api.private_knowledge.repository import (
    PrivateKnowledgeRepositoryError,
    PrivateKnowledgeRepositoryErrorCode,
)


class PrivateKnowledgeBaselineReader(Protocol):
    def read_baseline(
        self,
        household_space_id: UUID,
    ) -> KnowledgeDatabaseBaseline: ...


def prepare_private_knowledge_dry_run(
    *,
    package_root: Path,
    report_path: Path,
    repository_root: Path,
    household_space_id: UUID,
    baseline_reader: PrivateKnowledgeBaselineReader,
) -> KnowledgeDryRunReport:
    """Validate one external package and write a count-only DB reconciliation."""

    package = load_private_knowledge_package(
        package_root,
        repository_root=repository_root,
    )
    baseline = baseline_reader.read_baseline(household_space_id)
    if baseline.household_space_id != household_space_id:
        raise PrivateKnowledgeRepositoryError(PrivateKnowledgeRepositoryErrorCode.BASELINE_INVALID)
    report = build_dry_run_report(package, baseline)
    write_dry_run_report(
        report,
        report_path,
        repository_root=repository_root,
    )
    return report

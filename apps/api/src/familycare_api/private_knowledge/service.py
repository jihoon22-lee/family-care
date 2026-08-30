"""Application services for private knowledge validation and dry runs."""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Protocol
from uuid import UUID

from familycare_api.private_knowledge.package import (
    PrivateKnowledgePackage,
    load_private_knowledge_package,
)
from familycare_api.private_knowledge.reconciliation import (
    KnowledgeDatabaseBaseline,
    KnowledgeDryRunReport,
    build_dry_run_report,
    load_dry_run_report,
    write_dry_run_report,
)
from familycare_api.private_knowledge.repository import (
    AppliedKnowledgeSnapshot,
    PrivateKnowledgeRepositoryError,
    PrivateKnowledgeRepositoryErrorCode,
)


class PrivateKnowledgeBaselineReader(Protocol):
    def read_baseline(
        self,
        household_space_id: UUID,
    ) -> KnowledgeDatabaseBaseline: ...


class PrivateKnowledgeSnapshotApplier(Protocol):
    def apply_snapshot(
        self,
        package: PrivateKnowledgePackage,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report: KnowledgeDryRunReport,
    ) -> AppliedKnowledgeSnapshot: ...


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


def apply_private_knowledge_snapshot(
    *,
    package_root: Path,
    report_path: Path,
    repository_root: Path,
    household_space_id: UUID,
    actor_id: UUID,
    approved_report_digest_sha256: str,
    snapshot_applier: PrivateKnowledgeSnapshotApplier,
) -> AppliedKnowledgeSnapshot:
    """Reload authenticated inputs immediately before the atomic DB apply."""

    package = load_private_knowledge_package(
        package_root,
        repository_root=repository_root,
    )
    approved_report = load_dry_run_report(report_path)
    if not hmac.compare_digest(
        approved_report.report_digest_sha256,
        approved_report_digest_sha256,
    ):
        raise PrivateKnowledgeRepositoryError(PrivateKnowledgeRepositoryErrorCode.APPROVAL_INVALID)
    return snapshot_applier.apply_snapshot(
        package,
        household_space_id=household_space_id,
        actor_id=actor_id,
        approved_report=approved_report,
    )

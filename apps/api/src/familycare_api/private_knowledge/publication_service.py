"""Approval services for reviewed rule-publication packages."""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Protocol
from uuid import UUID

from familycare_api.private_knowledge.publication_package import (
    RulePublicationPackage,
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_reconciliation import (
    PublicationDatabaseBaseline,
    RulePublicationDryRunReport,
    build_rule_publication_dry_run,
    load_rule_publication_dry_run_report,
    write_rule_publication_dry_run_report,
)
from familycare_api.private_knowledge.publication_repository import (
    AppliedRulePublication,
    RulePublicationRepositoryError,
    RulePublicationRepositoryErrorCode,
)


class RulePublicationBaselineReader(Protocol):
    def read_baseline(
        self,
        household_space_id: UUID,
    ) -> PublicationDatabaseBaseline: ...


class RulePublicationApplier(Protocol):
    def apply(
        self,
        package: RulePublicationPackage,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report: RulePublicationDryRunReport,
    ) -> AppliedRulePublication: ...


def prepare_rule_publication_dry_run(
    *,
    package_root: Path,
    report_path: Path,
    repository_root: Path,
    household_space_id: UUID,
    baseline_reader: RulePublicationBaselineReader,
) -> RulePublicationDryRunReport:
    package = load_rule_publication_package(
        package_root,
        repository_root=repository_root,
    )
    baseline = baseline_reader.read_baseline(household_space_id)
    if baseline.household_space_id != household_space_id:
        raise RulePublicationRepositoryError(RulePublicationRepositoryErrorCode.BASELINE_INVALID)
    report = build_rule_publication_dry_run(package, baseline)
    write_rule_publication_dry_run_report(
        report,
        report_path,
        repository_root=repository_root,
    )
    return report


def apply_rule_publication_package(
    *,
    package_root: Path,
    report_path: Path,
    repository_root: Path,
    household_space_id: UUID,
    actor_id: UUID,
    approved_report_digest_sha256: str,
    publication_applier: RulePublicationApplier,
) -> AppliedRulePublication:
    package = load_rule_publication_package(
        package_root,
        repository_root=repository_root,
    )
    report = load_rule_publication_dry_run_report(
        report_path,
        repository_root=repository_root,
    )
    if not hmac.compare_digest(
        report.report_digest_sha256,
        approved_report_digest_sha256,
    ):
        raise RulePublicationRepositoryError(RulePublicationRepositoryErrorCode.APPROVAL_INVALID)
    return publication_applier.apply(
        package,
        household_space_id=household_space_id,
        actor_id=actor_id,
        approved_report=report,
    )

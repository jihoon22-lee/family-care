"""Service boundary for approved private rule publications."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.private_knowledge.publication_package import (
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_reconciliation import (
    load_rule_publication_dry_run_report,
)
from familycare_api.private_knowledge.publication_repository import (
    AppliedRulePublication,
    RulePublicationRepositoryError,
    RulePublicationRepositoryErrorCode,
)
from familycare_api.private_knowledge.publication_service import (
    apply_rule_publication_package,
    prepare_rule_publication_dry_run,
)

from apps.api.tests.private_knowledge_publication_fixtures import (
    write_synthetic_rule_publication_package,
)
from apps.api.tests.test_private_knowledge_publication_reconciliation import (
    HOUSEHOLD_ID,
    PUBLICATION_RUN_ID,
    _baseline,
)

ACTOR_ID = UUID("00000000-0000-4000-8000-000000004101")


class FakePublicationRepository:
    def __init__(self, baseline) -> None:
        self.baseline = baseline
        self.applied_report = None

    def read_baseline(self, household_space_id: UUID):
        assert household_space_id == HOUSEHOLD_ID
        return self.baseline

    def apply(
        self,
        package,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report,
    ) -> AppliedRulePublication:
        assert household_space_id == HOUSEHOLD_ID
        assert actor_id == ACTOR_ID
        self.applied_report = approved_report
        return AppliedRulePublication(
            run_id=PUBLICATION_RUN_ID,
            package_digest_sha256=package.package_digest_sha256,
            state="APPLIED",
            is_current=True,
            counts=package.reconciliation,
            dispositions=approved_report.dispositions,
        )


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    package_root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    report_parent = tmp_path / "reports"
    report_parent.mkdir(mode=0o700)
    report_parent.chmod(0o700)
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    return package_root, report_parent / "dry-run.json", repository_root


def test_prepare_writes_and_rereads_mode_0600_count_only_report(tmp_path: Path) -> None:
    package_root, report_path, repository_root = _paths(tmp_path)
    package = load_rule_publication_package(
        package_root,
        repository_root=repository_root,
    )
    repository = FakePublicationRepository(_baseline(package))

    report = prepare_rule_publication_dry_run(
        package_root=package_root,
        report_path=report_path,
        repository_root=repository_root,
        household_space_id=HOUSEHOLD_ID,
        baseline_reader=repository,
    )

    assert report.operation == "CREATE"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert (
        load_rule_publication_dry_run_report(
            report_path,
            repository_root=repository_root,
        )
        == report
    )


def test_apply_requires_exact_approved_digest_and_reloads_inputs(tmp_path: Path) -> None:
    package_root, report_path, repository_root = _paths(tmp_path)
    package = load_rule_publication_package(
        package_root,
        repository_root=repository_root,
    )
    repository = FakePublicationRepository(_baseline(package))
    report = prepare_rule_publication_dry_run(
        package_root=package_root,
        report_path=report_path,
        repository_root=repository_root,
        household_space_id=HOUSEHOLD_ID,
        baseline_reader=repository,
    )

    with pytest.raises(RulePublicationRepositoryError) as unapproved:
        apply_rule_publication_package(
            package_root=package_root,
            report_path=report_path,
            repository_root=repository_root,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report_digest_sha256="9" * 64,
            publication_applier=repository,
        )
    assert unapproved.value.code is RulePublicationRepositoryErrorCode.APPROVAL_INVALID

    applied = apply_rule_publication_package(
        package_root=package_root,
        report_path=report_path,
        repository_root=repository_root,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report_digest_sha256=report.report_digest_sha256,
        publication_applier=repository,
    )

    assert applied.run_id == PUBLICATION_RUN_ID
    assert repository.applied_report == report

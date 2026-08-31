"""Environment-only operator CLI for private knowledge snapshots."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Never
from uuid import UUID

from familycare_api.private_knowledge.confirmations import (
    ConfirmationError,
    ConfirmationErrorCode,
    apply_confirmation_manifest,
    load_confirmation_manifest,
    prepare_confirmation_dry_run,
)
from familycare_api.private_knowledge.errors import (
    PrivateKnowledgePackageError,
    PublicationPackageError,
)
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.publication_models import (
    PublicationCounts,
    PublicationCountsV2,
)
from familycare_api.private_knowledge.publication_package import (
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_reconciliation import (
    PublicationReconciliationError,
)
from familycare_api.private_knowledge.publication_repository import (
    PostgresRulePublicationRepository,
    RulePublicationRepositoryError,
)
from familycare_api.private_knowledge.publication_service import (
    apply_rule_publication_package,
    prepare_rule_publication_dry_run,
)
from familycare_api.private_knowledge.reconciliation import (
    KnowledgeEntityCounts,
    PrivateKnowledgeReconciliationError,
    package_entity_counts,
)
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
    PrivateKnowledgeRepositoryError,
)
from familycare_api.private_knowledge.service import (
    apply_private_knowledge_snapshot,
    prepare_private_knowledge_dry_run,
)

_PACKAGE_ROOT = "FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT"
_REPORT_PATH = "FAMILYCARE_PRIVATE_KNOWLEDGE_REPORT_PATH"
_HOUSEHOLD_ID = "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID"
_ACTOR_ID = "FAMILYCARE_PRIVATE_KNOWLEDGE_ACTOR_ID"
_APPROVAL_DIGEST = "FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST"
_DATABASE_URL = "FAMILYCARE_DATABASE_URL"
_CONFIRMATION_MANIFEST_PATH = "FAMILYCARE_PRIVATE_CONFIRMATION_MANIFEST_PATH"
_CONFIRMATION_REPORT_PATH = "FAMILYCARE_PRIVATE_CONFIRMATION_REPORT_PATH"
_RULE_PACKAGE_ROOT = "FAMILYCARE_PRIVATE_RULE_PACKAGE_ROOT"
_RULE_REPORT_PATH = "FAMILYCARE_PRIVATE_RULE_REPORT_PATH"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PrivateKnowledgeCliErrorCode(StrEnum):
    ENVIRONMENT_REQUIRED = "ENVIRONMENT_REQUIRED"
    ENVIRONMENT_INVALID = "ENVIRONMENT_INVALID"


class PrivateKnowledgeCliError(RuntimeError):
    def __init__(self, code: PrivateKnowledgeCliErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        self.print_usage(sys.stderr)
        self.exit(2, "familycare-private-knowledge: invalid arguments\n")


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value:
        raise PrivateKnowledgeCliError(PrivateKnowledgeCliErrorCode.ENVIRONMENT_REQUIRED)
    return value


def _path_environment(name: str) -> Path:
    value = _required_environment(name)
    if "\x00" in value:
        raise PrivateKnowledgeCliError(PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID)
    return Path(value)


def _uuid_environment(name: str) -> UUID:
    try:
        return UUID(_required_environment(name))
    except ValueError:
        raise PrivateKnowledgeCliError(PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID) from None


def _approval_digest() -> str:
    value = _required_environment(_APPROVAL_DIGEST)
    if _SHA256.fullmatch(value) is None:
        raise PrivateKnowledgeCliError(PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID)
    return value


def _trusted_repository_root() -> Path:
    """Derive the protected application root from the installed runtime."""

    try:
        runtime_prefix = Path(sys.prefix).resolve(strict=True)
        repository_root = runtime_prefix.parent.resolve(strict=True)
    except OSError:
        raise PrivateKnowledgeCliError(PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID) from None
    if (
        not runtime_prefix.is_dir()
        or not repository_root.is_dir()
        or not repository_root.is_absolute()
    ):
        raise PrivateKnowledgeCliError(PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID)
    return repository_root


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="familycare-private-knowledge")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_SafeArgumentParser,
    )
    subparsers.add_parser("validate")
    subparsers.add_parser("dry-run")
    subparsers.add_parser("apply")
    subparsers.add_parser("verify")
    subparsers.add_parser("confirmation-dry-run")
    subparsers.add_parser("confirmation-apply")
    subparsers.add_parser("confirmation-verify")
    subparsers.add_parser("publication-validate")
    subparsers.add_parser("publication-dry-run")
    subparsers.add_parser("publication-apply")
    subparsers.add_parser("publication-verify")
    return parser


def _print_counts(
    *,
    status: str,
    counts: KnowledgeEntityCounts,
    run_id: UUID | None = None,
) -> None:
    fields = [f"status={status}"]
    if run_id is not None:
        fields.append(f"run_id={run_id}")
    fields.extend(
        [
            f"subjects={counts.subjects}",
            f"contracts={counts.contracts}",
            f"coverages={counts.coverages}",
            f"sections={counts.terms_sections}",
            f"clauses={counts.source_clauses}",
            f"facts={counts.facts}",
            f"citations={counts.fact_citations}",
        ]
    )
    print(" ".join(fields))


def _print_confirmation_counts(
    *,
    status: str,
    subjects: int,
    contracts: int,
    run_id: UUID | None = None,
    binding_changes: int | None = None,
    confirmation_changes: int | None = None,
) -> None:
    fields = [f"status={status}"]
    if run_id is not None:
        fields.append(f"run_id={run_id}")
    fields.extend((f"subjects={subjects}", f"contracts={contracts}"))
    if binding_changes is not None:
        fields.append(f"binding_changes={binding_changes}")
    if confirmation_changes is not None:
        fields.append(f"confirmation_changes={confirmation_changes}")
    print(" ".join(fields))


def _print_publication_counts(
    *,
    status: str,
    counts: PublicationCounts | PublicationCountsV2,
    run_id: UUID | None = None,
) -> None:
    fields = [f"status={status}"]
    if run_id is not None:
        fields.append(f"run_id={run_id}")
    fields.extend(
        (
            f"subjects={counts.subject_count}",
            f"contracts={counts.contract_count}",
            f"coverages={counts.coverage_count}",
            f"published={counts.published_disposition_count}",
            f"advisory={getattr(counts, 'advisory_disposition_count', 0)}",
            f"user_confirmed_enrollment={getattr(counts, 'user_confirmed_enrollment_count', 0)}",
            f"blocked={counts.blocked_disposition_count}",
            f"not_applicable={counts.not_applicable_disposition_count}",
            f"rules={counts.rule_publication_count}",
            f"calculations={counts.calculation_publication_count}",
            f"citations={counts.rule_citation_count + counts.calculation_citation_count}",
        )
    )
    print(" ".join(fields))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repository_root = _trusted_repository_root()
        if args.command == "publication-validate":
            publication_package = load_rule_publication_package(
                _path_environment(_RULE_PACKAGE_ROOT),
                repository_root=repository_root,
            )
            _print_publication_counts(
                status="PUBLICATION_VALIDATED",
                counts=publication_package.reconciliation,
            )
        elif args.command == "publication-dry-run":
            publication_repository = PostgresRulePublicationRepository(
                _required_environment(_DATABASE_URL)
            )
            publication_report = prepare_rule_publication_dry_run(
                package_root=_path_environment(_RULE_PACKAGE_ROOT),
                report_path=_path_environment(_RULE_REPORT_PATH),
                repository_root=repository_root,
                household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                baseline_reader=publication_repository,
            )
            _print_publication_counts(
                status=f"PUBLICATION_DRY_RUN_{publication_report.operation}",
                counts=publication_report.expected_current_counts,
            )
        elif args.command == "publication-apply":
            publication_repository = PostgresRulePublicationRepository(
                _required_environment(_DATABASE_URL)
            )
            publication_applied = apply_rule_publication_package(
                package_root=_path_environment(_RULE_PACKAGE_ROOT),
                report_path=_path_environment(_RULE_REPORT_PATH),
                repository_root=repository_root,
                household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                actor_id=_uuid_environment(_ACTOR_ID),
                approved_report_digest_sha256=_approval_digest(),
                publication_applier=publication_repository,
            )
            _print_publication_counts(
                status="PUBLICATION_APPLIED",
                run_id=publication_applied.run_id,
                counts=publication_applied.counts,
            )
        elif args.command == "publication-verify":
            publication_repository = PostgresRulePublicationRepository(
                _required_environment(_DATABASE_URL)
            )
            publication_verified = publication_repository.verify_current(
                _uuid_environment(_HOUSEHOLD_ID)
            )
            _print_publication_counts(
                status="PUBLICATION_VERIFIED",
                run_id=publication_verified.run_id,
                counts=publication_verified.counts,
            )
        elif args.command == "validate":
            package = load_private_knowledge_package(
                _path_environment(_PACKAGE_ROOT),
                repository_root=repository_root,
            )
            _print_counts(status="VALIDATED", counts=package_entity_counts(package))
        elif args.command == "dry-run":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            knowledge_report = prepare_private_knowledge_dry_run(
                package_root=_path_environment(_PACKAGE_ROOT),
                report_path=_path_environment(_REPORT_PATH),
                repository_root=repository_root,
                household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                baseline_reader=repository,
            )
            _print_counts(
                status=f"DRY_RUN_{knowledge_report.operation}",
                counts=knowledge_report.expected_current_counts,
            )
        elif args.command == "apply":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            knowledge_applied = apply_private_knowledge_snapshot(
                package_root=_path_environment(_PACKAGE_ROOT),
                report_path=_path_environment(_REPORT_PATH),
                repository_root=repository_root,
                household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                actor_id=_uuid_environment(_ACTOR_ID),
                approved_report_digest_sha256=_approval_digest(),
                snapshot_applier=repository,
            )
            _print_counts(
                status="APPLIED",
                run_id=knowledge_applied.run_id,
                counts=knowledge_applied.counts,
            )
        elif args.command == "verify":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            verified = repository.verify_current(_uuid_environment(_HOUSEHOLD_ID))
            _print_counts(
                status="VERIFIED",
                run_id=verified.run_id,
                counts=verified.counts,
            )
        elif args.command == "confirmation-dry-run":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            confirmation_report = prepare_confirmation_dry_run(
                manifest_path=_path_environment(_CONFIRMATION_MANIFEST_PATH),
                report_path=_path_environment(_CONFIRMATION_REPORT_PATH),
                repository_root=repository_root,
                expected_household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                repository=repository,
            )
            _print_confirmation_counts(
                status=f"CONFIRMATION_DRY_RUN_{confirmation_report.operation}",
                subjects=confirmation_report.subject_count,
                contracts=confirmation_report.contract_count,
                run_id=confirmation_report.current_run_id,
                binding_changes=confirmation_report.binding_change_count,
                confirmation_changes=confirmation_report.confirmation_insert_count,
            )
        elif args.command == "confirmation-apply":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            confirmation_applied = apply_confirmation_manifest(
                manifest_path=_path_environment(_CONFIRMATION_MANIFEST_PATH),
                report_path=_path_environment(_CONFIRMATION_REPORT_PATH),
                repository_root=repository_root,
                expected_household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                approved_report_digest_sha256=_approval_digest(),
                repository=repository,
            )
            _print_confirmation_counts(
                status="CONFIRMATIONS_APPLIED",
                subjects=confirmation_applied.subject_count,
                contracts=confirmation_applied.contract_count,
                run_id=confirmation_applied.run_id,
            )
        else:
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            manifest = load_confirmation_manifest(
                _path_environment(_CONFIRMATION_MANIFEST_PATH),
                repository_root=repository_root,
            )
            if manifest.household_space_id != _uuid_environment(_HOUSEHOLD_ID):
                raise ConfirmationError(ConfirmationErrorCode.MANIFEST_SCOPE_MISMATCH)
            verification_report = repository.prepare_confirmation_dry_run(manifest)
            if verification_report.operation != "NO_OP":
                raise ConfirmationError(ConfirmationErrorCode.VERIFICATION_FAILED)
            _print_confirmation_counts(
                status="CONFIRMATIONS_VERIFIED",
                subjects=verification_report.subject_count,
                contracts=verification_report.contract_count,
                run_id=verification_report.current_run_id,
            )
        return 0
    except (
        PrivateKnowledgeCliError,
        ConfirmationError,
        PrivateKnowledgePackageError,
        PrivateKnowledgeReconciliationError,
        PrivateKnowledgeRepositoryError,
        PublicationPackageError,
        PublicationReconciliationError,
        RulePublicationRepositoryError,
    ) as error:
        code = getattr(error, "code", PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID)
        print(f"familycare-private-knowledge: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]

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

from familycare_api.private_knowledge.errors import PrivateKnowledgePackageError
from familycare_api.private_knowledge.package import load_private_knowledge_package
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
_REPOSITORY_ROOT = "FAMILYCARE_PRIVATE_KNOWLEDGE_REPOSITORY_ROOT"
_HOUSEHOLD_ID = "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID"
_ACTOR_ID = "FAMILYCARE_PRIVATE_KNOWLEDGE_ACTOR_ID"
_APPROVAL_DIGEST = "FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST"
_DATABASE_URL = "FAMILYCARE_DATABASE_URL"
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            package = load_private_knowledge_package(
                _path_environment(_PACKAGE_ROOT),
                repository_root=_path_environment(_REPOSITORY_ROOT),
            )
            _print_counts(status="VALIDATED", counts=package_entity_counts(package))
        elif args.command == "dry-run":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            report = prepare_private_knowledge_dry_run(
                package_root=_path_environment(_PACKAGE_ROOT),
                report_path=_path_environment(_REPORT_PATH),
                repository_root=_path_environment(_REPOSITORY_ROOT),
                household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                baseline_reader=repository,
            )
            _print_counts(
                status=f"DRY_RUN_{report.operation}",
                counts=report.expected_current_counts,
            )
        elif args.command == "apply":
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            applied = apply_private_knowledge_snapshot(
                package_root=_path_environment(_PACKAGE_ROOT),
                report_path=_path_environment(_REPORT_PATH),
                repository_root=_path_environment(_REPOSITORY_ROOT),
                household_space_id=_uuid_environment(_HOUSEHOLD_ID),
                actor_id=_uuid_environment(_ACTOR_ID),
                approved_report_digest_sha256=_approval_digest(),
                snapshot_applier=repository,
            )
            _print_counts(
                status="APPLIED",
                run_id=applied.run_id,
                counts=applied.counts,
            )
        else:
            repository = PostgresPrivateKnowledgeRepository(_required_environment(_DATABASE_URL))
            verified = repository.verify_current(_uuid_environment(_HOUSEHOLD_ID))
            _print_counts(
                status="VERIFIED",
                run_id=verified.run_id,
                counts=verified.counts,
            )
        return 0
    except (
        PrivateKnowledgeCliError,
        PrivateKnowledgePackageError,
        PrivateKnowledgeReconciliationError,
        PrivateKnowledgeRepositoryError,
    ) as error:
        code = getattr(error, "code", PrivateKnowledgeCliErrorCode.ENVIRONMENT_INVALID)
        print(f"familycare-private-knowledge: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]

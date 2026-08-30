"""Atomic PostgreSQL publication apply, verification, and drift detection."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from familycare_api.private_knowledge.errors import PublicationPackageError
from familycare_api.private_knowledge.publication_models import PublicationCounts
from familycare_api.private_knowledge.publication_package import (
    RulePublicationPackage,
    validate_loaded_rule_publication_package,
)
from familycare_api.private_knowledge.publication_reconciliation import (
    DispositionCounts,
    PublicationCoverageBaseline,
    PublicationDatabaseBaseline,
    PublicationEvidenceBaseline,
    RulePublicationDryRunReport,
    build_rule_publication_dry_run,
    canonical_rule_publication_report_digest,
)


class RulePublicationRepositoryErrorCode(StrEnum):
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    HOUSEHOLD_NOT_FOUND = "HOUSEHOLD_NOT_FOUND"
    CURRENT_KNOWLEDGE_NOT_FOUND = "CURRENT_KNOWLEDGE_NOT_FOUND"
    TRANSACTION_MODE_INVALID = "TRANSACTION_MODE_INVALID"
    BASELINE_INVALID = "BASELINE_INVALID"
    ACTOR_NOT_FOUND = "ACTOR_NOT_FOUND"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    STALE_DRY_RUN = "STALE_DRY_RUN"
    APPLY_BLOCKED = "APPLY_BLOCKED"
    COUNT_MISMATCH = "COUNT_MISMATCH"
    APPLY_FAILED = "APPLY_FAILED"
    CURRENT_NOT_FOUND = "CURRENT_NOT_FOUND"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class RulePublicationRepositoryError(RuntimeError):
    def __init__(self, code: RulePublicationRepositoryErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class RulePublicationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: UUID
    package_digest_sha256: str
    state: Literal["APPLIED"]
    is_current: Literal[True]
    counts: PublicationCounts
    dispositions: DispositionCounts


class AppliedRulePublication(RulePublicationSummary):
    """Successful create, supersede, or idempotent publication result."""


_PUBLICATION_TABLES = (
    ("status_intervals", "private_knowledge_contract_status_intervals"),
    ("dispositions", "private_knowledge_coverage_execution_dispositions"),
    ("fact_normalizers", "private_knowledge_fact_normalizer_publications"),
    ("rule_publications", "private_knowledge_rule_publications"),
    ("rule_citations", "private_knowledge_rule_citations"),
    ("calculation_publications", "private_knowledge_calculation_publications"),
    ("calculation_citations", "private_knowledge_calculation_citations"),
)


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise RulePublicationRepositoryError(
            RulePublicationRepositoryErrorCode.DATABASE_UNAVAILABLE
        )
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _prefixed_digest(prefix: bytes, value: object) -> str:
    return hashlib.sha256(prefix + b"\x00" + _canonical_json(value)).hexdigest()


def _baseline_digest(value: object) -> str:
    return _prefixed_digest(b"familycare-private-rule-baseline-v1", value)


def _actor_digest(value: object) -> str:
    return _prefixed_digest(b"familycare-private-rule-actors-v1", value)


def _projection_digest(value: object) -> str:
    return _prefixed_digest(b"familycare-private-rule-projection-v1", value)


def _record_digest(value: object) -> str:
    return _prefixed_digest(b"familycare-private-rule-record-v1", value)


def _advisory_lock_key(household_space_id: UUID) -> int:
    payload = hashlib.sha256(
        b"familycare-private-rule-publication-lock\x00" + household_space_id.bytes
    ).digest()
    return int.from_bytes(payload[:8], byteorder="big", signed=True)


def _disposition_counts(values: Sequence[str]) -> DispositionCounts:
    return DispositionCounts(
        published=values.count("PUBLISHED"),
        blocked=values.count("BLOCKED"),
        not_applicable=values.count("NOT_APPLICABLE"),
    )


def _citation_identity(value: object) -> tuple[object, ...]:
    if isinstance(value, PublicationEvidenceBaseline):
        return (
            value.canonical_policy_id,
            value.terms_source_alias,
            value.source_section_key,
            value.source_clause_index,
            value.source_fact_key,
        )
    record = cast(Any, value)
    return (
        record.canonical_policy_id,
        record.terms_source_alias,
        record.source_section_key,
        record.source_clause_index,
        record.source_fact_key,
    )


class PostgresRulePublicationRepository:
    """Publish one reviewed rule package against one exact knowledge snapshot."""

    def __init__(
        self,
        database_url: str,
        *,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.database_url = _database_url(database_url)
        self._failure_injector = failure_injector

    def _after_group(self, stage: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(stage)

    def read_baseline(
        self,
        household_space_id: UUID,
    ) -> PublicationDatabaseBaseline:
        """Read a repeatable, non-mutating publication baseline."""

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    self._require_transaction_mode(connection, read_only=True)
                    baseline = self._read_baseline(connection, household_space_id)
                    self._require_unassigned_transaction_id(connection)
                    return baseline
        except RulePublicationRepositoryError:
            raise
        except psycopg.Error:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.DATABASE_UNAVAILABLE
            ) from None
        except KeyError, TypeError, ValueError:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.BASELINE_INVALID
            ) from None

    def prepare_dry_run(
        self,
        package: RulePublicationPackage,
        *,
        household_space_id: UUID,
    ) -> RulePublicationDryRunReport:
        try:
            validate_loaded_rule_publication_package(package)
        except PublicationPackageError:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.APPROVAL_INVALID
            ) from None
        return build_rule_publication_dry_run(
            package,
            self.read_baseline(household_space_id),
        )

    @staticmethod
    def _require_transaction_mode(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        read_only: bool,
    ) -> None:
        row = connection.execute(
            """
            SELECT current_setting('transaction_isolation') AS isolation,
                   current_setting('transaction_read_only') AS read_only
            """
        ).fetchone()
        expected = "on" if read_only else "off"
        if row != {"isolation": "repeatable read", "read_only": expected}:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.TRANSACTION_MODE_INVALID
            )

    @staticmethod
    def _require_unassigned_transaction_id(
        connection: psycopg.Connection[dict[str, Any]],
    ) -> None:
        row = connection.execute("SELECT txid_current_if_assigned() AS transaction_id").fetchone()
        if row is None or row["transaction_id"] is not None:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.TRANSACTION_MODE_INVALID
            )

    def _read_baseline(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        household_space_id: UUID,
    ) -> PublicationDatabaseBaseline:
        household = connection.execute(
            """
            SELECT 1 FROM household_spaces
            WHERE id = %s AND deleted_at IS NULL
            """,
            (household_space_id,),
        ).fetchone()
        if household is None:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.HOUSEHOLD_NOT_FOUND
            )

        knowledge_rows = connection.execute(
            """
            SELECT id, package_digest_sha256, projection_digest_sha256,
                   state, is_current
            FROM private_knowledge_import_runs
            WHERE household_space_id = %s
            ORDER BY created_at, id
            """,
            (household_space_id,),
        ).fetchall()
        current_knowledge = [
            row for row in knowledge_rows if row["state"] == "APPLIED" and row["is_current"] is True
        ]
        if len(current_knowledge) != 1:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.CURRENT_KNOWLEDGE_NOT_FOUND
            )
        knowledge = current_knowledge[0]
        knowledge_run_id = cast(UUID, knowledge["id"])
        knowledge_projection = knowledge["projection_digest_sha256"]
        if not isinstance(knowledge_projection, str):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.BASELINE_INVALID
            )

        coverage_rows = connection.execute(
            """
            SELECT coverage.knowledge_contract_id,
                   coverage.id AS knowledge_coverage_id,
                   subject.source_subject_key,
                   subject.family_alias,
                   contract.source_record_json ->> 'canonical_policy_id'
                     AS canonical_policy_id,
                   coverage.source_record_json ->> 'canonical_rider_id'
                     AS canonical_coverage_id,
                   subject.binding_decision AS subject_binding_decision,
                   coverage.enrollment_decision,
                   coverage.component_classification,
                   coverage.benefit_type,
                   mapping.mapping_applicability,
                   mapping.enrollment_decision AS mapping_enrollment_decision,
                   mapping.document_identity_decision,
                   mapping.edition_applicability_decision,
                   mapping.section_mapping_decision,
                   mapping.overall_decision AS overall_mapping_decision,
                   confirmation.decision AS current_confirmation_decision,
                   confirmation.confirmed_status AS current_confirmed_status
            FROM private_knowledge_coverages AS coverage
            JOIN private_knowledge_contracts AS contract
              ON contract.id = coverage.knowledge_contract_id
             AND contract.import_run_id = coverage.import_run_id
            JOIN private_knowledge_subjects AS subject
              ON subject.id = contract.subject_id
             AND subject.import_run_id = contract.import_run_id
            LEFT JOIN private_knowledge_coverage_terms_mappings AS mapping
              ON mapping.coverage_id = coverage.id
             AND mapping.import_run_id = coverage.import_run_id
            LEFT JOIN private_knowledge_contract_confirmations AS confirmation
              ON confirmation.knowledge_contract_id = contract.id
             AND confirmation.import_run_id = contract.import_run_id
             AND confirmation.is_current
            WHERE coverage.import_run_id = %s
            ORDER BY canonical_coverage_id, coverage.id, mapping.id
            """,
            (knowledge_run_id,),
        ).fetchall()
        if not coverage_rows:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.BASELINE_INVALID
            )
        coverage_keys = [row["canonical_coverage_id"] for row in coverage_rows]
        if any(not isinstance(value, str) or not value for value in coverage_keys) or len(
            set(coverage_keys)
        ) != len(coverage_keys):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.BASELINE_INVALID
            )
        coverages = tuple(PublicationCoverageBaseline.model_validate(row) for row in coverage_rows)

        evidence_rows = self._evidence_rows(connection, knowledge_run_id)
        evidence = tuple(PublicationEvidenceBaseline.model_validate(row) for row in evidence_rows)
        evidence_keys = [_citation_identity(item) for item in evidence]
        if len(set(evidence_keys)) != len(evidence_keys):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.BASELINE_INVALID
            )

        actor_rows = self._json_records(
            connection,
            """
            SELECT jsonb_build_object(
                     'id', id,
                     'is_active', is_active,
                     'updated_at', updated_at,
                     'deactivated_at', deactivated_at
                   ) AS record
            FROM app_users
            WHERE household_space_id = %s
            ORDER BY id
            """,
            household_space_id,
        )
        actor_identity_digest = _actor_digest(actor_rows)

        publication_rows = connection.execute(
            """
            SELECT id, package_digest_sha256, state, is_current,
                   entity_counts_json, disposition_counts_json,
                   projection_digest_sha256
            FROM private_knowledge_rule_import_runs
            WHERE household_space_id = %s
            ORDER BY created_at, id
            """,
            (household_space_id,),
        ).fetchall()
        current_publications = [
            row
            for row in publication_rows
            if row["state"] == "APPLIED" and row["is_current"] is True
        ]
        if len(current_publications) > 1:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.BASELINE_INVALID
            )
        current_publication = current_publications[0] if current_publications else None
        current_counts = PublicationCounts.zero()
        current_dispositions = DispositionCounts.zero()
        current_publication_run_id: UUID | None = None
        current_publication_digest: str | None = None
        if current_publication is not None:
            current_publication_run_id = cast(UUID, current_publication["id"])
            current_publication_digest = cast(
                str,
                current_publication["package_digest_sha256"],
            )
            current_counts = PublicationCounts.model_validate(
                current_publication["entity_counts_json"]
            )
            current_dispositions = DispositionCounts.model_validate(
                current_publication["disposition_counts_json"]
            )

        fingerprint = {
            "knowledge": {
                "run_id": str(knowledge_run_id),
                "package_digest_sha256": knowledge["package_digest_sha256"],
                "projection_digest_sha256": knowledge_projection,
            },
            "coverage_authorities": [item.model_dump(mode="json") for item in coverages],
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "actors": actor_rows,
            "publication_runs": [
                {
                    "id": str(row["id"]),
                    "package_digest_sha256": row["package_digest_sha256"],
                    "state": row["state"],
                    "is_current": row["is_current"],
                    "entity_counts_json": row["entity_counts_json"],
                    "disposition_counts_json": row["disposition_counts_json"],
                    "projection_digest_sha256": row["projection_digest_sha256"],
                }
                for row in publication_rows
            ],
        }
        return PublicationDatabaseBaseline(
            household_space_id=household_space_id,
            baseline_digest_sha256=_baseline_digest(fingerprint),
            knowledge_import_run_id=knowledge_run_id,
            knowledge_package_digest_sha256=cast(str, knowledge["package_digest_sha256"]),
            knowledge_projection_digest_sha256=knowledge_projection,
            known_publication_digests=tuple(
                sorted(cast(str, row["package_digest_sha256"]) for row in publication_rows)
            ),
            current_publication_run_id=current_publication_run_id,
            current_publication_package_digest_sha256=current_publication_digest,
            current_publication_counts=current_counts,
            current_disposition_counts=current_dispositions,
            coverage_authorities=coverages,
            evidence=evidence,
            actor_identity_digest_sha256=actor_identity_digest,
        )

    @staticmethod
    def _json_records(
        connection: psycopg.Connection[dict[str, Any]],
        statement: str,
        *parameters: UUID,
    ) -> list[object]:
        return [row["record"] for row in connection.execute(statement, parameters).fetchall()]

    @staticmethod
    def _evidence_rows(
        connection: psycopg.Connection[dict[str, Any]],
        knowledge_run_id: UUID,
    ) -> list[dict[str, Any]]:
        return connection.execute(
            """
            WITH policy_sources AS (
              SELECT DISTINCT
                     contract.source_record_json ->> 'canonical_policy_id'
                       AS canonical_policy_id,
                     source.source_alias
              FROM private_knowledge_contracts AS contract
              JOIN private_knowledge_terms_assignments AS assignment
                ON assignment.knowledge_contract_id = contract.id
               AND assignment.import_run_id = contract.import_run_id
              JOIN private_knowledge_terms_assignment_sources AS source
                ON source.terms_assignment_id = assignment.id
               AND source.import_run_id = assignment.import_run_id
              WHERE contract.import_run_id = %(run)s
            ), section_evidence AS (
              SELECT section.id AS terms_section_id,
                     NULL::uuid AS source_clause_id,
                     NULL::uuid AS fact_id,
                     policy.canonical_policy_id,
                     section.terms_source_alias,
                     section.source_record_json ->> 'section_id'
                       AS source_section_key,
                     NULL::integer AS source_clause_index,
                     NULL::text AS source_fact_key,
                     section.page_start,
                     section.page_end,
                     section.source_record_digest_sha256 AS source_text_sha256
              FROM policy_sources AS policy
              JOIN private_knowledge_terms_sections AS section
                ON section.import_run_id = %(run)s
               AND section.terms_source_alias = policy.source_alias
            ), clause_evidence AS (
              SELECT section.id AS terms_section_id,
                     clause.id AS source_clause_id,
                     NULL::uuid AS fact_id,
                     policy.canonical_policy_id,
                     section.terms_source_alias,
                     section.source_record_json ->> 'section_id'
                       AS source_section_key,
                     (clause.source_record_json ->> 'clause_index')::integer
                       AS source_clause_index,
                     NULL::text AS source_fact_key,
                     clause.page_start,
                     clause.page_end,
                     clause.source_text_sha256
              FROM policy_sources AS policy
              JOIN private_knowledge_terms_sections AS section
                ON section.import_run_id = %(run)s
               AND section.terms_source_alias = policy.source_alias
              JOIN private_knowledge_source_clauses AS clause
                ON clause.terms_section_id = section.id
               AND clause.import_run_id = section.import_run_id
            ), fact_evidence AS (
              SELECT section.id AS terms_section_id,
                     clause.id AS source_clause_id,
                     fact.id AS fact_id,
                     policy.canonical_policy_id,
                     section.terms_source_alias,
                     section.source_record_json ->> 'section_id'
                       AS source_section_key,
                     (clause.source_record_json ->> 'clause_index')::integer
                       AS source_clause_index,
                     fact.source_record_json ->> 'fact_id' AS source_fact_key,
                     citation.page_start,
                     citation.page_end,
                     citation.source_text_sha256
              FROM policy_sources AS policy
              JOIN private_knowledge_terms_sections AS section
                ON section.import_run_id = %(run)s
               AND section.terms_source_alias = policy.source_alias
              JOIN private_knowledge_facts AS fact
                ON fact.terms_section_id = section.id
               AND fact.import_run_id = section.import_run_id
              JOIN private_knowledge_fact_citations AS citation
                ON citation.fact_id = fact.id
               AND citation.import_run_id = fact.import_run_id
              JOIN private_knowledge_source_clauses AS clause
                ON clause.id = citation.source_clause_id
               AND clause.import_run_id = citation.import_run_id
            )
            SELECT * FROM section_evidence
            UNION ALL SELECT * FROM clause_evidence
            UNION ALL SELECT * FROM fact_evidence
            ORDER BY canonical_policy_id, terms_source_alias, source_section_key,
                     source_clause_index NULLS FIRST, source_fact_key NULLS FIRST,
                     terms_section_id, source_clause_id, fact_id
            """,
            {"run": knowledge_run_id},
        ).fetchall()

    def apply(
        self,
        package: RulePublicationPackage,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report: RulePublicationDryRunReport,
    ) -> AppliedRulePublication:
        """Apply one exact approved report in one transaction."""

        try:
            validate_loaded_rule_publication_package(package)
        except PublicationPackageError:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.APPROVAL_INVALID
            ) from None
        if (
            approved_report.report_digest_sha256
            != canonical_rule_publication_report_digest(approved_report)
            or approved_report.package_digest_sha256 != package.package_digest_sha256
            or approved_report.knowledge_package_digest_sha256
            != package.manifest.source_knowledge_package_digest_sha256
            or approved_report.knowledge_snapshot_digest_sha256
            != package.manifest.source_knowledge_projection_digest_sha256
        ):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.APPROVAL_INVALID
            )
        if approved_report.operation == "BLOCKED" or approved_report.apply_block_count:
            raise RulePublicationRepositoryError(RulePublicationRepositoryErrorCode.APPLY_BLOCKED)

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                with connection.transaction():
                    self._require_transaction_mode(connection, read_only=False)
                    connection.execute(
                        """
                        LOCK TABLE household_spaces, app_users,
                          private_knowledge_import_runs,
                          private_knowledge_subjects,
                          private_knowledge_contracts,
                          private_knowledge_coverages,
                          private_knowledge_terms_assignments,
                          private_knowledge_terms_assignment_sources,
                          private_knowledge_terms_sections,
                          private_knowledge_source_clauses,
                          private_knowledge_facts,
                          private_knowledge_fact_citations,
                          private_knowledge_coverage_terms_mappings,
                          private_knowledge_contract_confirmations,
                          private_knowledge_rule_import_runs,
                          private_knowledge_contract_status_intervals,
                          private_knowledge_coverage_execution_dispositions,
                          private_knowledge_fact_normalizer_publications,
                          private_knowledge_rule_publications,
                          private_knowledge_rule_citations,
                          private_knowledge_calculation_publications,
                          private_knowledge_calculation_citations
                        IN SHARE ROW EXCLUSIVE MODE
                        """
                    )
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (_advisory_lock_key(household_space_id),),
                    )
                    actor = connection.execute(
                        """
                        SELECT 1 FROM app_users
                        WHERE id = %s AND household_space_id = %s AND is_active
                        """,
                        (actor_id, household_space_id),
                    ).fetchone()
                    if actor is None:
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.ACTOR_NOT_FOUND
                        )

                    baseline = self._read_baseline(connection, household_space_id)
                    current_report = build_rule_publication_dry_run(package, baseline)
                    if current_report != approved_report:
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.STALE_DRY_RUN
                        )
                    if current_report.operation == "BLOCKED":
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.APPLY_BLOCKED
                        )
                    if current_report.operation == "NO_OP":
                        if baseline.current_publication_run_id is None:
                            raise RulePublicationRepositoryError(
                                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
                            )
                        summary = self._verify_run(
                            connection,
                            household_space_id=household_space_id,
                            run_id=baseline.current_publication_run_id,
                        )
                        return AppliedRulePublication.model_validate(summary.model_dump())
                    if current_report.operation not in {"CREATE", "SUPERSEDE"}:
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.APPROVAL_INVALID
                        )

                    run_id = uuid4()
                    connection.execute(
                        """
                        INSERT INTO private_knowledge_rule_import_runs (
                          id, knowledge_import_run_id, household_space_id,
                          package_schema_version, package_digest_sha256,
                          manifest_digest_sha256, baseline_digest_sha256,
                          report_digest_sha256, projection_digest_sha256,
                          publisher_version, state, review_state,
                          entity_counts_json, disposition_counts_json,
                          reviewed_by, reviewed_at, is_current
                        ) VALUES (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, 'APPLIED', 'USER_CONFIRMED', %s, %s,
                          %s, clock_timestamp(), false
                        )
                        """,
                        (
                            run_id,
                            baseline.knowledge_import_run_id,
                            household_space_id,
                            package.schema_version,
                            package.package_digest_sha256,
                            package.manifest_digest_sha256,
                            baseline.baseline_digest_sha256,
                            approved_report.report_digest_sha256,
                            "0" * 64,
                            package.manifest.publisher_version,
                            Jsonb(package.reconciliation.model_dump(mode="json")),
                            Jsonb(approved_report.dispositions.model_dump(mode="json")),
                            actor_id,
                        ),
                    )
                    self._after_group("publication_run")
                    self._insert_publication_records(
                        connection,
                        run_id=run_id,
                        household_space_id=household_space_id,
                        actor_id=actor_id,
                        baseline=baseline,
                        package=package,
                    )

                    actual_counts, actual_dispositions, records = self._publication_snapshot(
                        connection,
                        knowledge_run_id=baseline.knowledge_import_run_id,
                        publication_run_id=run_id,
                    )
                    if (
                        actual_counts != approved_report.expected_insert_counts
                        or actual_counts != package.reconciliation
                        or actual_dispositions != approved_report.dispositions
                    ):
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.COUNT_MISMATCH
                        )
                    projection = _projection_digest(records)
                    connection.execute(
                        """
                        UPDATE private_knowledge_rule_import_runs
                        SET projection_digest_sha256 = %s
                        WHERE id = %s AND household_space_id = %s
                        """,
                        (projection, run_id, household_space_id),
                    )
                    self._after_group("before_current_switch")
                    connection.execute(
                        """
                        UPDATE private_knowledge_rule_import_runs
                        SET state = 'SUPERSEDED', is_current = false,
                            superseded_at = clock_timestamp()
                        WHERE household_space_id = %s AND is_current
                        """,
                        (household_space_id,),
                    )
                    selected = connection.execute(
                        """
                        UPDATE private_knowledge_rule_import_runs
                        SET is_current = true
                        WHERE id = %s AND household_space_id = %s
                          AND state = 'APPLIED' AND NOT is_current
                        RETURNING id
                        """,
                        (run_id, household_space_id),
                    ).fetchone()
                    if selected is None:
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.APPLY_FAILED
                        )
                    summary = self._verify_run(
                        connection,
                        household_space_id=household_space_id,
                        run_id=run_id,
                    )
            return AppliedRulePublication.model_validate(summary.model_dump())
        except RulePublicationRepositoryError:
            raise
        except psycopg.Error:
            recovered = self._recover_applied_publication(
                household_space_id,
                package.package_digest_sha256,
            )
            if recovered is not None:
                return recovered
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.APPLY_FAILED
            ) from None
        except Exception:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.APPLY_FAILED
            ) from None

    def _insert_publication_records(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        *,
        run_id: UUID,
        household_space_id: UUID,
        actor_id: UUID,
        baseline: PublicationDatabaseBaseline,
        package: RulePublicationPackage,
    ) -> None:
        knowledge_run_id = baseline.knowledge_import_run_id
        contract_by_policy = {
            item.canonical_policy_id: item.knowledge_contract_id
            for item in baseline.coverage_authorities
        }
        coverage_by_key = {
            item.canonical_coverage_id: item.knowledge_coverage_id
            for item in baseline.coverage_authorities
        }
        evidence_by_key = {_citation_identity(item): item for item in baseline.evidence}
        rule_ids = {record.value.rule_key: uuid4() for record in package.rule_publications}
        calculation_ids = {
            record.value.calculation_key: uuid4() for record in package.calculation_publications
        }

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_contract_status_intervals (
              id, rule_import_run_id, import_run_id, household_space_id,
              knowledge_contract_id, decision, confirmed_status,
              effective_from, effective_through, authority, reason_code,
              review_state, confirmed_by, confirmed_at,
              interval_digest_sha256
            ) VALUES (
              %(id)s, %(rule_run)s, %(knowledge_run)s, %(household)s,
              %(contract)s, %(decision)s, %(status)s, %(start)s, %(end)s,
              %(authority)s, %(reason)s, 'USER_CONFIRMED', %(actor)s,
              clock_timestamp(), %(digest)s
            )
            """,
            [
                {
                    "id": uuid4(),
                    "rule_run": run_id,
                    "knowledge_run": knowledge_run_id,
                    "household": household_space_id,
                    "contract": contract_by_policy[record.value.canonical_policy_id],
                    "decision": record.value.decision,
                    "status": record.value.confirmed_status,
                    "start": record.value.effective_from,
                    "end": record.value.effective_through,
                    "authority": record.value.authority,
                    "reason": record.value.reason_code,
                    "actor": actor_id,
                    "digest": record.record_digest_sha256,
                }
                for record in package.status_intervals
            ],
        )
        self._after_group("status_intervals")

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_coverage_execution_dispositions (
              id, rule_import_run_id, knowledge_import_run_id,
              household_space_id, knowledge_coverage_id, disposition,
              reason_codes_json
            ) VALUES (
              %(id)s, %(rule_run)s, %(knowledge_run)s, %(household)s,
              %(coverage)s, %(disposition)s, %(reasons)s
            )
            """,
            [
                {
                    "id": uuid4(),
                    "rule_run": run_id,
                    "knowledge_run": knowledge_run_id,
                    "household": household_space_id,
                    "coverage": coverage_by_key[record.value.canonical_coverage_id],
                    "disposition": record.value.disposition,
                    "reasons": Jsonb(record.value.reason_codes),
                }
                for record in package.coverage_dispositions
            ],
        )
        self._after_group("dispositions")

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_fact_normalizer_publications (
              id, rule_import_run_id, knowledge_import_run_id,
              household_space_id, field_path, normalized_tokens_json,
              normalized_value_json, match_kind, priority, review_state,
              reviewed_by, reviewed_at, normalizer_digest_sha256
            ) VALUES (
              %(id)s, %(rule_run)s, %(knowledge_run)s, %(household)s,
              %(field)s, %(tokens)s, %(value)s, %(kind)s, %(priority)s,
              'USER_CONFIRMED', %(actor)s, clock_timestamp(), %(digest)s
            )
            """,
            [
                {
                    "id": uuid4(),
                    "rule_run": run_id,
                    "knowledge_run": knowledge_run_id,
                    "household": household_space_id,
                    "field": record.value.field_path,
                    "tokens": Jsonb(self._normalize_tokens(record.value.phrase)),
                    "value": Jsonb(record.value.normalized_value),
                    "kind": record.value.match_kind,
                    "priority": record.value.priority,
                    "actor": actor_id,
                    "digest": record.record_digest_sha256,
                }
                for record in package.fact_normalizers
            ],
        )
        self._after_group("fact_normalizers")

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_rule_publications (
              id, rule_import_run_id, knowledge_import_run_id,
              household_space_id, knowledge_coverage_id, rule_key,
              rule_kind, schema_version, required, rule_json,
              result_reason_code, review_state, reviewed_by, reviewed_at,
              rule_digest_sha256
            ) VALUES (
              %(id)s, %(rule_run)s, %(knowledge_run)s, %(household)s,
              %(coverage)s, %(key)s, %(kind)s, %(schema)s, %(required)s,
              %(document)s, %(reason)s, 'USER_CONFIRMED', %(actor)s,
              clock_timestamp(), %(digest)s
            )
            """,
            [
                {
                    "id": rule_ids[record.value.rule_key],
                    "rule_run": run_id,
                    "knowledge_run": knowledge_run_id,
                    "household": household_space_id,
                    "coverage": coverage_by_key[record.value.canonical_coverage_id],
                    "key": record.value.rule_key,
                    "kind": record.value.rule_kind,
                    "schema": record.value.schema_version,
                    "required": record.value.required,
                    "document": Jsonb(record.value.rule_document),
                    "reason": record.value.result_reason_code,
                    "actor": actor_id,
                    "digest": record.record_digest_sha256,
                }
                for record in package.rule_publications
            ],
        )
        self._after_group("rules")

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_rule_citations (
              id, rule_publication_id, rule_import_run_id,
              knowledge_import_run_id, household_space_id, terms_section_id,
              source_clause_id, fact_id, citation_key, evidence_purpose,
              page_start, page_end, source_text_sha256,
              citation_digest_sha256
            ) VALUES (
              %(id)s, %(publication)s, %(rule_run)s, %(knowledge_run)s,
              %(household)s, %(section)s, %(clause)s, %(fact)s,
              %(key)s, %(purpose)s, %(start)s, %(end)s, %(text_digest)s,
              %(digest)s
            )
            """,
            [
                self._citation_parameters(
                    record.value,
                    record_digest=record.record_digest_sha256,
                    parent_id=rule_ids[record.value.rule_key],
                    run_id=run_id,
                    knowledge_run_id=knowledge_run_id,
                    household_space_id=household_space_id,
                    evidence_by_key=evidence_by_key,
                )
                for record in package.rule_citations
            ],
        )
        self._after_group("rule_citations")

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_calculation_publications (
              id, rule_import_run_id, knowledge_import_run_id,
              household_space_id, knowledge_coverage_id, calculation_key,
              calculation_kind, schema_version, calculation_json,
              result_reason_code, review_state, reviewed_by, reviewed_at,
              calculation_digest_sha256
            ) VALUES (
              %(id)s, %(rule_run)s, %(knowledge_run)s, %(household)s,
              %(coverage)s, %(key)s, %(kind)s, %(schema)s, %(document)s,
              %(reason)s, 'USER_CONFIRMED', %(actor)s, clock_timestamp(),
              %(digest)s
            )
            """,
            [
                {
                    "id": calculation_ids[record.value.calculation_key],
                    "rule_run": run_id,
                    "knowledge_run": knowledge_run_id,
                    "household": household_space_id,
                    "coverage": coverage_by_key[record.value.canonical_coverage_id],
                    "key": record.value.calculation_key,
                    "kind": record.value.calculation_kind,
                    "schema": record.value.schema_version,
                    "document": Jsonb(record.value.calculation_document),
                    "reason": record.value.result_reason_code,
                    "actor": actor_id,
                    "digest": record.record_digest_sha256,
                }
                for record in package.calculation_publications
            ],
        )
        self._after_group("calculations")

        self._execute_many(
            connection,
            """
            INSERT INTO private_knowledge_calculation_citations (
              id, calculation_publication_id, rule_import_run_id,
              knowledge_import_run_id, household_space_id, terms_section_id,
              source_clause_id, fact_id, citation_key, evidence_purpose,
              page_start, page_end, source_text_sha256,
              citation_digest_sha256
            ) VALUES (
              %(id)s, %(publication)s, %(rule_run)s, %(knowledge_run)s,
              %(household)s, %(section)s, %(clause)s, %(fact)s,
              %(key)s, %(purpose)s, %(start)s, %(end)s, %(text_digest)s,
              %(digest)s
            )
            """,
            [
                self._citation_parameters(
                    record.value,
                    record_digest=record.record_digest_sha256,
                    parent_id=calculation_ids[record.value.calculation_key],
                    run_id=run_id,
                    knowledge_run_id=knowledge_run_id,
                    household_space_id=household_space_id,
                    evidence_by_key=evidence_by_key,
                )
                for record in package.calculation_citations
            ],
        )
        self._after_group("calculation_citations")

    @staticmethod
    def _normalize_tokens(value: str) -> list[str]:
        return unicodedata.normalize("NFKC", value).casefold().split()

    @staticmethod
    def _citation_parameters(
        citation: Any,
        *,
        record_digest: str,
        parent_id: UUID,
        run_id: UUID,
        knowledge_run_id: UUID,
        household_space_id: UUID,
        evidence_by_key: Mapping[tuple[object, ...], PublicationEvidenceBaseline],
    ) -> dict[str, object]:
        evidence = evidence_by_key[_citation_identity(citation)]
        return {
            "id": uuid4(),
            "publication": parent_id,
            "rule_run": run_id,
            "knowledge_run": knowledge_run_id,
            "household": household_space_id,
            "section": evidence.terms_section_id,
            "clause": evidence.source_clause_id,
            "fact": evidence.fact_id,
            "key": citation.citation_key,
            "purpose": citation.evidence_purpose,
            "start": citation.page_start,
            "end": citation.page_end,
            "text_digest": citation.source_text_sha256,
            "digest": record_digest,
        }

    @staticmethod
    def _execute_many(
        connection: psycopg.Connection[dict[str, Any]],
        statement: str,
        rows: list[dict[str, object]],
    ) -> None:
        if not rows:
            return
        with connection.cursor() as cursor:
            cursor.executemany(statement, rows)

    def verify_current(self, household_space_id: UUID) -> RulePublicationSummary:
        """Verify the current publication and all cited knowledge lineage."""

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    self._require_transaction_mode(connection, read_only=True)
                    row = connection.execute(
                        """
                        SELECT id
                        FROM private_knowledge_rule_import_runs
                        WHERE household_space_id = %s
                          AND state = 'APPLIED' AND is_current
                        """,
                        (household_space_id,),
                    ).fetchone()
                    if row is None:
                        raise RulePublicationRepositoryError(
                            RulePublicationRepositoryErrorCode.CURRENT_NOT_FOUND
                        )
                    summary = self._verify_run(
                        connection,
                        household_space_id=household_space_id,
                        run_id=cast(UUID, row["id"]),
                    )
                    self._require_unassigned_transaction_id(connection)
                    return summary
        except RulePublicationRepositoryError:
            raise
        except psycopg.Error:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.DATABASE_UNAVAILABLE
            ) from None
        except KeyError, TypeError, ValueError:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            ) from None

    def _verify_run(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        *,
        household_space_id: UUID,
        run_id: UUID,
    ) -> RulePublicationSummary:
        run = connection.execute(
            """
            SELECT publication.id, publication.knowledge_import_run_id,
                   publication.package_digest_sha256,
                   publication.projection_digest_sha256,
                   publication.state, publication.is_current,
                   publication.entity_counts_json,
                   publication.disposition_counts_json,
                   knowledge.state AS knowledge_state,
                   knowledge.is_current AS knowledge_is_current
            FROM private_knowledge_rule_import_runs AS publication
            JOIN private_knowledge_import_runs AS knowledge
              ON knowledge.id = publication.knowledge_import_run_id
             AND knowledge.household_space_id = publication.household_space_id
            WHERE publication.id = %s
              AND publication.household_space_id = %s
            """,
            (run_id, household_space_id),
        ).fetchone()
        if (
            run is None
            or run["state"] != "APPLIED"
            or run["is_current"] is not True
            or run["knowledge_state"] != "APPLIED"
            or run["knowledge_is_current"] is not True
        ):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            )
        knowledge_run_id = cast(UUID, run["knowledge_import_run_id"])
        expected_counts = PublicationCounts.model_validate(run["entity_counts_json"])
        expected_dispositions = DispositionCounts.model_validate(run["disposition_counts_json"])
        actual_counts, actual_dispositions, records = self._publication_snapshot(
            connection,
            knowledge_run_id=knowledge_run_id,
            publication_run_id=run_id,
        )
        if actual_counts != expected_counts:
            raise RulePublicationRepositoryError(RulePublicationRepositoryErrorCode.COUNT_MISMATCH)
        if actual_dispositions != expected_dispositions:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            )
        stored_projection = run["projection_digest_sha256"]
        if (
            not isinstance(stored_projection, str)
            or _projection_digest(records) != stored_projection
        ):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            )
        if self._citation_violation_count(
            connection,
            knowledge_run_id=knowledge_run_id,
            publication_run_id=run_id,
        ):
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            )
        return RulePublicationSummary(
            run_id=run_id,
            package_digest_sha256=cast(str, run["package_digest_sha256"]),
            state="APPLIED",
            is_current=True,
            counts=actual_counts,
            dispositions=actual_dispositions,
        )

    @staticmethod
    def _publication_snapshot(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        knowledge_run_id: UUID,
        publication_run_id: UUID,
    ) -> tuple[PublicationCounts, DispositionCounts, dict[str, list[object]]]:
        source_counts = connection.execute(
            """
            SELECT
              count(DISTINCT contract.subject_id) AS subject_count,
              count(DISTINCT coverage.knowledge_contract_id) AS contract_count,
              count(*) AS coverage_count
            FROM private_knowledge_coverage_execution_dispositions AS disposition
            JOIN private_knowledge_coverages AS coverage
              ON coverage.id = disposition.knowledge_coverage_id
             AND coverage.import_run_id = %(knowledge_run)s
            JOIN private_knowledge_contracts AS contract
              ON contract.id = coverage.knowledge_contract_id
             AND contract.import_run_id = coverage.import_run_id
            WHERE disposition.rule_import_run_id = %(publication_run)s
            """,
            {
                "knowledge_run": knowledge_run_id,
                "publication_run": publication_run_id,
            },
        ).fetchone()
        if source_counts is None:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            )
        records: dict[str, list[object]] = {}
        child_counts: dict[str, int] = {}
        for key, table_name in _PUBLICATION_TABLES:
            rows = connection.execute(
                f"""
                SELECT to_jsonb(item) - 'created_at' AS record
                FROM {table_name} AS item
                WHERE item.rule_import_run_id = %s
                ORDER BY item.id
                """,
                (publication_run_id,),
            ).fetchall()
            records[key] = [row["record"] for row in rows]
            child_counts[key] = len(rows)
        disposition_values = [
            cast(str, value["disposition"])
            for value in connection.execute(
                """
                SELECT disposition
                FROM private_knowledge_coverage_execution_dispositions
                WHERE rule_import_run_id = %s
                ORDER BY knowledge_coverage_id
                """,
                (publication_run_id,),
            ).fetchall()
        ]
        dispositions = _disposition_counts(disposition_values)
        counts = PublicationCounts(
            subject_count=int(source_counts["subject_count"]),
            contract_count=int(source_counts["contract_count"]),
            coverage_count=int(source_counts["coverage_count"]),
            disposition_count=child_counts["dispositions"],
            published_disposition_count=dispositions.published,
            blocked_disposition_count=dispositions.blocked,
            not_applicable_disposition_count=dispositions.not_applicable,
            status_interval_count=child_counts["status_intervals"],
            fact_normalizer_count=child_counts["fact_normalizers"],
            rule_publication_count=child_counts["rule_publications"],
            rule_citation_count=child_counts["rule_citations"],
            calculation_publication_count=child_counts["calculation_publications"],
            calculation_citation_count=child_counts["calculation_citations"],
        )
        records["source_counts"] = [
            {
                "knowledge_import_run_id": str(knowledge_run_id),
                **counts.model_dump(mode="json"),
            }
        ]
        return counts, dispositions, records

    @staticmethod
    def _citation_violation_count(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        knowledge_run_id: UUID,
        publication_run_id: UUID,
    ) -> int:
        row = connection.execute(
            """
            WITH citations AS (
              SELECT terms_section_id, source_clause_id, fact_id,
                     page_start, page_end, source_text_sha256
              FROM private_knowledge_rule_citations
              WHERE rule_import_run_id = %(publication_run)s
              UNION ALL
              SELECT terms_section_id, source_clause_id, fact_id,
                     page_start, page_end, source_text_sha256
              FROM private_knowledge_calculation_citations
              WHERE rule_import_run_id = %(publication_run)s
            )
            SELECT count(*) AS violations
            FROM citations AS citation
            LEFT JOIN private_knowledge_terms_sections AS section
              ON section.id = citation.terms_section_id
             AND section.import_run_id = %(knowledge_run)s
            LEFT JOIN private_knowledge_source_clauses AS clause
              ON clause.id = citation.source_clause_id
             AND clause.import_run_id = %(knowledge_run)s
            LEFT JOIN private_knowledge_facts AS fact
              ON fact.id = citation.fact_id
             AND fact.import_run_id = %(knowledge_run)s
            WHERE section.id IS NULL
               OR (citation.source_clause_id IS NULL AND citation.fact_id IS NOT NULL)
               OR (citation.source_clause_id IS NOT NULL AND (
                    clause.id IS NULL
                    OR clause.terms_section_id <> citation.terms_section_id
                    OR clause.page_start <> citation.page_start
                    OR clause.page_end <> citation.page_end
                    OR clause.source_text_sha256 <> citation.source_text_sha256
                  ))
               OR (citation.source_clause_id IS NULL AND citation.fact_id IS NULL AND (
                    section.page_start <> citation.page_start
                    OR section.page_end <> citation.page_end
                    OR section.source_record_digest_sha256
                         <> citation.source_text_sha256
                  ))
               OR (citation.fact_id IS NOT NULL AND (
                    fact.id IS NULL
                    OR fact.terms_section_id <> citation.terms_section_id
                    OR NOT EXISTS (
                      SELECT 1
                      FROM private_knowledge_fact_citations AS source
                      WHERE source.import_run_id = %(knowledge_run)s
                        AND source.fact_id = citation.fact_id
                        AND source.source_clause_id = citation.source_clause_id
                        AND source.page_start = citation.page_start
                        AND source.page_end = citation.page_end
                        AND source.source_text_sha256 = citation.source_text_sha256
                    )
                  ))
            """,
            {
                "knowledge_run": knowledge_run_id,
                "publication_run": publication_run_id,
            },
        ).fetchone()
        if row is None:
            raise RulePublicationRepositoryError(
                RulePublicationRepositoryErrorCode.VERIFICATION_FAILED
            )
        return int(row["violations"])

    def _recover_applied_publication(
        self,
        household_space_id: UUID,
        package_digest_sha256: str,
    ) -> AppliedRulePublication | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT id
                    FROM private_knowledge_rule_import_runs
                    WHERE household_space_id = %s
                      AND package_digest_sha256 = %s
                      AND state = 'APPLIED' AND is_current
                    """,
                    (household_space_id, package_digest_sha256),
                ).fetchone()
                if row is None:
                    return None
                summary = self._verify_run(
                    connection,
                    household_space_id=household_space_id,
                    run_id=cast(UUID, row["id"]),
                )
                return AppliedRulePublication.model_validate(summary.model_dump())
        except psycopg.Error, RulePublicationRepositoryError:
            return None

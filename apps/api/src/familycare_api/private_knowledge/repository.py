"""PostgreSQL reconciliation baseline for private knowledge imports."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from familycare_api.private_knowledge.confirmations import (
    AppliedConfirmationSet,
    ConfirmationDryRunReport,
    ConfirmationError,
    ConfirmationErrorCode,
    LoadedConfirmationManifest,
    canonical_confirmation_report_digest,
)
from familycare_api.private_knowledge.errors import PrivateKnowledgePackageError
from familycare_api.private_knowledge.package import (
    PrivateKnowledgePackage,
    validate_loaded_private_knowledge_package,
)
from familycare_api.private_knowledge.reconciliation import (
    BaselineCounts,
    KnowledgeDatabaseBaseline,
    KnowledgeDecisionCounts,
    KnowledgeDryRunReport,
    KnowledgeEntityCounts,
    LabelKeyCount,
    build_dry_run_report,
    canonical_report_digest,
    operational_label_key,
    report_decision_counts,
)
from familycare_api.private_knowledge.snapshot import insert_private_knowledge_snapshot


class PrivateKnowledgeRepositoryErrorCode(StrEnum):
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    HOUSEHOLD_NOT_FOUND = "HOUSEHOLD_NOT_FOUND"
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


class PrivateKnowledgeRepositoryError(RuntimeError):
    """Sanitized repository error without SQL, DSNs, or private values."""

    def __init__(self, code: PrivateKnowledgeRepositoryErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


class KnowledgeSnapshotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: UUID
    package_digest_sha256: Sha256
    state: Literal["APPLIED"]
    is_current: Literal[True]
    counts: KnowledgeEntityCounts
    executable_fact_count: NonNegativeInt
    executable_mapping_count: NonNegativeInt
    unsafe_operational_binding_count: NonNegativeInt


class AppliedKnowledgeSnapshot(KnowledgeSnapshotSummary):
    """Successful create, supersede, or idempotent current snapshot result."""


_KNOWLEDGE_TABLES = (
    ("subjects", "private_knowledge_subjects"),
    ("contracts", "private_knowledge_contracts"),
    ("coverages", "private_knowledge_coverages"),
    ("terms_assignments", "private_knowledge_terms_assignments"),
    ("terms_assignment_sources", "private_knowledge_terms_assignment_sources"),
    ("terms_sections", "private_knowledge_terms_sections"),
    ("source_clauses", "private_knowledge_source_clauses"),
    ("semantic_reviews", "private_knowledge_semantic_reviews"),
    ("facts", "private_knowledge_facts"),
    ("fact_citations", "private_knowledge_fact_citations"),
    ("coverage_terms_mappings", "private_knowledge_coverage_terms_mappings"),
    ("document_bindings", "private_knowledge_document_bindings"),
)


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PrivateKnowledgeRepositoryError(
            PrivateKnowledgeRepositoryErrorCode.DATABASE_UNAVAILABLE
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


def _baseline_digest(payload: object) -> str:
    return hashlib.sha256(
        b"familycare-private-knowledge-baseline-v1\x00" + _canonical_json(payload)
    ).hexdigest()


def _record_digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _projection_digest(payload: object) -> str:
    return hashlib.sha256(
        b"familycare-private-knowledge-projection-v1\x00" + _canonical_json(payload)
    ).hexdigest()


def _advisory_lock_key(household_space_id: UUID) -> int:
    payload = hashlib.sha256(
        b"familycare-private-knowledge-lock\x00" + household_space_id.bytes
    ).digest()
    return int.from_bytes(payload[:8], byteorder="big", signed=True)


def _label_counts(values: list[str]) -> tuple[LabelKeyCount, ...]:
    counts = Counter(values)
    return tuple(LabelKeyCount(key=key, count=count) for key, count in sorted(counts.items()))


def _confirmation_baseline_digest(payload: object) -> str:
    return hashlib.sha256(
        b"familycare-private-confirmation-baseline-v1\x00" + _canonical_json(payload)
    ).hexdigest()


def _confirmation_record_digest(payload: object) -> str:
    return hashlib.sha256(
        b"familycare-private-confirmation-record-v1\x00" + _canonical_json(payload)
    ).hexdigest()


class PostgresPrivateKnowledgeRepository:
    """Read a private, repeatable, count-only operational reconciliation view."""

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

    def apply_snapshot(
        self,
        package: PrivateKnowledgePackage,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report: KnowledgeDryRunReport,
    ) -> AppliedKnowledgeSnapshot:
        """Apply one approved snapshot under a household advisory transaction lock."""

        try:
            validate_loaded_private_knowledge_package(package)
        except PrivateKnowledgePackageError:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.APPROVAL_INVALID
            ) from None
        if (
            approved_report.report_digest_sha256 != canonical_report_digest(approved_report)
            or approved_report.package_digest_sha256 != package.package_digest_sha256
        ):
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.APPROVAL_INVALID
            )
        if (
            approved_report.operation == "BLOCKED"
            or approved_report.snapshot_conflict_count
            or approved_report.apply_block_count
        ):
            raise PrivateKnowledgeRepositoryError(PrivateKnowledgeRepositoryErrorCode.APPLY_BLOCKED)

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                with connection.transaction():
                    self._require_apply_transaction_mode(connection)
                    connection.execute(
                        """
                        LOCK TABLE household_spaces, app_users, family_members,
                                   policy_contracts, riders, terms_editions,
                                   documents, document_versions, evidence
                        IN SHARE MODE
                        """
                    )
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (_advisory_lock_key(household_space_id),),
                    )
                    actor = connection.execute(
                        """
                        SELECT 1
                        FROM app_users
                        WHERE id = %s AND household_space_id = %s AND is_active
                        """,
                        (actor_id, household_space_id),
                    ).fetchone()
                    if actor is None:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.ACTOR_NOT_FOUND
                        )

                    baseline = self._read_baseline(connection, household_space_id)
                    current_report = build_dry_run_report(package, baseline)
                    if current_report.report_digest_sha256 != approved_report.report_digest_sha256:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.STALE_DRY_RUN
                        )
                    if current_report.operation == "BLOCKED":
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.APPLY_BLOCKED
                        )
                    if current_report.operation == "NO_OP":
                        if baseline.current_run_id is None:
                            raise PrivateKnowledgeRepositoryError(
                                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
                            )
                        summary = self._verify_run(
                            connection,
                            household_space_id=household_space_id,
                            run_id=baseline.current_run_id,
                        )
                        return AppliedKnowledgeSnapshot.model_validate(summary.model_dump())
                    if current_report.operation not in {"CREATE", "SUPERSEDE"}:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.APPROVAL_INVALID
                        )

                    run_id = uuid4()
                    connection.execute(
                        """
                        INSERT INTO private_knowledge_import_runs (
                          id, household_space_id, package_schema_version,
                          package_digest_sha256, manifest_digest_sha256,
                          importer_version, analysis_authority, state, is_current,
                          manifest_counts_json, manifest_json,
                          reconciliation_counts_json, entity_counts_json,
                          decision_counts_json,
                          baseline_digest_sha256, report_digest_sha256,
                          applied_by, applied_at
                        ) VALUES (
                          %s, %s, %s, %s, %s, 'familycare-private-knowledge-v1',
                          %s, 'APPLIED', false, %s, %s, %s, %s,
                          %s, %s, %s, %s, clock_timestamp()
                        )
                        """,
                        (
                            run_id,
                            household_space_id,
                            package.schema_version,
                            package.package_digest_sha256,
                            package.manifest_digest_sha256,
                            package.manifest.review_authority,
                            Jsonb(package.manifest.counts.model_dump(mode="json")),
                            Jsonb(package.manifest.model_dump(mode="json")),
                            Jsonb(package.reconciliation.model_dump(mode="json")),
                            Jsonb(approved_report.expected_insert_counts.model_dump(mode="json")),
                            Jsonb(report_decision_counts(approved_report).model_dump(mode="json")),
                            baseline.baseline_digest_sha256,
                            approved_report.report_digest_sha256,
                            actor_id,
                        ),
                    )
                    self._after_group("import_run")
                    inserted_counts = insert_private_knowledge_snapshot(
                        connection,
                        run_id=run_id,
                        household_space_id=household_space_id,
                        package=package,
                        after_group=self._after_group,
                    )
                    if inserted_counts != approved_report.expected_insert_counts:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.COUNT_MISMATCH
                        )
                    persisted_counts, persisted_records = self._current_knowledge_snapshot(
                        connection,
                        run_id,
                    )
                    if persisted_counts != inserted_counts:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.COUNT_MISMATCH
                        )
                    projection_digest = _projection_digest(persisted_records)
                    connection.execute(
                        """
                        UPDATE private_knowledge_import_runs
                        SET projection_digest_sha256 = %s
                        WHERE id = %s AND household_space_id = %s
                        """,
                        (projection_digest, run_id, household_space_id),
                    )
                    self._after_group("before_current_switch")
                    connection.execute(
                        """
                        UPDATE private_knowledge_import_runs
                        SET state = 'SUPERSEDED', is_current = false,
                            superseded_at = clock_timestamp()
                        WHERE household_space_id = %s AND is_current
                        """,
                        (household_space_id,),
                    )
                    selected = connection.execute(
                        """
                        UPDATE private_knowledge_import_runs
                        SET is_current = true
                        WHERE id = %s AND household_space_id = %s
                          AND state = 'APPLIED' AND NOT is_current
                        RETURNING id
                        """,
                        (run_id, household_space_id),
                    ).fetchone()
                    if selected is None:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.APPLY_FAILED
                        )
                    summary = self._verify_run(
                        connection,
                        household_space_id=household_space_id,
                        run_id=run_id,
                    )
            return AppliedKnowledgeSnapshot.model_validate(summary.model_dump())
        except PrivateKnowledgeRepositoryError:
            raise
        except psycopg.Error:
            recovered = self._recover_applied_snapshot(
                household_space_id,
                package.package_digest_sha256,
            )
            if recovered is not None:
                return recovered
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.APPLY_FAILED
            ) from None
        except Exception:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.APPLY_FAILED
            ) from None

    def verify_current(self, household_space_id: UUID) -> KnowledgeSnapshotSummary:
        """Verify counts, row digests, execution flags, and unsafe bindings."""

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    self._require_transaction_mode(connection)
                    baseline = self._read_baseline(connection, household_space_id)
                    if baseline.current_run_id is None:
                        raise PrivateKnowledgeRepositoryError(
                            PrivateKnowledgeRepositoryErrorCode.CURRENT_NOT_FOUND
                        )
                    summary = self._verify_run(
                        connection,
                        household_space_id=household_space_id,
                        run_id=baseline.current_run_id,
                    )
                    self._require_unassigned_transaction_id(connection)
                    return summary
        except PrivateKnowledgeRepositoryError:
            raise
        except psycopg.Error:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.DATABASE_UNAVAILABLE
            ) from None
        except KeyError, TypeError, ValueError, ValidationError:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            ) from None

    def _recover_applied_snapshot(
        self,
        household_space_id: UUID,
        package_digest_sha256: str,
    ) -> AppliedKnowledgeSnapshot | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT id
                    FROM private_knowledge_import_runs
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
                return AppliedKnowledgeSnapshot.model_validate(summary.model_dump())
        except psycopg.Error, PrivateKnowledgeRepositoryError:
            return None

    def _verify_run(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        *,
        household_space_id: UUID,
        run_id: UUID,
    ) -> KnowledgeSnapshotSummary:
        run = connection.execute(
            """
            SELECT id, package_digest_sha256, projection_digest_sha256, state, is_current,
                   entity_counts_json, decision_counts_json
            FROM private_knowledge_import_runs
            WHERE id = %s AND household_space_id = %s
            """,
            (run_id, household_space_id),
        ).fetchone()
        if run is None or run["state"] != "APPLIED" or run["is_current"] is not True:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        expected_counts = KnowledgeEntityCounts.model_validate(run["entity_counts_json"])
        actual_counts, actual_records = self._current_knowledge_snapshot(connection, run_id)
        if actual_counts != expected_counts:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.COUNT_MISMATCH
            )
        projection_digest = run["projection_digest_sha256"]
        if (
            not isinstance(projection_digest, str)
            or _projection_digest(actual_records) != projection_digest
        ):
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        expected_decisions = KnowledgeDecisionCounts.model_validate(run["decision_counts_json"])
        if self._persisted_decision_counts(connection, run_id) != expected_decisions:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        if self._referential_closure_violation_count(connection, run_id):
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )

        for _, table_name in _KNOWLEDGE_TABLES:
            records = connection.execute(
                f"""
                SELECT source_record_json, source_record_digest_sha256
                FROM {table_name}
                WHERE import_run_id = %s
                """,
                (run_id,),
            ).fetchall()
            if any(
                _record_digest(row["source_record_json"]) != row["source_record_digest_sha256"]
                for row in records
            ):
                raise PrivateKnowledgeRepositoryError(
                    PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
                )

        safety = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM private_knowledge_facts
               WHERE import_run_id = %(run)s AND executable) AS executable_facts,
              (SELECT count(*) FROM private_knowledge_coverage_terms_mappings
               WHERE import_run_id = %(run)s AND executable) AS executable_mappings,
              (
                (SELECT count(*) FROM private_knowledge_subjects
                 WHERE import_run_id = %(run)s
                   AND (
                     (binding_decision = 'MATCH'
                      AND (family_member_id IS NULL
                           OR binding_confirmed_by IS NULL
                           OR binding_confirmed_at IS NULL))
                     OR (binding_decision <> 'MATCH' AND family_member_id IS NOT NULL)
                   )) +
                (SELECT count(*) FROM private_knowledge_contracts
                 WHERE import_run_id = %(run)s
                   AND (policy_contract_id IS NOT NULL
                        OR operational_binding_decision <> 'UNKNOWN')) +
                (SELECT count(*) FROM private_knowledge_coverages
                 WHERE import_run_id = %(run)s
                   AND (rider_id IS NOT NULL
                        OR operational_binding_decision <> 'UNKNOWN')) +
                (SELECT count(*) FROM private_knowledge_terms_assignments
                 WHERE import_run_id = %(run)s
                   AND (terms_edition_id IS NOT NULL
                        OR operational_binding_decision <> 'UNKNOWN')) +
                (SELECT count(*) FROM private_knowledge_document_bindings
                 WHERE import_run_id = %(run)s
                   AND (document_version_id IS NOT NULL OR evidence_id IS NOT NULL
                        OR binding_decision <> 'UNKNOWN'))
              ) AS unsafe_bindings
            """,
            {"run": run_id},
        ).fetchone()
        if safety is None:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        executable_facts = cast(int, safety["executable_facts"])
        executable_mappings = cast(int, safety["executable_mappings"])
        unsafe_bindings = cast(int, safety["unsafe_bindings"])
        if executable_facts or executable_mappings or unsafe_bindings:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        return KnowledgeSnapshotSummary(
            run_id=run_id,
            package_digest_sha256=cast(str, run["package_digest_sha256"]),
            state="APPLIED",
            is_current=True,
            counts=actual_counts,
            executable_fact_count=executable_facts,
            executable_mapping_count=executable_mappings,
            unsafe_operational_binding_count=unsafe_bindings,
        )

    @staticmethod
    def _persisted_decision_counts(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
    ) -> KnowledgeDecisionCounts:
        row = connection.execute(
            """
            SELECT
              (SELECT jsonb_build_object(
                 'match', count(*) FILTER (WHERE enrollment_decision = 'MATCH'),
                 'no_match', count(*) FILTER (WHERE enrollment_decision = 'NO_MATCH'),
                 'unknown', count(*) FILTER (WHERE enrollment_decision = 'UNKNOWN')
               ) FROM private_knowledge_coverage_terms_mappings
               WHERE import_run_id = %(run)s) AS enrollment_decisions,
              (SELECT jsonb_build_object(
                 'fixed', count(*) FILTER (WHERE benefit_type = 'FIXED'),
                 'indemnity', count(*) FILTER (WHERE benefit_type = 'INDEMNITY'),
                 'unknown', count(*) FILTER (WHERE benefit_type = 'UNKNOWN'),
                 'not_applicable', count(*) FILTER (
                   WHERE benefit_type = 'NOT_APPLICABLE'
                 )
               ) FROM private_knowledge_coverages
               WHERE import_run_id = %(run)s) AS benefit_types,
              (SELECT jsonb_build_object(
                 'match', count(*) FILTER (
                   WHERE document_identity_decision = 'MATCH'
                 ),
                 'no_match', count(*) FILTER (
                   WHERE document_identity_decision = 'NO_MATCH'
                 ),
                 'unknown', count(*) FILTER (
                   WHERE document_identity_decision = 'UNKNOWN'
                 )
               ) FROM private_knowledge_terms_assignments
               WHERE import_run_id = %(run)s) AS terms_document_identity,
              (SELECT jsonb_build_object(
                 'match', count(*) FILTER (
                   WHERE edition_applicability_decision = 'MATCH'
                 ),
                 'no_match', count(*) FILTER (
                   WHERE edition_applicability_decision = 'NO_MATCH'
                 ),
                 'unknown', count(*) FILTER (
                   WHERE edition_applicability_decision = 'UNKNOWN'
                 )
               ) FROM private_knowledge_terms_assignments
               WHERE import_run_id = %(run)s) AS terms_edition_applicability,
              (SELECT jsonb_build_object(
                 'match', count(*) FILTER (WHERE overall_decision = 'MATCH'),
                 'no_match', count(*) FILTER (WHERE overall_decision = 'NO_MATCH'),
                 'unknown', count(*) FILTER (WHERE overall_decision = 'UNKNOWN')
               ) FROM private_knowledge_terms_assignments
               WHERE import_run_id = %(run)s) AS terms_overall_review,
              (SELECT jsonb_build_object(
                 'match', count(*) FILTER (
                   WHERE source_record_json ->> 'mapping_decision' = 'MATCH'
                 ),
                 'no_match', count(*) FILTER (
                   WHERE source_record_json ->> 'mapping_decision' = 'NO_MATCH'
                 ),
                 'unknown', count(*) FILTER (
                   WHERE source_record_json ->> 'mapping_decision' = 'UNKNOWN'
                 ),
                 'not_applicable', count(*) FILTER (
                   WHERE source_record_json ->> 'mapping_decision' = 'NOT_APPLICABLE'
                 )
               ) FROM private_knowledge_coverage_terms_mappings
               WHERE import_run_id = %(run)s) AS mapping_source_decisions,
              (SELECT jsonb_build_object(
                 'applicable', count(*) FILTER (
                   WHERE mapping_applicability = 'APPLICABLE'
                 ),
                 'not_applicable', count(*) FILTER (
                   WHERE mapping_applicability = 'NOT_APPLICABLE'
                 ),
                 'unknown', count(*) FILTER (
                   WHERE mapping_applicability = 'UNKNOWN'
                 )
               ) FROM private_knowledge_coverage_terms_mappings
               WHERE import_run_id = %(run)s) AS mapping_applicability,
              (SELECT jsonb_build_object(
                 'active', count(*) FILTER (WHERE current_status = 'active'),
                 'inactive', count(*) FILTER (WHERE current_status = 'inactive'),
                 'lapsed', count(*) FILTER (WHERE current_status = 'lapsed'),
                 'terminated', count(*) FILTER (WHERE current_status = 'terminated'),
                 'unknown', count(*) FILTER (WHERE current_status = 'unknown')
               ) FROM private_knowledge_coverages
               WHERE import_run_id = %(run)s) AS current_statuses
            """,
            {"run": run_id},
        ).fetchone()
        if row is None:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        return KnowledgeDecisionCounts.model_validate(row)

    @staticmethod
    def _referential_closure_violation_count(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
    ) -> int:
        row = connection.execute(
            """
            SELECT count(*) AS violations
            FROM (
              SELECT child.id
              FROM private_knowledge_contracts AS child
              LEFT JOIN private_knowledge_subjects AS parent
                ON parent.id = child.subject_id
               AND parent.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s AND parent.id IS NULL
              UNION ALL
              SELECT child.id
              FROM private_knowledge_coverages AS child
              LEFT JOIN private_knowledge_contracts AS parent
                ON parent.id = child.knowledge_contract_id
               AND parent.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s AND parent.id IS NULL
              UNION ALL
              SELECT child.id
              FROM private_knowledge_terms_assignments AS child
              LEFT JOIN private_knowledge_contracts AS parent
                ON parent.id = child.knowledge_contract_id
               AND parent.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s AND parent.id IS NULL
              UNION ALL
              SELECT child.id
              FROM private_knowledge_terms_assignment_sources AS child
              LEFT JOIN private_knowledge_terms_assignments AS parent
                ON parent.id = child.terms_assignment_id
               AND parent.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s AND parent.id IS NULL
              UNION ALL
              SELECT child.id
              FROM private_knowledge_source_clauses AS child
              LEFT JOIN private_knowledge_terms_sections AS parent
                ON parent.id = child.terms_section_id
               AND parent.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s AND parent.id IS NULL
              UNION ALL
              SELECT child.id
              FROM private_knowledge_semantic_reviews AS child
              LEFT JOIN private_knowledge_terms_sections AS parent
                ON parent.id = child.terms_section_id
               AND parent.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s AND parent.id IS NULL
              UNION ALL
              SELECT child.id
              FROM private_knowledge_facts AS child
              LEFT JOIN private_knowledge_terms_sections AS section
                ON section.id = child.terms_section_id
               AND section.import_run_id = child.import_run_id
              LEFT JOIN private_knowledge_semantic_reviews AS review
                ON review.id = child.semantic_review_id
               AND review.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s
                AND (section.id IS NULL OR review.id IS NULL
                     OR review.terms_section_id <> child.terms_section_id)
              UNION ALL
              SELECT child.id
              FROM private_knowledge_fact_citations AS child
              LEFT JOIN private_knowledge_facts AS fact
                ON fact.id = child.fact_id
               AND fact.import_run_id = child.import_run_id
              LEFT JOIN private_knowledge_source_clauses AS clause
                ON clause.id = child.source_clause_id
               AND clause.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s
                AND (fact.id IS NULL OR clause.id IS NULL
                     OR fact.terms_section_id <> clause.terms_section_id)
              UNION ALL
              SELECT child.id
              FROM private_knowledge_coverage_terms_mappings AS child
              LEFT JOIN private_knowledge_coverages AS coverage
                ON coverage.id = child.coverage_id
               AND coverage.import_run_id = child.import_run_id
              LEFT JOIN private_knowledge_terms_sections AS section
                ON section.id = child.terms_section_id
               AND section.import_run_id = child.import_run_id
              WHERE child.import_run_id = %(run)s
                AND (coverage.id IS NULL OR (
                  child.terms_section_id IS NOT NULL AND section.id IS NULL
                ) OR (
                  child.terms_section_id IS NOT NULL
                  AND child.selected_terms_source_alias_digest_sha256
                      IS DISTINCT FROM section.terms_source_alias_digest_sha256
                ))
            ) AS violations
            """,
            {"run": run_id},
        ).fetchone()
        if row is None:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED
            )
        return int(row["violations"])

    @staticmethod
    def _build_confirmation_report(
        connection: psycopg.Connection[dict[str, Any]],
        manifest: LoadedConfirmationManifest,
    ) -> ConfirmationDryRunReport:
        run = connection.execute(
            """
            SELECT id, package_digest_sha256
            FROM private_knowledge_import_runs
            WHERE household_space_id = %s
              AND state = 'APPLIED'
              AND is_current
            """,
            (manifest.household_space_id,),
        ).fetchone()
        if run is None:
            raise ConfirmationError(ConfirmationErrorCode.CURRENT_SNAPSHOT_NOT_FOUND)
        if run["package_digest_sha256"] != manifest.package_digest_sha256:
            raise ConfirmationError(ConfirmationErrorCode.PACKAGE_DIGEST_MISMATCH)
        run_id = cast(UUID, run["id"])

        actor = connection.execute(
            """
            SELECT 1
            FROM app_users
            WHERE id = %s AND household_space_id = %s AND is_active
            """,
            (manifest.confirmed_by, manifest.household_space_id),
        ).fetchone()
        if actor is None:
            raise ConfirmationError(ConfirmationErrorCode.ACTOR_NOT_FOUND)

        member_ids = [row.family_member_id for row in manifest.subjects]
        members = connection.execute(
            """
            SELECT id
            FROM family_members
            WHERE household_space_id = %s
              AND deleted_at IS NULL
              AND id = ANY(%s::uuid[])
            """,
            (manifest.household_space_id, member_ids),
        ).fetchall()
        if {row["id"] for row in members} != set(member_ids):
            raise ConfirmationError(ConfirmationErrorCode.FAMILY_MEMBER_NOT_FOUND)

        subjects = connection.execute(
            """
            SELECT source_subject_key, family_member_id, binding_decision,
                   binding_confirmed_by, binding_confirmed_at
            FROM private_knowledge_subjects
            WHERE import_run_id = %s
            ORDER BY source_subject_key
            """,
            (run_id,),
        ).fetchall()
        desired_subjects = {
            row.source_subject_key: row.family_member_id for row in manifest.subjects
        }
        if {row["source_subject_key"] for row in subjects} != set(desired_subjects):
            raise ConfirmationError(ConfirmationErrorCode.SUBJECT_SET_MISMATCH)

        contracts = connection.execute(
            """
            SELECT contract.id,
                   contract.source_record_json ->> 'canonical_policy_id'
                     AS canonical_policy_id,
                   confirmation.decision, confirmation.confirmed_status,
                   confirmation.status_as_of, confirmation.authority,
                   confirmation.reason_code, confirmation.confirmed_by,
                   confirmation.confirmation_digest_sha256
            FROM private_knowledge_contracts AS contract
            LEFT JOIN private_knowledge_contract_confirmations AS confirmation
              ON confirmation.knowledge_contract_id = contract.id
             AND confirmation.import_run_id = contract.import_run_id
             AND confirmation.is_current
            WHERE contract.import_run_id = %s
            ORDER BY canonical_policy_id
            """,
            (run_id,),
        ).fetchall()
        desired_contracts = {row.canonical_policy_id: row for row in manifest.contracts}
        if {row["canonical_policy_id"] for row in contracts} != set(desired_contracts):
            raise ConfirmationError(ConfirmationErrorCode.CONTRACT_SET_MISMATCH)

        binding_change_count = sum(
            1
            for row in subjects
            if not (
                row["family_member_id"] == desired_subjects[row["source_subject_key"]]
                and row["binding_decision"] == "MATCH"
                and row["binding_confirmed_by"] == manifest.confirmed_by
                and row["binding_confirmed_at"] is not None
            )
        )
        confirmation_insert_count = 0
        confirmation_supersede_count = 0
        for row in contracts:
            desired = desired_contracts[row["canonical_policy_id"]]
            current_matches = (
                row["decision"] == desired.decision
                and row["confirmed_status"] == desired.confirmed_status
                and row["status_as_of"] == manifest.status_as_of
                and row["authority"] == manifest.authority
                and row["reason_code"] == desired.reason_code
                and row["confirmed_by"] == manifest.confirmed_by
            )
            if not current_matches:
                confirmation_insert_count += 1
                if row["decision"] is not None:
                    confirmation_supersede_count += 1

        baseline_payload = {
            "run_id": str(run_id),
            "package_digest_sha256": manifest.package_digest_sha256,
            "actor_id": str(manifest.confirmed_by),
            "subjects": [
                {
                    "source_subject_key": row["source_subject_key"],
                    "family_member_id": (
                        str(row["family_member_id"])
                        if row["family_member_id"] is not None
                        else None
                    ),
                    "binding_decision": row["binding_decision"],
                    "binding_confirmed_by": (
                        str(row["binding_confirmed_by"])
                        if row["binding_confirmed_by"] is not None
                        else None
                    ),
                    "binding_confirmed_at": (
                        row["binding_confirmed_at"].isoformat()
                        if row["binding_confirmed_at"] is not None
                        else None
                    ),
                }
                for row in subjects
            ],
            "contracts": [
                {
                    "canonical_policy_id": row["canonical_policy_id"],
                    "decision": row["decision"],
                    "confirmed_status": row["confirmed_status"],
                    "status_as_of": (
                        row["status_as_of"].isoformat() if row["status_as_of"] is not None else None
                    ),
                    "authority": row["authority"],
                    "reason_code": row["reason_code"],
                    "confirmed_by": (
                        str(row["confirmed_by"]) if row["confirmed_by"] is not None else None
                    ),
                    "confirmation_digest_sha256": row["confirmation_digest_sha256"],
                }
                for row in contracts
            ],
        }
        operation: Literal["APPLY", "NO_OP"] = (
            "NO_OP" if binding_change_count == 0 and confirmation_insert_count == 0 else "APPLY"
        )
        provisional = ConfirmationDryRunReport(
            schema_version="private-knowledge-confirmation-dry-run.v1",
            manifest_digest_sha256=manifest.manifest_digest_sha256,
            package_digest_sha256=manifest.package_digest_sha256,
            household_space_id=manifest.household_space_id,
            current_run_id=run_id,
            baseline_digest_sha256=_confirmation_baseline_digest(baseline_payload),
            operation=operation,
            subject_count=len(subjects),
            contract_count=len(contracts),
            binding_change_count=binding_change_count,
            confirmation_insert_count=confirmation_insert_count,
            confirmation_supersede_count=confirmation_supersede_count,
            report_digest_sha256="0" * 64,
        )
        return provisional.model_copy(
            update={"report_digest_sha256": canonical_confirmation_report_digest(provisional)}
        )

    def prepare_confirmation_dry_run(
        self,
        manifest: LoadedConfirmationManifest,
    ) -> ConfirmationDryRunReport:
        """Validate exact member and contract sets without database mutation."""

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    self._require_transaction_mode(connection)
                    report = self._build_confirmation_report(connection, manifest)
                    self._require_unassigned_transaction_id(connection)
                    return report
        except ConfirmationError:
            raise
        except psycopg.Error:
            raise ConfirmationError(ConfirmationErrorCode.DATABASE_UNAVAILABLE) from None
        except KeyError, TypeError, ValueError:
            raise ConfirmationError(ConfirmationErrorCode.VERIFICATION_FAILED) from None

    def apply_confirmations(
        self,
        manifest: LoadedConfirmationManifest,
        *,
        approved_report: ConfirmationDryRunReport,
    ) -> AppliedConfirmationSet:
        """Atomically bind every subject and append changed confirmations."""

        if (
            approved_report.report_digest_sha256
            != canonical_confirmation_report_digest(approved_report)
            or approved_report.manifest_digest_sha256 != manifest.manifest_digest_sha256
            or approved_report.package_digest_sha256 != manifest.package_digest_sha256
            or approved_report.household_space_id != manifest.household_space_id
        ):
            raise ConfirmationError(ConfirmationErrorCode.APPROVAL_INVALID)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                with connection.transaction():
                    self._require_apply_transaction_mode(connection)
                    connection.execute(
                        """
                        LOCK TABLE private_knowledge_import_runs,
                                   private_knowledge_subjects,
                                   private_knowledge_contracts,
                                   private_knowledge_contract_confirmations,
                                   family_members, app_users
                        IN SHARE ROW EXCLUSIVE MODE
                        """
                    )
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (_advisory_lock_key(manifest.household_space_id),),
                    )
                    current_report = self._build_confirmation_report(connection, manifest)
                    if current_report != approved_report:
                        raise ConfirmationError(ConfirmationErrorCode.STALE_DRY_RUN)

                    for subject in manifest.subjects:
                        connection.execute(
                            """
                            UPDATE private_knowledge_subjects
                               SET family_member_id = %s,
                                   binding_decision = 'MATCH',
                                   binding_conflict = false,
                                   binding_reason_code = 'USER_EXACT_BINDING',
                                   binding_confirmed_by = %s,
                                   binding_confirmed_at = clock_timestamp()
                             WHERE import_run_id = %s
                               AND source_subject_key = %s
                               AND NOT (
                                 family_member_id = %s
                                 AND binding_decision = 'MATCH'
                                 AND binding_confirmed_by = %s
                                 AND binding_confirmed_at IS NOT NULL
                               )
                            """,
                            (
                                subject.family_member_id,
                                manifest.confirmed_by,
                                approved_report.current_run_id,
                                subject.source_subject_key,
                                subject.family_member_id,
                                manifest.confirmed_by,
                            ),
                        )

                    contract_rows = connection.execute(
                        """
                        SELECT contract.id,
                               contract.source_record_json ->> 'canonical_policy_id'
                                 AS canonical_policy_id,
                               confirmation.id AS confirmation_id,
                               confirmation.decision,
                               confirmation.confirmed_status,
                               confirmation.status_as_of,
                               confirmation.authority,
                               confirmation.reason_code,
                               confirmation.confirmed_by,
                               confirmation.confirmation_digest_sha256
                        FROM private_knowledge_contracts AS contract
                        LEFT JOIN private_knowledge_contract_confirmations AS confirmation
                          ON confirmation.knowledge_contract_id = contract.id
                         AND confirmation.import_run_id = contract.import_run_id
                         AND confirmation.is_current
                        WHERE contract.import_run_id = %s
                        """,
                        (approved_report.current_run_id,),
                    ).fetchall()
                    contracts_by_key = {row["canonical_policy_id"]: row for row in contract_rows}
                    for desired in manifest.contracts:
                        current = contracts_by_key[desired.canonical_policy_id]
                        current_matches = (
                            current["decision"] == desired.decision
                            and current["confirmed_status"] == desired.confirmed_status
                            and current["status_as_of"] == manifest.status_as_of
                            and current["authority"] == manifest.authority
                            and current["reason_code"] == desired.reason_code
                            and current["confirmed_by"] == manifest.confirmed_by
                        )
                        if current_matches:
                            continue
                        previous_digest = current["confirmation_digest_sha256"]
                        if current["confirmation_id"] is not None:
                            connection.execute(
                                """
                                UPDATE private_knowledge_contract_confirmations
                                   SET is_current = false,
                                       superseded_at = clock_timestamp()
                                 WHERE id = %s AND is_current
                                """,
                                (current["confirmation_id"],),
                            )
                        confirmation_digest = _confirmation_record_digest(
                            {
                                "run_id": str(approved_report.current_run_id),
                                "canonical_policy_id": desired.canonical_policy_id,
                                "decision": desired.decision,
                                "confirmed_status": desired.confirmed_status,
                                "status_as_of": manifest.status_as_of.isoformat(),
                                "authority": manifest.authority,
                                "reason_code": desired.reason_code,
                                "confirmed_by": str(manifest.confirmed_by),
                                "previous_confirmation_digest_sha256": previous_digest,
                            }
                        )
                        connection.execute(
                            """
                            INSERT INTO private_knowledge_contract_confirmations (
                              import_run_id, household_space_id,
                              knowledge_contract_id, decision, confirmed_status,
                              status_as_of, authority, reason_code, confirmed_by,
                              confirmation_digest_sha256
                            ) VALUES (
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                approved_report.current_run_id,
                                manifest.household_space_id,
                                current["id"],
                                desired.decision,
                                desired.confirmed_status,
                                manifest.status_as_of,
                                manifest.authority,
                                desired.reason_code,
                                manifest.confirmed_by,
                                confirmation_digest,
                            ),
                        )

                    verified = self._build_confirmation_report(connection, manifest)
                    if verified.operation != "NO_OP":
                        raise ConfirmationError(ConfirmationErrorCode.VERIFICATION_FAILED)
                    return AppliedConfirmationSet(
                        run_id=approved_report.current_run_id,
                        package_digest_sha256=manifest.package_digest_sha256,
                        subject_count=verified.subject_count,
                        contract_count=verified.contract_count,
                        current_confirmation_count=verified.contract_count,
                    )
        except ConfirmationError:
            raise
        except psycopg.Error:
            raise ConfirmationError(ConfirmationErrorCode.APPLY_FAILED) from None
        except KeyError, TypeError, ValueError:
            raise ConfirmationError(ConfirmationErrorCode.VERIFICATION_FAILED) from None

    def read_baseline(self, household_space_id: UUID) -> KnowledgeDatabaseBaseline:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    self._require_transaction_mode(connection)
                    baseline = self._read_baseline(connection, household_space_id)
                    self._require_unassigned_transaction_id(connection)
                    return baseline
        except PrivateKnowledgeRepositoryError:
            raise
        except psycopg.Error:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.DATABASE_UNAVAILABLE
            ) from None
        except KeyError, TypeError, ValueError:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.BASELINE_INVALID
            ) from None

    @staticmethod
    def _require_apply_transaction_mode(
        connection: psycopg.Connection[dict[str, Any]],
    ) -> None:
        row = connection.execute(
            """
            SELECT
              current_setting('transaction_isolation') AS isolation,
              current_setting('transaction_read_only') AS read_only
            """
        ).fetchone()
        if row != {"isolation": "repeatable read", "read_only": "off"}:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.TRANSACTION_MODE_INVALID
            )

    @staticmethod
    def _require_transaction_mode(
        connection: psycopg.Connection[dict[str, Any]],
    ) -> None:
        row = connection.execute(
            """
            SELECT
              current_setting('transaction_isolation') AS isolation,
              current_setting('transaction_read_only') AS read_only
            """
        ).fetchone()
        if row != {"isolation": "repeatable read", "read_only": "on"}:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.TRANSACTION_MODE_INVALID
            )

    @staticmethod
    def _require_unassigned_transaction_id(
        connection: psycopg.Connection[dict[str, Any]],
    ) -> None:
        row = connection.execute("SELECT txid_current_if_assigned() AS transaction_id").fetchone()
        if row is None or row["transaction_id"] is not None:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.TRANSACTION_MODE_INVALID
            )

    def _read_baseline(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        household_space_id: UUID,
    ) -> KnowledgeDatabaseBaseline:
        household = connection.execute(
            """
            SELECT to_jsonb(space) AS record
            FROM household_spaces AS space
            WHERE space.id = %s AND space.deleted_at IS NULL
            """,
            (household_space_id,),
        ).fetchone()
        if household is None:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.HOUSEHOLD_NOT_FOUND
            )

        family_rows = self._records(
            connection,
            """
            SELECT to_jsonb(member) AS record
            FROM family_members AS member
            WHERE member.household_space_id = %s AND member.deleted_at IS NULL
            ORDER BY member.id
            """,
            household_space_id,
        )
        policy_rows = self._records(
            connection,
            """
            SELECT to_jsonb(policy) AS record
            FROM policy_contracts AS policy
            WHERE policy.household_space_id = %s AND policy.deleted_at IS NULL
            ORDER BY policy.id
            """,
            household_space_id,
        )
        rider_rows = self._records(
            connection,
            """
            SELECT to_jsonb(rider) AS record
            FROM riders AS rider
            JOIN policy_contracts AS policy
              ON policy.id = rider.policy_contract_id
             AND policy.household_space_id = rider.household_space_id
            WHERE rider.household_space_id = %s
              AND rider.deleted_at IS NULL
              AND policy.deleted_at IS NULL
            ORDER BY rider.id
            """,
            household_space_id,
        )
        evidence_rows = self._records(
            connection,
            """
            SELECT to_jsonb(item) AS record
            FROM evidence AS item
            WHERE item.household_space_id = %s
            ORDER BY item.id
            """,
            household_space_id,
        )
        document_rows = self._records(
            connection,
            """
            WITH referenced AS (
              SELECT policy.source_document_version_id AS id
              FROM policy_contracts AS policy
              WHERE policy.household_space_id = %s AND policy.deleted_at IS NULL
              UNION
              SELECT item.document_version_id AS id
              FROM evidence AS item
              WHERE item.household_space_id = %s
            )
            SELECT jsonb_build_object(
                     'version', to_jsonb(version),
                     'document', to_jsonb(document)
                   ) AS record
            FROM referenced
            JOIN document_versions AS version ON version.id = referenced.id
            JOIN documents AS document ON document.id = version.document_id
            ORDER BY version.id
            """,
            household_space_id,
            household_space_id,
        )
        run_rows = self._records(
            connection,
            """
            SELECT to_jsonb(run) - 'manifest_json' AS record
            FROM private_knowledge_import_runs AS run
            WHERE run.household_space_id = %s
            ORDER BY run.created_at, run.id
            """,
            household_space_id,
        )
        runs = connection.execute(
            """
            SELECT id, package_digest_sha256, is_current
            FROM private_knowledge_import_runs
            WHERE household_space_id = %s
            ORDER BY created_at, id
            """,
            (household_space_id,),
        ).fetchall()
        current_rows = [row for row in runs if cast(bool, row["is_current"])]
        if len(current_rows) > 1:
            raise PrivateKnowledgeRepositoryError(
                PrivateKnowledgeRepositoryErrorCode.BASELINE_INVALID
            )
        current = current_rows[0] if current_rows else None
        current_run_id = cast(UUID, current["id"]) if current else None
        current_package_digest = cast(str, current["package_digest_sha256"]) if current else None
        current_counts, knowledge_rows = self._current_knowledge_snapshot(
            connection,
            current_run_id,
        )
        policy_labels = connection.execute(
            """
            SELECT insurer_display, product_display
            FROM policy_contracts
            WHERE household_space_id = %s AND deleted_at IS NULL
            ORDER BY id
            """,
            (household_space_id,),
        ).fetchall()
        coverage_labels = connection.execute(
            """
            SELECT policy.insurer_display, policy.product_display, rider.display_name
            FROM riders AS rider
            JOIN policy_contracts AS policy
              ON policy.id = rider.policy_contract_id
             AND policy.household_space_id = rider.household_space_id
            WHERE rider.household_space_id = %s
              AND rider.deleted_at IS NULL
              AND policy.deleted_at IS NULL
            ORDER BY rider.id
            """,
            (household_space_id,),
        ).fetchall()

        fingerprint = {
            "household": household["record"],
            "family_members": family_rows,
            "policy_contracts": policy_rows,
            "riders": rider_rows,
            "evidence": evidence_rows,
            "document_versions": document_rows,
            "import_runs": run_rows,
            "current_knowledge": knowledge_rows,
        }
        return KnowledgeDatabaseBaseline(
            household_space_id=household_space_id,
            baseline_digest_sha256=_baseline_digest(fingerprint),
            current_run_id=current_run_id,
            current_package_digest_sha256=current_package_digest,
            known_package_digests=tuple(
                sorted(cast(str, row["package_digest_sha256"]) for row in runs)
            ),
            counts=BaselineCounts(
                family_members=len(family_rows),
                policy_contracts=len(policy_rows),
                riders=len(rider_rows),
                document_versions=len(document_rows),
                evidence=len(evidence_rows),
                import_runs=len(runs),
                current_import_runs=len(current_rows),
            ),
            current_snapshot_counts=current_counts,
            policy_label_key_counts=_label_counts(
                [
                    operational_label_key(
                        cast(str, row["insurer_display"]),
                        cast(str, row["product_display"]),
                    )
                    for row in policy_labels
                ]
            ),
            coverage_label_key_counts=_label_counts(
                [
                    operational_label_key(
                        cast(str, row["insurer_display"]),
                        cast(str, row["product_display"]),
                        cast(str, row["display_name"]),
                    )
                    for row in coverage_labels
                ]
            ),
        )

    @staticmethod
    def _records(
        connection: psycopg.Connection[dict[str, Any]],
        statement: str,
        *parameters: UUID,
    ) -> list[object]:
        rows = connection.execute(statement, parameters).fetchall()
        return [row["record"] for row in rows]

    @staticmethod
    def _current_knowledge_snapshot(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID | None,
    ) -> tuple[KnowledgeEntityCounts, dict[str, list[object]]]:
        if run_id is None:
            return KnowledgeEntityCounts.zero(), {}
        counts: dict[str, int] = {}
        records: dict[str, list[object]] = {}
        for field_name, table_name in _KNOWLEDGE_TABLES:
            projection = "to_jsonb(item) - 'source_record_json'"
            if table_name == "private_knowledge_subjects":
                projection += (
                    " - 'family_member_id' - 'binding_decision'"
                    " - 'binding_conflict' - 'binding_reason_code'"
                    " - 'binding_confirmed_by' - 'binding_confirmed_at'"
                )
            rows = connection.execute(
                f"""
                SELECT {projection} AS record
                FROM {table_name} AS item
                WHERE item.import_run_id = %s
                ORDER BY item.id
                """,
                (run_id,),
            ).fetchall()
            counts[field_name] = len(rows)
            records[field_name] = [row["record"] for row in rows]
        return KnowledgeEntityCounts.model_validate(counts), records

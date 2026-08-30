"""PostgreSQL reconciliation baseline for private knowledge imports."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row

from familycare_api.private_knowledge.reconciliation import (
    BaselineCounts,
    KnowledgeDatabaseBaseline,
    KnowledgeEntityCounts,
    LabelKeyCount,
    operational_label_key,
)


class PrivateKnowledgeRepositoryErrorCode(StrEnum):
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    HOUSEHOLD_NOT_FOUND = "HOUSEHOLD_NOT_FOUND"
    TRANSACTION_MODE_INVALID = "TRANSACTION_MODE_INVALID"
    BASELINE_INVALID = "BASELINE_INVALID"


class PrivateKnowledgeRepositoryError(RuntimeError):
    """Sanitized repository error without SQL, DSNs, or private values."""

    def __init__(self, code: PrivateKnowledgeRepositoryErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


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


def _label_counts(values: list[str]) -> tuple[LabelKeyCount, ...]:
    counts = Counter(values)
    return tuple(LabelKeyCount(key=key, count=count) for key, count in sorted(counts.items()))


class PostgresPrivateKnowledgeRepository:
    """Read a private, repeatable, count-only operational reconciliation view."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

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
            rows = connection.execute(
                f"""
                SELECT to_jsonb(item) - 'source_record_json' AS record
                FROM {table_name} AS item
                WHERE item.import_run_id = %s
                ORDER BY item.id
                """,
                (run_id,),
            ).fetchall()
            counts[field_name] = len(rows)
            records[field_name] = [row["record"] for row in rows]
        return KnowledgeEntityCounts.model_validate(counts), records

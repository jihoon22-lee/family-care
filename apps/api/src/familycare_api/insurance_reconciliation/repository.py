"""PostgreSQL boundary for the member insurance reconciliation projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.domain import DocumentRole, UnreadableSource
from familycare_api.insurance_documents.repository import _processing_state
from familycare_api.insurance_reconciliation.domain import (
    DocumentResolutionHistory,
    KnowledgeContractSource,
    MemberInsuranceReconciliation,
    OperationalLinkHistory,
    OperationalPolicySource,
    TriState,
    build_member_reconciliation,
)

_MAX_CONTRACTS = 256
_MAX_POLICIES = 256
_MAX_UNREADABLE_SOURCES = 1000


class ReconciliationRepositoryError(RuntimeError):
    """Base sanitized persistence error."""


class ReconciliationRepositoryConflict(ReconciliationRepositoryError):
    """The target state or expected current history changed."""


class ReconciliationRepositoryNotFound(ReconciliationRepositoryError):
    """The scoped mutation target does not exist."""


class ReconciliationRepositoryTooLarge(ReconciliationRepositoryError):
    """The bounded projection cannot be represented safely."""


class ReconciliationRepositoryUnavailable(ReconciliationRepositoryError):
    """The persistence boundary could not complete the operation."""


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReconciliationRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _digest(values: dict[str, object]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _uuid_text(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _link(row: dict[str, Any]) -> OperationalLinkHistory:
    return OperationalLinkHistory(
        id=cast(UUID, row["id"]),
        knowledge_contract_id=cast(UUID, row["knowledge_contract_id"]),
        policy_contract_id=cast(UUID | None, row["policy_contract_id"]),
        decision=cast(TriState, row["decision"]),
        conflict=bool(row["link_conflict"]),
        authority="USER_CONFIRMED_OPERATIONAL_IDENTITY",
        reason_code=cast(str, row["reason_code"]),
        confirmed_at=row["confirmed_at"],
    )


def _resolution(row: dict[str, Any]) -> DocumentResolutionHistory:
    return DocumentResolutionHistory(
        id=cast(UUID, row["id"]),
        failed_item_id=cast(UUID, row["failed_item_id"]),
        replacement_item_id=cast(UUID | None, row["replacement_item_id"]),
        resolution=cast(Any, row["resolution"]),
        authority="USER_CONFIRMED_DOCUMENT_RESOLUTION",
        reason_code=cast(str, row["reason_code"]),
        confirmed_at=row["confirmed_at"],
    )


class InsuranceReconciliationRepository:
    """Keep private catalog identity separate from operational evidence readiness."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def get_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
    ) -> MemberInsuranceReconciliation | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                member = connection.execute(
                    """
                    SELECT id FROM family_members
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    """,
                    (member_id, scope.household_space_id),
                ).fetchone()
                if member is None:
                    return None
                run = connection.execute(
                    """
                    SELECT id FROM private_knowledge_import_runs
                    WHERE household_space_id = %s AND state = 'APPLIED' AND is_current
                    """,
                    (scope.household_space_id,),
                ).fetchone()
                if run is None:
                    return None
                run_id = cast(UUID, run["id"])
                contract_rows = connection.execute(
                    """
                    SELECT contract.id, contract.insurer_display,
                           contract.product_display, contract.certificate_decision,
                           CASE WHEN confirmation.decision = 'MATCH'
                                THEN confirmation.confirmed_status
                                ELSE contract.current_status END AS current_status,
                           contract.policy_contract_id,
                           contract.operational_binding_decision,
                           contract.operational_binding_reason_code
                    FROM private_knowledge_contracts AS contract
                    JOIN private_knowledge_subjects AS subject
                      ON subject.id = contract.subject_id
                     AND subject.import_run_id = contract.import_run_id
                    LEFT JOIN private_knowledge_contract_confirmations AS confirmation
                      ON confirmation.knowledge_contract_id = contract.id
                     AND confirmation.import_run_id = contract.import_run_id
                     AND confirmation.is_current
                    WHERE contract.import_run_id = %s
                      AND contract.household_space_id = %s
                      AND subject.family_member_id = %s
                      AND subject.binding_decision = 'MATCH'
                      AND NOT subject.binding_conflict
                    ORDER BY contract.id
                    LIMIT %s
                    """,
                    (run_id, scope.household_space_id, member_id, _MAX_CONTRACTS + 1),
                ).fetchall()
                if len(contract_rows) > _MAX_CONTRACTS:
                    raise ReconciliationRepositoryTooLarge
                link_rows = connection.execute(
                    """
                    SELECT id, knowledge_contract_id, policy_contract_id, decision,
                           link_conflict, authority, reason_code, confirmed_at
                    FROM private_knowledge_operational_links
                    WHERE import_run_id = %s AND household_space_id = %s
                      AND family_member_id = %s AND is_current
                    ORDER BY knowledge_contract_id
                    """,
                    (run_id, scope.household_space_id, member_id),
                ).fetchall()
                policy_rows = connection.execute(
                    """
                    SELECT DISTINCT policy.id, policy.insurer_display,
                           policy.product_display, policy.status,
                           EXISTS (
                             SELECT 1
                             FROM insurance_document_sets AS document_set
                             JOIN insurance_document_set_items AS set_item
                               ON set_item.insurance_document_set_id = document_set.id
                              AND set_item.deleted_at IS NULL
                              AND set_item.match_state = 'USER_CONFIRMED'
                             JOIN insurance_document_components AS component
                               ON component.id = set_item.insurance_document_component_id
                              AND component.deleted_at IS NULL
                              AND component.review_state = 'USER_CONFIRMED'
                             WHERE document_set.household_space_id = policy.household_space_id
                               AND document_set.family_member_id = %s
                               AND document_set.policy_contract_id = policy.id
                               AND document_set.deleted_at IS NULL
                               AND component.role = 'terms'
                           ) AS has_terms,
                           EXISTS (
                             SELECT 1
                             FROM insurance_document_sets AS document_set
                             JOIN insurance_document_set_items AS set_item
                               ON set_item.insurance_document_set_id = document_set.id
                              AND set_item.deleted_at IS NULL
                              AND set_item.match_state = 'USER_CONFIRMED'
                             JOIN insurance_document_components AS component
                               ON component.id = set_item.insurance_document_component_id
                              AND component.deleted_at IS NULL
                              AND component.review_state = 'USER_CONFIRMED'
                             WHERE document_set.household_space_id = policy.household_space_id
                               AND document_set.family_member_id = %s
                               AND document_set.policy_contract_id = policy.id
                               AND document_set.deleted_at IS NULL
                               AND component.role = 'product_explanation'
                           ) AS has_product_explanation,
                           EXISTS (
                             SELECT 1
                             FROM insurance_document_sets AS document_set
                             JOIN insurance_document_set_items AS set_item
                               ON set_item.insurance_document_set_id = document_set.id
                              AND set_item.deleted_at IS NULL
                              AND set_item.match_state = 'USER_CONFIRMED'
                             JOIN insurance_document_components AS component
                               ON component.id = set_item.insurance_document_component_id
                              AND component.deleted_at IS NULL
                              AND component.review_state = 'USER_CONFIRMED'
                             WHERE document_set.household_space_id = policy.household_space_id
                               AND document_set.family_member_id = %s
                               AND document_set.policy_contract_id = policy.id
                               AND document_set.deleted_at IS NULL
                               AND component.role = 'application'
                           ) AS has_application
                    FROM policy_contracts AS policy
                    JOIN policy_parties AS party
                      ON party.policy_contract_id = policy.id
                     AND party.household_space_id = policy.household_space_id
                     AND party.deleted_at IS NULL
                    WHERE policy.household_space_id = %s
                      AND policy.deleted_at IS NULL
                      AND party.family_member_id = %s
                    ORDER BY policy.id
                    LIMIT %s
                    """,
                    (
                        member_id,
                        member_id,
                        member_id,
                        scope.household_space_id,
                        member_id,
                        _MAX_POLICIES + 1,
                    ),
                ).fetchall()
                if len(policy_rows) > _MAX_POLICIES:
                    raise ReconciliationRepositoryTooLarge
                unreadable_rows = connection.execute(
                    """
                    SELECT item.id AS document_batch_item_id, item.document_kind,
                           item.state AS item_state, item.ocr_state
                    FROM document_batch_items AS item
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    WHERE batch.household_space_id = %s
                      AND batch.family_member_id = %s
                      AND (item.state IN ('password_required', 'permanently_failed')
                           OR item.ocr_state = 'failed')
                      AND NOT EXISTS (
                        SELECT 1 FROM document_batch_item_resolutions AS resolution
                        WHERE resolution.failed_item_id = item.id
                          AND resolution.household_space_id = batch.household_space_id
                          AND resolution.family_member_id = batch.family_member_id
                          AND resolution.is_current
                          AND resolution.resolution IN ('REPLACED', 'DISMISSED')
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM document_batch_items AS resolved_item
                        JOIN document_batches AS resolved_batch
                          ON resolved_batch.id = resolved_item.batch_id
                        WHERE resolved_batch.household_space_id = batch.household_space_id
                          AND resolved_batch.family_member_id = batch.family_member_id
                          AND resolved_item.source_id = item.source_id
                          AND resolved_item.state = 'succeeded'
                          AND resolved_item.processed_document_version_id IS NOT NULL
                          AND resolved_item.ocr_state <> 'failed'
                          AND resolved_item.completed_at > COALESCE(
                            item.completed_at, item.updated_at, item.created_at)
                      )
                    ORDER BY item.created_at, item.id
                    LIMIT %s
                    """,
                    (scope.household_space_id, member_id, _MAX_UNREADABLE_SOURCES + 1),
                ).fetchall()
                if len(unreadable_rows) > _MAX_UNREADABLE_SOURCES:
                    raise ReconciliationRepositoryTooLarge
                generated = connection.execute(
                    "SELECT clock_timestamp() AS generated_at"
                ).fetchone()
                if generated is None:
                    raise ReconciliationRepositoryUnavailable
        except ReconciliationRepositoryError:
            raise
        except psycopg.Error:
            raise ReconciliationRepositoryUnavailable from None

        labels: dict[DocumentRole, str] = {
            "policy": "보험증권 문서",
            "terms": "보험약관 문서",
            "product_explanation": "상품설명서 문서",
            "application": "청약서 문서",
            "supporting": "보조자료 문서",
        }
        try:
            contracts = tuple(
                KnowledgeContractSource(
                    id=cast(UUID, row["id"]),
                    insurer_display=cast(str, row["insurer_display"]),
                    product_display=cast(str, row["product_display"]),
                    certificate_decision=cast(TriState, row["certificate_decision"]),
                    current_status=cast(Any, row["current_status"]),
                    snapshot_policy_contract_id=cast(UUID | None, row["policy_contract_id"]),
                    snapshot_operational_decision=cast(
                        TriState, row["operational_binding_decision"]
                    ),
                    snapshot_operational_reason_code=cast(
                        str, row["operational_binding_reason_code"]
                    ),
                )
                for row in contract_rows
            )
            policies = tuple(
                OperationalPolicySource(
                    id=cast(UUID, row["id"]),
                    insurer_display=cast(str, row["insurer_display"]),
                    product_display=cast(str, row["product_display"]),
                    status=cast(Any, row["status"]),
                    completeness=(
                        "CERTIFICATE_AND_TERMS" if row["has_terms"] else "CERTIFICATE_ONLY"
                    ),
                    has_product_explanation=bool(row["has_product_explanation"]),
                    has_application=bool(row["has_application"]),
                )
                for row in policy_rows
            )
            unreadable = tuple(
                UnreadableSource(
                    document_batch_item_id=cast(UUID, row["document_batch_item_id"]),
                    source_kind=cast(DocumentRole, row["document_kind"]),
                    display_label=labels[cast(DocumentRole, row["document_kind"])],
                    processing_state=cast(Any, _processing_state(row)),
                )
                for row in unreadable_rows
            )
            return build_member_reconciliation(
                member_id=member_id,
                knowledge_run_id=run_id,
                generated_at=generated["generated_at"],
                contracts=contracts,
                current_links=tuple(_link(row) for row in link_rows),
                operational_policies=policies,
                unresolved_sources=unreadable,
            )
        except KeyError, TypeError, ValueError:
            raise ReconciliationRepositoryUnavailable from None

    def confirm_operational_link(
        self,
        scope: HouseholdScope,
        *,
        actor_id: UUID,
        knowledge_contract_id: UUID,
        decision: TriState,
        conflict: bool,
        policy_contract_id: UUID | None,
        reason_code: str,
        expected_current_link_id: UUID | None,
    ) -> OperationalLinkHistory:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                contract = connection.execute(
                    """
                    SELECT contract.import_run_id, subject.family_member_id
                    FROM private_knowledge_contracts AS contract
                    JOIN private_knowledge_import_runs AS run
                      ON run.id = contract.import_run_id
                     AND run.household_space_id = contract.household_space_id
                     AND run.state = 'APPLIED' AND run.is_current
                    JOIN private_knowledge_subjects AS subject
                      ON subject.id = contract.subject_id
                     AND subject.import_run_id = contract.import_run_id
                    WHERE contract.id = %s AND contract.household_space_id = %s
                      AND subject.binding_decision = 'MATCH'
                      AND NOT subject.binding_conflict
                    FOR UPDATE OF contract, subject, run
                    """,
                    (knowledge_contract_id, scope.household_space_id),
                ).fetchone()
                if contract is None:
                    raise ReconciliationRepositoryNotFound
                member_id = cast(UUID, contract["family_member_id"])
                if decision == "MATCH":
                    policy = connection.execute(
                        """
                        SELECT policy.id
                        FROM policy_contracts AS policy
                        JOIN policy_parties AS party
                          ON party.policy_contract_id = policy.id
                         AND party.household_space_id = policy.household_space_id
                         AND party.deleted_at IS NULL
                        WHERE policy.id = %s AND policy.household_space_id = %s
                          AND policy.deleted_at IS NULL
                          AND party.family_member_id = %s
                        FOR UPDATE OF policy, party
                        """,
                        (policy_contract_id, scope.household_space_id, member_id),
                    ).fetchone()
                    if policy is None:
                        raise ReconciliationRepositoryConflict
                current = connection.execute(
                    """
                    SELECT id, knowledge_contract_id, policy_contract_id, decision,
                           link_conflict, authority, reason_code, confirmed_at
                    FROM private_knowledge_operational_links
                    WHERE knowledge_contract_id = %s AND is_current
                    FOR UPDATE
                    """,
                    (knowledge_contract_id,),
                ).fetchone()
                current_id = cast(UUID | None, current["id"] if current else None)
                if current_id != expected_current_link_id:
                    raise ReconciliationRepositoryConflict
                if current is not None and (
                    current["decision"] == decision
                    and bool(current["link_conflict"]) == conflict
                    and current["policy_contract_id"] == policy_contract_id
                    and current["reason_code"] == reason_code
                ):
                    return _link(current)
                if current is not None:
                    connection.execute(
                        """
                        UPDATE private_knowledge_operational_links
                        SET is_current = false, superseded_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (current_id,),
                    )
                digest = _digest(
                    {
                        "actor_id": str(actor_id),
                        "conflict": conflict,
                        "decision": decision,
                        "expected_current_link_id": _uuid_text(expected_current_link_id),
                        "knowledge_contract_id": str(knowledge_contract_id),
                        "policy_contract_id": _uuid_text(policy_contract_id),
                        "reason_code": reason_code,
                        "run_id": str(contract["import_run_id"]),
                    }
                )
                row = connection.execute(
                    """
                    INSERT INTO private_knowledge_operational_links (
                      import_run_id, household_space_id, family_member_id,
                      knowledge_contract_id, policy_contract_id, decision,
                      link_conflict, authority, reason_code, confirmed_by,
                      link_digest_sha256
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s,
                      'USER_CONFIRMED_OPERATIONAL_IDENTITY', %s, %s, %s
                    )
                    RETURNING id, knowledge_contract_id, policy_contract_id, decision,
                              link_conflict, authority, reason_code, confirmed_at
                    """,
                    (
                        contract["import_run_id"],
                        scope.household_space_id,
                        member_id,
                        knowledge_contract_id,
                        policy_contract_id,
                        decision,
                        conflict,
                        reason_code,
                        actor_id,
                        digest,
                    ),
                ).fetchone()
                if row is None:
                    raise ReconciliationRepositoryUnavailable
                return _link(row)
        except ReconciliationRepositoryError:
            raise
        except (
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
            psycopg.errors.UniqueViolation,
        ):
            raise ReconciliationRepositoryConflict from None
        except psycopg.Error:
            raise ReconciliationRepositoryUnavailable from None

    def confirm_document_resolution(
        self,
        scope: HouseholdScope,
        *,
        actor_id: UUID,
        failed_item_id: UUID,
        resolution: str,
        replacement_item_id: UUID | None,
        reason_code: str,
        expected_current_resolution_id: UUID | None,
    ) -> DocumentResolutionHistory:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                failed = connection.execute(
                    """
                    SELECT item.id, batch.household_space_id, batch.family_member_id,
                           COALESCE(item.completed_at, item.updated_at, item.created_at)
                             AS failed_at
                    FROM document_batch_items AS item
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    WHERE item.id = %s AND batch.household_space_id = %s
                      AND (item.state IN ('password_required', 'permanently_failed')
                           OR item.ocr_state = 'failed')
                    FOR UPDATE OF item, batch
                    """,
                    (failed_item_id, scope.household_space_id),
                ).fetchone()
                if failed is None:
                    raise ReconciliationRepositoryNotFound
                member_id = cast(UUID, failed["family_member_id"])
                if resolution == "REPLACED":
                    replacement = connection.execute(
                        """
                        SELECT replacement.id
                        FROM document_batch_items AS replacement
                        JOIN document_batches AS replacement_batch
                          ON replacement_batch.id = replacement.batch_id
                        WHERE replacement.id = %s
                          AND replacement_batch.household_space_id = %s
                          AND replacement_batch.family_member_id = %s
                          AND replacement.state = 'succeeded'
                          AND replacement.processed_document_version_id IS NOT NULL
                          AND replacement.ocr_state <> 'failed'
                          AND replacement.completed_at > %s
                        FOR UPDATE OF replacement, replacement_batch
                        """,
                        (
                            replacement_item_id,
                            scope.household_space_id,
                            member_id,
                            failed["failed_at"],
                        ),
                    ).fetchone()
                    if replacement is None:
                        raise ReconciliationRepositoryConflict
                current = connection.execute(
                    """
                    SELECT id, failed_item_id, replacement_item_id, resolution,
                           authority, reason_code, confirmed_at
                    FROM document_batch_item_resolutions
                    WHERE failed_item_id = %s AND is_current
                    FOR UPDATE
                    """,
                    (failed_item_id,),
                ).fetchone()
                current_id = cast(UUID | None, current["id"] if current else None)
                if current_id != expected_current_resolution_id:
                    raise ReconciliationRepositoryConflict
                if current is not None and (
                    current["resolution"] == resolution
                    and current["replacement_item_id"] == replacement_item_id
                    and current["reason_code"] == reason_code
                ):
                    return _resolution(current)
                if current is not None:
                    connection.execute(
                        """
                        UPDATE document_batch_item_resolutions
                        SET is_current = false, superseded_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (current_id,),
                    )
                digest = _digest(
                    {
                        "actor_id": str(actor_id),
                        "expected_current_resolution_id": _uuid_text(
                            expected_current_resolution_id
                        ),
                        "failed_item_id": str(failed_item_id),
                        "reason_code": reason_code,
                        "replacement_item_id": _uuid_text(replacement_item_id),
                        "resolution": resolution,
                    }
                )
                row = connection.execute(
                    """
                    INSERT INTO document_batch_item_resolutions (
                      household_space_id, family_member_id, failed_item_id,
                      replacement_item_id, resolution, authority, reason_code,
                      confirmed_by, resolution_digest_sha256
                    ) VALUES (
                      %s, %s, %s, %s, %s,
                      'USER_CONFIRMED_DOCUMENT_RESOLUTION', %s, %s, %s
                    )
                    RETURNING id, failed_item_id, replacement_item_id, resolution,
                              authority, reason_code, confirmed_at
                    """,
                    (
                        scope.household_space_id,
                        member_id,
                        failed_item_id,
                        replacement_item_id,
                        resolution,
                        reason_code,
                        actor_id,
                        digest,
                    ),
                ).fetchone()
                if row is None:
                    raise ReconciliationRepositoryUnavailable
                return _resolution(row)
        except ReconciliationRepositoryError:
            raise
        except (
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
            psycopg.errors.UniqueViolation,
        ):
            raise ReconciliationRepositoryConflict from None
        except psycopg.Error:
            raise ReconciliationRepositoryUnavailable from None


__all__ = [
    "InsuranceReconciliationRepository",
    "ReconciliationRepositoryConflict",
    "ReconciliationRepositoryNotFound",
    "ReconciliationRepositoryTooLarge",
    "ReconciliationRepositoryUnavailable",
]

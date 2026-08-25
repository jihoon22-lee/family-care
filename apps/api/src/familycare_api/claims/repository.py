"""PostgreSQL persistence for immutable, household-scoped ClaimCases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.claims.domain import ClaimStatus
from familycare_api.claims.errors import (
    ChecklistItemNotFound,
    ClaimInvalid,
    ClaimNotFound,
    ClaimRepositoryUnavailable,
    InvalidClaimTransitionError,
)
from familycare_api.claims.snapshot import build_claim_snapshot
from familycare_api.claims.state_machine import (
    InvalidClaimTransition,
    allowed_claim_transitions,
    transition_claim_status,
)
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.domain import ClaimHistoryFact
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.policies.errors import VersionConflict


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ClaimRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class ClaimRepository:
    """Store ClaimCases without files, document bodies, or insurer submission calls."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def create_claim_case(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        insurer_key: str,
        policy_contract_id: UUID,
    ) -> dict[str, object]:
        decision_repository = DecisionRepository(self.database_url)
        event = decision_repository.get_medical_event(scope, event_id)
        result = decision_repository.get_decision_result(scope, event_id, event.version)
        policy_snapshots = tuple(
            snapshot
            for snapshot in decision_repository.for_event_date(
                scope, event.family_member_id, event.event_date
            )
            if snapshot.policy_id == policy_contract_id
        )
        if not policy_snapshots:
            raise ClaimInvalid
        rider_ids = {snapshot.rider_id for snapshot in policy_snapshots}
        candidates = tuple(item for item in result.candidates if item.rider_id in rider_ids)
        if not candidates:
            raise ClaimInvalid
        evaluations = tuple(item for item in result.evaluations if item.rider_id in rider_ids)
        scoped_result = replace(result, candidates=candidates, evaluations=evaluations)
        rules = tuple(
            rule
            for rider_id in sorted(rider_ids, key=str)
            for rule in decision_repository.executable_for_rider(scope, rider_id)
        )
        evidence = tuple(item for evaluation in evaluations for item in evaluation.evidence)
        calculations = CalculationRepository(self.database_url).calculate_event(scope, event_id)
        candidate_ids = {item.id for item in candidates if item.id is not None}
        matching_calculations = tuple(
            item for item in calculations if item.get("claim_candidate_id") in candidate_ids
        )
        snapshot = build_claim_snapshot(
            scoped_result,
            matching_calculations,
            policy_snapshot=policy_snapshots,
            rule_versions=rules,
            evidence=evidence,
        )
        claim_id = uuid4()
        snapshot_id = uuid4()
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO claim_cases (
                      id, household_space_id, medical_event_id, family_member_id,
                      policy_contract_id, insurer_key, status
                    )
                    SELECT %s, event.household_space_id, event.id, event.family_member_id,
                           policy.id, %s, 'preparing'
                    FROM medical_events AS event
                    JOIN policy_contracts AS policy
                      ON policy.id = %s
                     AND policy.household_space_id = event.household_space_id
                     AND policy.insurer_key = %s
                     AND policy.deleted_at IS NULL
                    WHERE event.id = %s AND event.household_space_id = %s
                      AND event.deleted_at IS NULL
                    RETURNING id
                    """,
                    (
                        claim_id,
                        insurer_key,
                        policy_contract_id,
                        insurer_key,
                        event_id,
                        scope.household_space_id,
                    ),
                ).fetchone()
                if row is None:
                    raise ClaimInvalid
                values = snapshot.persistence_values()
                connection.execute(
                    """
                    INSERT INTO claim_case_snapshots (
                      id, claim_case_id, snapshot_version,
                      candidate_snapshot_json, rule_snapshot_json,
                      policy_snapshot_json, evidence_snapshot_json,
                      calculation_snapshot_json, snapshot_sha256
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        snapshot_id,
                        claim_id,
                        values["snapshot_version"],
                        Jsonb(values["candidate_snapshot"]),
                        Jsonb(values["rule_snapshot"]),
                        Jsonb(values["policy_snapshot"]),
                        Jsonb(values["evidence_snapshot"]),
                        Jsonb(values["calculation_snapshot"]),
                        values["snapshot_sha256"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO claim_status_events (
                      claim_case_id, from_status, to_status, occurred_at,
                      reason_code, metadata_json
                    ) VALUES (%s, NULL, 'preparing', clock_timestamp(),
                              'CLAIM_CREATED', '{}'::jsonb)
                    """,
                    (claim_id,),
                )
                self._create_checklist(connection, claim_id, rules)
        except ClaimInvalid:
            raise
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None
        return self.get_claim_case(scope, claim_id)

    def list_claim_cases(
        self,
        scope: HouseholdScope,
        *,
        event_id: UUID | None = None,
        status: ClaimStatus | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
        deleted_only: bool = False,
    ) -> dict[str, object]:
        if limit < 1 or limit > 100:
            raise ClaimInvalid
        deletion = "IS NOT NULL" if deleted_only else "IS NULL"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    f"""
                    SELECT id FROM claim_cases
                    WHERE household_space_id = %(scope)s
                      AND deleted_at {deletion}
                      AND (%(event)s::uuid IS NULL OR medical_event_id = %(event)s)
                      AND (%(status)s::varchar IS NULL OR status = %(status)s)
                      AND (%(cursor)s::uuid IS NULL OR id > %(cursor)s)
                    ORDER BY id
                    LIMIT %(fetch)s
                    """,
                    {
                        "scope": scope.household_space_id,
                        "event": event_id,
                        "status": status,
                        "cursor": cursor,
                        "fetch": limit + 1,
                    },
                ).fetchall()
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None
        visible = rows[:limit]
        items = [
            self.get_claim_case(scope, cast(UUID, row["id"]), deleted_only=deleted_only)
            for row in visible
        ]
        next_cursor = cast(UUID, visible[-1]["id"]) if len(rows) > limit and visible else None
        return {"schema_version": "1", "items": items, "next_cursor": next_cursor}

    def get_claim_case(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> dict[str, object]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = self._claim_row(connection, scope, claim_id, deleted_only=deleted_only)
                if row is None:
                    raise ClaimNotFound
                return self._view(connection, row)
        except ClaimNotFound:
            raise
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None

    def update_claim_case(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> dict[str, object]:
        allowed = {"receipt_number", "claimed_amount", "currency", "outcome_reason_code"}
        if not changes or set(changes) - allowed:
            raise ClaimInvalid
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._claim_row(connection, scope, claim_id, for_update=True)
                if current is None:
                    raise ClaimNotFound
                if int(current["version"]) != expected_version:
                    raise VersionConflict
                if current["status"] == "closed":
                    raise ClaimInvalid
                values = {key: current.get(key) for key in allowed}
                values.update(changes)
                row = connection.execute(
                    """
                    UPDATE claim_cases
                    SET receipt_number = %s, claimed_amount = %s, currency = %s,
                        outcome_reason_code = %s, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND version = %s AND deleted_at IS NULL
                    RETURNING *
                    """,
                    (
                        values["receipt_number"],
                        values["claimed_amount"],
                        values["currency"],
                        values["outcome_reason_code"],
                        claim_id,
                        scope.household_space_id,
                        expected_version,
                    ),
                ).fetchone()
                if row is None:
                    raise VersionConflict
        except ClaimNotFound, ClaimInvalid, VersionConflict:
            raise
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None
        return self.get_claim_case(scope, claim_id)

    def transition_claim(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        target_status: ClaimStatus,
        expected_version: int,
        occurred_at: object,
        metadata: Mapping[str, object],
    ) -> dict[str, object]:
        if not isinstance(occurred_at, datetime):
            raise ClaimInvalid
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._claim_row(connection, scope, claim_id, for_update=True)
                if current is None:
                    raise ClaimNotFound
                if int(current["version"]) != expected_version:
                    raise VersionConflict
                source = cast(ClaimStatus, current["status"])
                try:
                    transition_claim_status(source, target_status)
                except InvalidClaimTransition:
                    raise InvalidClaimTransitionError from None
                reason_code = cast(str | None, metadata.get("reason_code"))
                submitted_at = current.get("submitted_at")
                paid_amount = current.get("paid_amount")
                currency = current.get("currency")
                changed_fields = ["status"]
                if target_status == "submitted":
                    submitted_at = occurred_at
                    changed_fields.append("submitted_at")
                if target_status in {"paid", "partially_paid"}:
                    paid_amount = metadata.get("amount")
                    payment_currency = metadata.get("currency")
                    if not isinstance(paid_amount, Decimal) or not isinstance(
                        payment_currency, str
                    ):
                        raise ClaimInvalid
                    if currency is not None and currency != payment_currency:
                        raise ClaimInvalid
                    currency = payment_currency
                    changed_fields.extend(("paid_amount", "payment_date"))
                if target_status == "denied":
                    changed_fields.append("outcome_reason_code")
                row = connection.execute(
                    """
                    UPDATE claim_cases
                    SET status = %s, submitted_at = %s, paid_amount = %s,
                        currency = %s, outcome_reason_code = %s,
                        version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND version = %s AND deleted_at IS NULL
                    RETURNING *
                    """,
                    (
                        target_status,
                        submitted_at,
                        paid_amount,
                        currency,
                        reason_code,
                        claim_id,
                        scope.household_space_id,
                        expected_version,
                    ),
                ).fetchone()
                if row is None:
                    raise VersionConflict
                connection.execute(
                    """
                    INSERT INTO claim_status_events (
                      claim_case_id, from_status, to_status, occurred_at,
                      reason_code, metadata_json
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        claim_id,
                        source,
                        target_status,
                        occurred_at,
                        reason_code,
                        Jsonb({"changed_fields": sorted(set(changed_fields))}),
                    ),
                )
                if target_status in {"paid", "partially_paid", "denied"}:
                    self._insert_history(
                        connection,
                        row,
                        outcome=target_status,
                        payment_date=cast(date | None, metadata.get("payment_date")),
                        amount=cast(Decimal | None, metadata.get("amount")),
                        reason_code=reason_code,
                    )
        except ClaimNotFound, ClaimInvalid, InvalidClaimTransitionError, VersionConflict:
            raise
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None
        return self.get_claim_case(scope, claim_id)

    def update_checklist_item(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        item_id: UUID,
        *,
        expected_version: int,
        prepared: bool,
        note_code: str | None,
    ) -> dict[str, object]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT item.*, claim.status
                    FROM claim_checklist_items AS item
                    JOIN claim_cases AS claim ON claim.id = item.claim_case_id
                    WHERE item.id = %s AND item.claim_case_id = %s
                      AND claim.household_space_id = %s AND claim.deleted_at IS NULL
                    FOR UPDATE OF item, claim
                    """,
                    (item_id, claim_id, scope.household_space_id),
                ).fetchone()
                if row is None:
                    raise ChecklistItemNotFound
                if int(row["version"]) != expected_version:
                    raise VersionConflict
                if row["status"] == "closed":
                    raise ClaimInvalid
                updated = connection.execute(
                    """
                    UPDATE claim_checklist_items
                    SET prepared = %s, note_code = %s, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND claim_case_id = %s AND version = %s
                    RETURNING id
                    """,
                    (prepared, note_code, item_id, claim_id, expected_version),
                ).fetchone()
                if updated is None:
                    raise VersionConflict
        except ChecklistItemNotFound, ClaimInvalid, VersionConflict:
            raise
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None
        return self.get_claim_case(scope, claim_id)

    def soft_delete_claim_case(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        expected_version: int,
    ) -> None:
        self._toggle_deleted(scope, claim_id, expected_version=expected_version, restore=False)

    def restore_claim_case(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        expected_version: int,
    ) -> dict[str, object]:
        self._toggle_deleted(scope, claim_id, expected_version=expected_version, restore=True)
        return self.get_claim_case(scope, claim_id)

    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    """
                    SELECT outcome, counted_occurrence, payment_date
                    FROM claim_history
                    WHERE household_space_id = %s AND family_member_id = %s
                    ORDER BY created_at, id
                    """,
                    (scope.household_space_id, family_member_id),
                ).fetchall()
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None
        return tuple(
            ClaimHistoryFact(
                outcome=cast(Any, row["outcome"]),
                counted_occurrence=bool(row["counted_occurrence"]),
                payment_date=cast(date | None, row["payment_date"]),
            )
            for row in rows
        )

    @staticmethod
    def _create_checklist(
        connection: psycopg.Connection[dict[str, Any]],
        claim_id: UUID,
        rules: Sequence[CoverageRuleVersion],
    ) -> None:
        for rule in rules:
            if rule.rule_kind != "required_document":
                continue
            evidence = tuple(rule.evidence)
            connection.execute(
                """
                INSERT INTO claim_checklist_items (
                  claim_case_id, document_kind, requirement_code,
                  required, conditional, prepared, source_rule_version_id,
                  source_evidence_id
                ) VALUES (%s, 'supporting_document', %s, %s, %s, false, %s, %s)
                """,
                (
                    claim_id,
                    rule.result_reason_code,
                    rule.required,
                    not rule.required,
                    rule.id,
                    evidence[0].evidence_id if evidence else None,
                ),
            )

    @staticmethod
    def _insert_history(
        connection: psycopg.Connection[dict[str, Any]],
        claim: Mapping[str, Any],
        *,
        outcome: ClaimStatus,
        payment_date: date | None,
        amount: Decimal | None,
        reason_code: str | None,
    ) -> None:
        riders = connection.execute(
            """
            SELECT id FROM riders
            WHERE household_space_id = %s AND policy_contract_id = %s
              AND deleted_at IS NULL
            ORDER BY id
            """,
            (claim["household_space_id"], claim["policy_contract_id"]),
        ).fetchall()
        rider_ids: list[UUID | None] = [cast(UUID, row["id"]) for row in riders] or [None]
        for rider_id in rider_ids:
            connection.execute(
                """
                INSERT INTO claim_history (
                  household_space_id, medical_event_id, family_member_id,
                  policy_contract_id, rider_id, outcome, payment_date,
                  counted_occurrence, amount, currency, reason_code
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    claim["household_space_id"],
                    claim["medical_event_id"],
                    claim["family_member_id"],
                    claim["policy_contract_id"],
                    rider_id,
                    outcome,
                    payment_date,
                    outcome in {"paid", "partially_paid"},
                    amount,
                    claim.get("currency"),
                    reason_code,
                ),
            )

    def _toggle_deleted(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        expected_version: int,
        restore: bool,
    ) -> None:
        deleted = "IS NOT NULL" if restore else "IS NULL"
        new_value = "NULL" if restore else "clock_timestamp()"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    f"""
                    SELECT id, version FROM claim_cases
                    WHERE id = %s AND household_space_id = %s AND deleted_at {deleted}
                    FOR UPDATE
                    """,
                    (claim_id, scope.household_space_id),
                ).fetchone()
                if row is None:
                    raise ClaimNotFound
                if int(row["version"]) != expected_version:
                    raise VersionConflict
                updated = connection.execute(
                    f"""
                    UPDATE claim_cases
                    SET deleted_at = {new_value}, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                    RETURNING id
                    """,
                    (claim_id, scope.household_space_id, expected_version),
                ).fetchone()
                if updated is None:
                    raise VersionConflict
        except ClaimNotFound, VersionConflict:
            raise
        except psycopg.Error:
            raise ClaimRepositoryUnavailable from None

    @staticmethod
    def _claim_row(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        deleted_only: bool = False,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        deleted = "IS NOT NULL" if deleted_only else "IS NULL"
        locking = "FOR UPDATE" if for_update else ""
        return connection.execute(
            f"""
            SELECT * FROM claim_cases
            WHERE id = %s AND household_space_id = %s AND deleted_at {deleted}
            {locking}
            """,
            (claim_id, scope.household_space_id),
        ).fetchone()

    @staticmethod
    def _view(
        connection: psycopg.Connection[dict[str, Any]], claim: Mapping[str, Any]
    ) -> dict[str, object]:
        snapshot = connection.execute(
            """
            SELECT * FROM claim_case_snapshots
            WHERE claim_case_id = %s
            ORDER BY snapshot_version DESC LIMIT 1
            """,
            (claim["id"],),
        ).fetchone()
        if snapshot is None:
            raise ClaimRepositoryUnavailable
        checklist = connection.execute(
            """
            SELECT * FROM claim_checklist_items
            WHERE claim_case_id = %s ORDER BY created_at, id
            """,
            (claim["id"],),
        ).fetchall()
        events = connection.execute(
            """
            SELECT * FROM claim_status_events
            WHERE claim_case_id = %s ORDER BY occurred_at, created_at, id
            """,
            (claim["id"],),
        ).fetchall()
        status = cast(ClaimStatus, claim["status"])
        return {
            "schema_version": "1",
            "id": claim["id"],
            "medical_event_id": claim["medical_event_id"],
            "family_member_id": claim["family_member_id"],
            "policy_contract_id": claim["policy_contract_id"],
            "insurer_key": claim["insurer_key"],
            "status": status,
            "receipt_number": claim.get("receipt_number"),
            "submitted_at": claim.get("submitted_at"),
            "claimed_amount": claim.get("claimed_amount"),
            "paid_amount": claim.get("paid_amount"),
            "currency": claim.get("currency"),
            "outcome_reason_code": claim.get("outcome_reason_code"),
            "version": claim["version"],
            "deleted": claim.get("deleted_at") is not None,
            "allowed_transitions": sorted(allowed_claim_transitions(status)),
            "snapshot": _snapshot_view(snapshot, cast(UUID, claim["policy_contract_id"])),
            "checklist": [
                {
                    "id": row["id"],
                    "document_kind": row["document_kind"],
                    "requirement_code": row["requirement_code"],
                    "required": row["required"],
                    "conditional": row["conditional"],
                    "prepared": row["prepared"],
                    "note_code": row.get("note_code"),
                    "source_rule_version_id": row.get("source_rule_version_id"),
                    "source_evidence_id": row.get("source_evidence_id"),
                    "version": row["version"],
                }
                for row in checklist
            ],
            "status_events": [
                {
                    "from_status": row.get("from_status"),
                    "to_status": row["to_status"],
                    "occurred_at": row["occurred_at"],
                    "reason_code": row.get("reason_code"),
                }
                for row in events
            ],
        }


def _snapshot_view(snapshot: Mapping[str, Any], policy_id: UUID) -> dict[str, object]:
    candidate = cast(Mapping[str, Any], snapshot["candidate_snapshot_json"])
    candidates = tuple(
        item for item in candidate.get("candidates", ()) if isinstance(item, Mapping)
    )
    rules = cast(Mapping[str, Any], snapshot["rule_snapshot_json"])
    versions = tuple(item for item in rules.get("versions", ()) if isinstance(item, Mapping))
    policy = cast(Mapping[str, Any], snapshot["policy_snapshot_json"])
    policies = tuple(item for item in policy.get("snapshots", ()) if isinstance(item, Mapping))
    evidence = cast(Mapping[str, Any], snapshot["evidence_snapshot_json"])
    evidence_items = tuple(
        item for item in evidence.get("evidence", ()) if isinstance(item, Mapping)
    )
    calculation = cast(Mapping[str, Any], snapshot["calculation_snapshot_json"])
    calculations = tuple(
        item for item in calculation.get("calculations", ()) if isinstance(item, Mapping)
    ) or ((calculation,) if calculation.get("present") else ())
    return {
        "snapshot_version": snapshot["snapshot_version"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "candidate": {
            "candidate_ids": [item["id"] for item in candidates if item.get("id")],
            "rider_ids": [item["rider_id"] for item in candidates if item.get("rider_id")],
            "aggregate_results": [
                item["aggregate_result"] for item in candidates if item.get("aggregate_result")
            ],
        },
        "rules": {
            "rule_version_ids": [item["id"] for item in versions if item.get("id")],
            "reason_codes": [
                item["result_reason_code"] for item in versions if item.get("result_reason_code")
            ],
            "evaluator_versions": sorted(
                {
                    str(evaluation["evaluator_version"])
                    for item in candidates
                    for evaluation in cast(Sequence[Mapping[str, Any]], item.get("evaluations", ()))
                    if evaluation.get("evaluator_version")
                }
            ),
        },
        "policy": {
            "policy_contract_id": policy_id,
            "rider_ids": [item["rider_id"] for item in policies if item.get("rider_id")],
            "status_codes": [
                item["effective_status"] for item in policies if item.get("effective_status")
            ],
            "captured_at": candidate.get("policy_snapshot_at"),
        },
        "evidence": {
            "evidence_ids": [
                item["evidence_id"] for item in evidence_items if item.get("evidence_id")
            ],
            "content_sha256": [
                item["content_sha256"] for item in evidence_items if item.get("content_sha256")
            ],
        },
        "calculation": {
            "calculation_ids": [
                item["calculation_id"] for item in calculations if item.get("calculation_id")
            ],
            "versions": [item["version"] for item in calculations if item.get("version")],
            "statuses": [item["status"] for item in calculations if item.get("status")],
        },
    }


__all__ = ["ClaimRepository"]

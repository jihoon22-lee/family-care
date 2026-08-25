"""PostgreSQL unit of work for structured events and immutable decision runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import (
    ClaimCandidate,
    ClaimHistoryFact,
    DecisionReaders,
    DecisionRunResult,
    FactConfirmation,
    FactValue,
    MedicalEvent,
    PolicySnapshot,
    Question,
    RuleEvaluation,
)
from familycare_api.decisions.engine import DeterministicCoverageDecisionEngine
from familycare_api.decisions.errors import (
    DecisionInvalid,
    DecisionRepositoryUnavailable,
    DecisionResultNotFound,
    MedicalEventNotFound,
)
from familycare_api.decisions.facts import FactNormalizationError, normalize_facts
from familycare_api.decisions.structuring_repository import _facts as _structured_fact_records
from familycare_api.decisions.structuring_repository import _merge_user_overrides
from familycare_api.decisions.structuring_repository import (
    _questions as _structured_question_records,
)
from familycare_api.policies.errors import EvidenceInvalid, VersionConflict


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise DecisionRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class DecisionRepository:
    """Use one repeatable-read transaction for input snapshot and result writes."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def create_medical_event(
        self,
        scope: HouseholdScope,
        *,
        family_member_id: UUID,
        mode: str,
        situation: str,
        event_date: date | None,
        visit_date: date | None,
        facts: Mapping[str, FactValue],
    ) -> MedicalEvent:
        values, confirmations = _event_json(facts)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO medical_events (
                      household_space_id, family_member_id, mode, situation_text,
                      event_date, visit_date, facts_json, confirmation_json
                    )
                    SELECT %s, member.id, %s, %s, %s, %s, %s, %s
                    FROM family_members AS member
                    WHERE member.id = %s
                      AND member.household_space_id = %s
                      AND member.deleted_at IS NULL
                    RETURNING *
                    """,
                    (
                        scope.household_space_id,
                        mode,
                        situation,
                        event_date,
                        visit_date,
                        Jsonb(values),
                        Jsonb(confirmations),
                        family_member_id,
                        scope.household_space_id,
                    ),
                ).fetchone()
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise MedicalEventNotFound
        return _medical_event(row)

    def get_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> MedicalEvent:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = self._event_row(
                    connection,
                    scope,
                    event_id,
                    deleted_only=deleted_only,
                )
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise MedicalEventNotFound
        return _medical_event(row)

    def list_deleted_medical_events(self, scope: HouseholdScope) -> list[MedicalEvent]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM medical_events
                    WHERE household_space_id = %s AND deleted_at IS NOT NULL
                    ORDER BY deleted_at DESC, id
                    """,
                    (scope.household_space_id,),
                ).fetchall()
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        return [_medical_event(row) for row in rows]

    def update_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
        **changes: object,
    ) -> MedicalEvent:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._event_row(connection, scope, event_id, for_update=True)
                if current is None:
                    raise MedicalEventNotFound
                if int(current["version"]) != expected_version:
                    raise VersionConflict
                mode = changes.get("mode", current["mode"])
                situation = changes.get("situation", current.get("situation_text", ""))
                event_date = changes.get("event_date", current.get("event_date"))
                visit_date = changes.get("visit_date", current.get("visit_date"))
                fact_values = current["facts_json"]
                confirmation_values = current["confirmation_json"]
                structured_values = current.get("structured_facts_json")
                structured_questions = current.get("structured_questions_json")
                structured_issues = current.get("structured_issues_json")
                structured_update: (
                    tuple[
                        dict[str, dict[str, object]],
                        list[dict[str, str]],
                        tuple[str, ...],
                        bool,
                    ]
                    | None
                ) = None
                if "facts" in changes:
                    facts = changes["facts"]
                    if not isinstance(facts, Mapping):
                        raise DecisionRepositoryUnavailable
                    fact_values, confirmation_values = _event_json(
                        cast(Mapping[str, FactValue], facts)
                    )
                if "structured_facts" in changes:
                    overrides = changes["structured_facts"]
                    if not isinstance(overrides, Mapping):
                        raise DecisionRepositoryUnavailable
                    structured_update = _merge_user_overrides(
                        structured_values,
                        structured_questions,
                        cast(Mapping[Any, str | bool | None], overrides),
                    )
                    structured_values, structured_questions, _, _ = structured_update
                    fact_values = dict(cast(Mapping[str, object | None], fact_values))
                    confirmation_values = dict(
                        cast(Mapping[str, FactConfirmation], confirmation_values)
                    )
                    condition_value = overrides.get("condition_class")
                    if "condition_class" in overrides:
                        fact_values["MedicalEvent.classification"] = condition_value
                        confirmation_values["MedicalEvent.classification"] = (
                            "user" if condition_value is not None else "unconfirmed"
                        )
                    if "admission" in overrides:
                        admission = overrides.get("admission")
                        fact_values["MedicalEvent.admission_days"] = (
                            0 if admission is False else None
                        )
                        confirmation_values["MedicalEvent.admission_days"] = (
                            "user" if admission is False else "unconfirmed"
                        )
                    if "event_date" in overrides:
                        event_date = _structured_date(overrides.get("event_date"))
                    if "visit_date" in overrides:
                        visit_date = _structured_date(overrides.get("visit_date"))
                row = connection.execute(
                    """
                    UPDATE medical_events
                    SET mode = %s, situation_text = %s, event_date = %s, visit_date = %s,
                        facts_json = %s, confirmation_json = %s,
                        version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND version = %s AND deleted_at IS NULL
                    RETURNING *
                    """,
                    (
                        mode,
                        situation,
                        event_date,
                        visit_date,
                        Jsonb(fact_values),
                        Jsonb(confirmation_values),
                        event_id,
                        scope.household_space_id,
                        expected_version,
                    ),
                ).fetchone()
                if row is not None and structured_update is not None:
                    (
                        structured_values,
                        structured_questions,
                        changed_fields,
                        conflict,
                    ) = structured_update
                    parent_id = current.get("structured_fact_version_id")
                    parent_version = int(current.get("structured_fact_version") or 0)
                    if parent_id is not None:
                        connection.execute(
                            """
                            UPDATE medical_event_fact_versions
                            SET is_current = false, version_state = 'superseded'
                            WHERE id = %s AND household_space_id = %s
                            """,
                            (parent_id, scope.household_space_id),
                        )
                    fact_version = connection.execute(
                        """
                        INSERT INTO medical_event_fact_versions (
                          household_space_id, medical_event_id, structuring_job_id,
                          parent_version_id, event_version, version, source,
                          version_state, facts_json, questions_json,
                          issue_codes_json, is_current
                        ) VALUES (
                          %s, %s, NULL, %s, %s, %s, 'user', 'applied',
                          %s, %s, %s, true
                        )
                        RETURNING id
                        """,
                        (
                            scope.household_space_id,
                            event_id,
                            parent_id,
                            int(row["version"]),
                            parent_version + 1,
                            Jsonb(structured_values),
                            Jsonb(structured_questions),
                            Jsonb(structured_issues if isinstance(structured_issues, list) else []),
                        ),
                    ).fetchone()
                    if fact_version is None:
                        raise DecisionRepositoryUnavailable
                    action = (
                        "conflict_detected"
                        if conflict
                        else ("overridden" if parent_id is not None else "created")
                    )
                    reason_code = "USER_AI_CONFLICT" if conflict else "USER_OVERRIDE"
                    connection.execute(
                        """
                        INSERT INTO medical_event_fact_audit (
                          household_space_id, medical_event_id, fact_version_id,
                          parent_version_id, event_version, action, actor_kind,
                          changed_fields_json, reason_code
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'user', %s, %s)
                        """,
                        (
                            scope.household_space_id,
                            event_id,
                            fact_version["id"],
                            parent_id,
                            int(row["version"]),
                            action,
                            Jsonb(list(changed_fields)),
                            reason_code,
                        ),
                    )
                    row["structured_facts_json"] = structured_values
                    row["structured_questions_json"] = structured_questions
                    row["structured_issues_json"] = (
                        structured_issues if isinstance(structured_issues, list) else []
                    )
                elif row is not None:
                    row["structured_facts_json"] = structured_values
                    row["structured_questions_json"] = structured_questions
                    row["structured_issues_json"] = structured_issues
        except MedicalEventNotFound, VersionConflict:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        return _medical_event(row)

    def soft_delete_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
    ) -> MedicalEvent:
        return self._transition_event(
            scope,
            event_id,
            expected_version=expected_version,
            restore=False,
        )

    def restore_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
    ) -> MedicalEvent:
        return self._transition_event(
            scope,
            event_id,
            expected_version=expected_version,
            restore=True,
        )

    def _transition_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
        restore: bool,
    ) -> MedicalEvent:
        deleted_predicate = "IS NOT NULL" if restore else "IS NULL"
        deleted_value = "NULL" if restore else "clock_timestamp()"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                visible = self._event_row(
                    connection,
                    scope,
                    event_id,
                    deleted_only=restore,
                    for_update=True,
                )
                if visible is None:
                    raise MedicalEventNotFound
                if int(visible["version"]) != expected_version:
                    raise VersionConflict
                row = connection.execute(
                    f"""
                    UPDATE medical_events
                    SET deleted_at = {deleted_value}, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND version = %s AND deleted_at {deleted_predicate}
                    RETURNING *
                    """,
                    (event_id, scope.household_space_id, expected_version),
                ).fetchone()
        except MedicalEventNotFound, VersionConflict:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        return _medical_event(row)

    def analyze_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
    ) -> DecisionRunResult:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                row = self._event_row(connection, scope, event_id, for_update=True)
                if row is None:
                    raise MedicalEventNotFound
                event = _medical_event(row)
                readers = _ConnectionReaders(self, connection)
                ports = DecisionReaders(
                    policy=readers,
                    rules=readers,
                    evidence=readers,
                    history=readers,
                )
                result = DeterministicCoverageDecisionEngine(ports).evaluate(scope, event)
                self._persist_result(connection, scope, result)
                return result
        except MedicalEventNotFound:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def get_decision_result(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        version: int,
    ) -> DecisionRunResult:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                run = connection.execute(
                    """
                    SELECT run.*
                    FROM decision_runs AS run
                    JOIN medical_events AS event ON event.id = run.medical_event_id
                    WHERE run.household_space_id = %s
                      AND run.medical_event_id = %s
                      AND run.event_version = %s
                      AND run.status = 'succeeded'
                      AND event.household_space_id = %s
                      AND event.deleted_at IS NULL
                    ORDER BY run.created_at DESC, run.id DESC
                    LIMIT 1
                    """,
                    (
                        scope.household_space_id,
                        event_id,
                        version,
                        scope.household_space_id,
                    ),
                ).fetchone()
                if run is None:
                    raise DecisionResultNotFound
                return self._load_result(connection, run)
        except DecisionResultNotFound:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def for_event_date(
        self,
        scope: HouseholdScope,
        family_member_id: UUID,
        event_date: date | None,
    ) -> tuple[PolicySnapshot, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                return self._policy_snapshots(connection, scope, family_member_id, event_date)
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def executable_for_rider(
        self,
        scope: HouseholdScope,
        rider_id: UUID,
    ) -> tuple[CoverageRuleVersion, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                return self._rule_versions(connection, scope, rider_id)
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def get_many(
        self,
        scope: HouseholdScope,
        evidence_ids: tuple[UUID, ...],
    ) -> tuple[EvidenceRef, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                return self._evidence_many(connection, scope, evidence_ids)
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def for_family_member(
        self,
        scope: HouseholdScope,
        family_member_id: UUID,
    ) -> tuple[ClaimHistoryFact, ...]:
        del scope, family_member_id
        return ()

    @staticmethod
    def _event_row(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event_id: UUID,
        *,
        deleted_only: bool = False,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        deleted = "IS NOT NULL" if deleted_only else "IS NULL"
        locking = "FOR UPDATE OF event" if for_update else ""
        return connection.execute(
            f"""
            SELECT event.*,
                   version.id AS structured_fact_version_id,
                   version.version AS structured_fact_version,
                   version.facts_json AS structured_facts_json,
                   version.questions_json AS structured_questions_json,
                   version.issue_codes_json AS structured_issues_json
            FROM medical_events AS event
            LEFT JOIN LATERAL (
              SELECT id, version, facts_json, questions_json, issue_codes_json
              FROM medical_event_fact_versions
              WHERE medical_event_id = event.id AND is_current = true
              ORDER BY version DESC, id DESC
              LIMIT 1
            ) AS version ON true
            WHERE event.id = %s AND event.household_space_id = %s
              AND event.deleted_at {deleted}
            {locking}
            """,
            (event_id, scope.household_space_id),
        ).fetchone()

    def _policy_snapshots(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        family_member_id: UUID,
        event_date: date | None,
    ) -> tuple[PolicySnapshot, ...]:
        rows = connection.execute(
            """
            WITH insured AS (
              SELECT party.policy_contract_id,
                     array_agg(DISTINCT party.evidence_id) AS party_evidence_ids
              FROM policy_parties AS party
              JOIN family_members AS member
                ON member.id = party.family_member_id
               AND member.household_space_id = %(scope)s
               AND member.deleted_at IS NULL
              WHERE party.household_space_id = %(scope)s
                AND party.family_member_id = %(member)s
                AND party.role IN ('primary_insured', 'additional_insured')
                AND party.deleted_at IS NULL
                AND (
                  %(event_date)s::date IS NULL OR party.effective_from IS NULL
                  OR party.effective_from <= %(event_date)s::date
                )
                AND (
                  %(event_date)s::date IS NULL OR party.effective_to IS NULL
                  OR party.effective_to >= %(event_date)s::date
                )
              GROUP BY party.policy_contract_id
            )
            SELECT
              policy.id AS policy_id, rider.id AS rider_id,
              policy.status AS current_policy_status,
              rider.status AS current_rider_status,
              policy.coverage_start_date AS contract_start,
              policy.coverage_end_date AS contract_end,
              rider.coverage_start_date AS rider_coverage_start,
              rider.coverage_end_date AS rider_coverage_end,
              rider.benefit_type AS rider_type,
              rider.display_name AS rider_label,
              rider.insured_amount, rider.currency, rider.renewable,
              rider.status_checked_at,
              insured.party_evidence_ids,
              ARRAY[
                policy.source_evidence_id, policy.status_evidence_id,
                rider.source_evidence_id, rider.status_evidence_id
              ] AS source_evidence_ids,
              policy_state.statuses AS policy_snapshot_statuses,
              policy_state.evidence_ids AS policy_snapshot_evidence_ids,
              policy_state.effective_at AS policy_snapshot_effective_at,
              rider_state.statuses AS rider_snapshot_statuses,
              rider_state.evidence_ids AS rider_snapshot_evidence_ids,
              rider_state.effective_at AS rider_snapshot_effective_at
            FROM insured
            JOIN policy_contracts AS policy
              ON policy.id = insured.policy_contract_id
             AND policy.household_space_id = %(scope)s
             AND policy.deleted_at IS NULL
            JOIN riders AS rider
              ON rider.policy_contract_id = policy.id
             AND rider.household_space_id = %(scope)s
             AND rider.deleted_at IS NULL
            LEFT JOIN LATERAL (
              SELECT array_agg(DISTINCT status ORDER BY status) AS statuses,
                     array_agg(DISTINCT evidence_id) AS evidence_ids,
                     max(effective_at) AS effective_at
              FROM policy_status_snapshots AS state
              WHERE state.household_space_id = %(scope)s
                AND state.policy_contract_id = policy.id
                AND state.deleted_at IS NULL
                AND %(event_date)s::date IS NOT NULL
                AND state.effective_at < (%(event_date)s::date + 1)
                AND state.effective_at = (
                  SELECT max(newest.effective_at)
                  FROM policy_status_snapshots AS newest
                  WHERE newest.household_space_id = %(scope)s
                    AND newest.policy_contract_id = policy.id
                    AND newest.deleted_at IS NULL
                    AND newest.effective_at < (%(event_date)s::date + 1)
                )
            ) AS policy_state ON true
            LEFT JOIN LATERAL (
              SELECT array_agg(DISTINCT status ORDER BY status) AS statuses,
                     array_agg(DISTINCT evidence_id) AS evidence_ids,
                     max(effective_at) AS effective_at
              FROM policy_status_snapshots AS state
              WHERE state.household_space_id = %(scope)s
                AND state.rider_id = rider.id
                AND state.deleted_at IS NULL
                AND %(event_date)s::date IS NOT NULL
                AND state.effective_at < (%(event_date)s::date + 1)
                AND state.effective_at = (
                  SELECT max(newest.effective_at)
                  FROM policy_status_snapshots AS newest
                  WHERE newest.household_space_id = %(scope)s
                    AND newest.rider_id = rider.id
                    AND newest.deleted_at IS NULL
                    AND newest.effective_at < (%(event_date)s::date + 1)
                )
            ) AS rider_state ON true
            ORDER BY rider.id
            """,
            {
                "scope": scope.household_space_id,
                "member": family_member_id,
                "event_date": event_date,
            },
        ).fetchall()
        return tuple(_policy_snapshot(row, event_date) for row in rows)

    def _rule_versions(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        rider_id: UUID,
    ) -> tuple[CoverageRuleVersion, ...]:
        rows = connection.execute(
            """
            SELECT
              version.id, version.coverage_rule_id, version.candidate_version_id,
              version.version_number, version.schema_version, version.rule_kind,
              version.required, version.input_field_paths, version.expression_json,
              version.result_reason_code, version.review_state, version.executable,
              version.generator_version, version.verifier_version,
              version.created_at, version.published_at,
              evidence.id AS evidence_id,
              evidence.document_version_id, evidence.extraction_id,
              evidence.content_sha256, evidence.physical_page,
              evidence.x0, evidence.y0, evidence.x1, evidence.y1,
              evidence.review_state AS evidence_review_state
            FROM coverage_rules AS rule
            JOIN coverage_rule_versions AS version
              ON version.coverage_rule_id = rule.id
             AND version.version_number = rule.version
            JOIN rider_clause_links AS link
              ON link.id = rule.rider_clause_link_id
             AND link.household_space_id = %(scope)s
             AND link.rider_id = %(rider)s
             AND link.deleted_at IS NULL
             AND link.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            JOIN riders AS rider
              ON rider.id = link.rider_id
             AND rider.household_space_id = %(scope)s
             AND rider.deleted_at IS NULL
            JOIN policy_contracts AS policy
              ON policy.id = rider.policy_contract_id
             AND policy.household_space_id = %(scope)s
             AND policy.deleted_at IS NULL
            LEFT JOIN coverage_rule_evidence AS linked
              ON linked.coverage_rule_version_id = version.id
            LEFT JOIN evidence ON evidence.id = linked.evidence_id
            WHERE rule.household_space_id = %(scope)s
              AND rule.current_status = 'published'
              AND rule.deleted_at IS NULL
              AND version.executable
              AND version.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
              AND version.published_at IS NOT NULL
              AND version.schema_version = 'coverage-rule-v1'
            ORDER BY version.rule_kind, rule.id, version.id,
                     evidence.physical_page NULLS LAST, evidence.id NULLS LAST
            """,
            {"scope": scope.household_space_id, "rider": rider_id},
        ).fetchall()
        grouped: dict[UUID, tuple[dict[str, Any], list[EvidenceRef]]] = {}
        for row in rows:
            version_id = cast(UUID, row["id"])
            grouped.setdefault(version_id, (row, []))
            evidence = _evidence(row)
            if evidence is not None:
                grouped[version_id][1].append(evidence)
        return tuple(_coverage_rule(row, evidence) for row, evidence in grouped.values())

    def _evidence_many(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        evidence_ids: tuple[UUID, ...],
    ) -> tuple[EvidenceRef, ...]:
        if not evidence_ids:
            return ()
        rows = connection.execute(
            """
            SELECT evidence.id AS evidence_id,
                   evidence.document_version_id, evidence.extraction_id,
                   evidence.content_sha256, evidence.physical_page,
                   evidence.x0, evidence.y0, evidence.x1, evidence.y1,
                   evidence.review_state AS evidence_review_state
            FROM evidence
            JOIN document_versions AS version
              ON version.id = evidence.document_version_id
             AND version.content_sha256 = evidence.content_sha256
            JOIN documents AS document
              ON document.id = version.document_id AND document.deleted_at IS NULL
            JOIN extractions AS extraction
              ON extraction.id = evidence.extraction_id
             AND extraction.document_version_id = evidence.document_version_id
             AND extraction.status = 'succeeded'
            JOIN extraction_pages AS page
              ON page.extraction_id = extraction.id
             AND page.page_number = evidence.physical_page
            WHERE evidence.household_space_id = %s
              AND evidence.id = ANY(%s)
              AND evidence.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
              AND evidence.physical_page BETWEEN 1 AND version.page_count
              AND (
                evidence.x0 IS NULL OR (
                  evidence.x0 >= 0 AND evidence.y0 >= 0
                  AND evidence.x1 <= page.width_points
                  AND evidence.y1 <= page.height_points
                )
              )
            """,
            (scope.household_space_id, list(evidence_ids)),
        ).fetchall()
        values = {item.evidence_id: item for row in rows if (item := _evidence(row)) is not None}
        return tuple(values[item] for item in evidence_ids if item in values)

    def _persist_result(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        result: DecisionRunResult,
    ) -> None:
        connection.execute(
            """
            INSERT INTO decision_runs (
              id, household_space_id, medical_event_id, engine_version,
              rule_set_version, event_version, policy_snapshot_at, status, stale
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'succeeded', %s)
            """,
            (
                result.run_id,
                scope.household_space_id,
                result.medical_event_id,
                result.engine_version,
                result.rule_set_version,
                result.event_version,
                result.policy_snapshot_at,
                result.stale,
            ),
        )
        for evaluation in result.evaluations:
            if evaluation.id is None:
                raise DecisionRepositoryUnavailable
            connection.execute(
                """
                INSERT INTO rule_evaluations (
                  id, decision_run_id, rider_id, coverage_rule_version_id,
                  result, required, reason_code, facts_json,
                  evidence_snapshot_json, missing_fields_json,
                  conflicting_fields_json, evaluator_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    evaluation.id,
                    result.run_id,
                    evaluation.rider_id,
                    evaluation.rule_version_id,
                    evaluation.result,
                    evaluation.required,
                    evaluation.reason_code,
                    Jsonb(_evaluation_facts(evaluation)),
                    Jsonb(_evidence_snapshot(evaluation.evidence)),
                    Jsonb(list(evaluation.missing_fields)),
                    Jsonb(list(evaluation.conflicting_fields)),
                    evaluation.evaluator_version,
                ),
            )
            for evidence_id in dict.fromkeys(evaluation.evidence_ids):
                connection.execute(
                    """
                    INSERT INTO rule_evaluation_evidence (rule_evaluation_id, evidence_id)
                    VALUES (%s, %s)
                    """,
                    (evaluation.id, evidence_id),
                )
        for candidate in result.candidates:
            if candidate.id is None or candidate.rider_type not in {"fixed", "indemnity"}:
                raise DecisionRepositoryUnavailable
            connection.execute(
                """
                INSERT INTO claim_candidates (
                  id, decision_run_id, rider_id, rider_type,
                  rider_label_snapshot, aggregate_result,
                  required_match_count, required_unknown_count,
                  required_no_match_count, questions_json, hold_reason_codes_json,
                  version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    candidate.id,
                    result.run_id,
                    candidate.rider_id,
                    candidate.rider_type,
                    candidate.rider_label,
                    candidate.aggregate_result,
                    candidate.required_match_count,
                    candidate.required_unknown_count,
                    candidate.required_no_match_count,
                    Jsonb(
                        [
                            {"field_path": item.field_path, "reason_code": item.reason_code}
                            for item in candidate.questions
                        ]
                    ),
                    Jsonb(list(candidate.hold_reason_codes)),
                    candidate.version,
                ),
            )

    def _load_result(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        run: dict[str, Any],
    ) -> DecisionRunResult:
        evaluation_rows = connection.execute(
            """
            SELECT evaluation.*
            FROM rule_evaluations AS evaluation
            WHERE evaluation.decision_run_id = %s
            ORDER BY evaluation.rider_id, evaluation.coverage_rule_version_id,
                     evaluation.id
            """,
            (run["id"],),
        ).fetchall()
        evaluations = tuple(_rule_evaluation(row) for row in evaluation_rows)
        candidate_rows = connection.execute(
            """
            SELECT candidate.*,
                   COALESCE(candidate.rider_label_snapshot, rider.display_name)
                     AS rider_label
            FROM claim_candidates AS candidate
            LEFT JOIN riders AS rider ON rider.id = candidate.rider_id
            WHERE candidate.decision_run_id = %s
            ORDER BY candidate.rider_id, candidate.id
            """,
            (run["id"],),
        ).fetchall()
        by_rider: dict[UUID, list[RuleEvaluation]] = {}
        for evaluation in evaluations:
            by_rider.setdefault(evaluation.rider_id, []).append(evaluation)
        candidates = tuple(
            _claim_candidate(row, tuple(by_rider.get(cast(UUID, row["rider_id"]), ())))
            for row in candidate_rows
        )
        return DecisionRunResult(
            run_id=cast(UUID, run["id"]),
            medical_event_id=cast(UUID, run["medical_event_id"]),
            event_version=int(run["event_version"]),
            engine_version=cast(str, run["engine_version"]),
            rule_set_version=cast(str, run["rule_set_version"]),
            policy_snapshot_at=cast(datetime, run["policy_snapshot_at"]),
            candidates=candidates,
            evaluations=evaluations,
            stale=bool(run["stale"]),
        )


class _ConnectionReaders:
    def __init__(
        self,
        repository: DecisionRepository,
        connection: psycopg.Connection[dict[str, Any]],
    ) -> None:
        self.repository = repository
        self.connection = connection
        self.policy = self
        self.rules = self
        self.evidence = self
        self.history = self

    def for_event_date(
        self,
        scope: HouseholdScope,
        family_member_id: UUID,
        event_date: date | None,
    ) -> tuple[PolicySnapshot, ...]:
        return self.repository._policy_snapshots(
            self.connection, scope, family_member_id, event_date
        )

    def executable_for_rider(
        self, scope: HouseholdScope, rider_id: UUID
    ) -> tuple[CoverageRuleVersion, ...]:
        return self.repository._rule_versions(self.connection, scope, rider_id)

    def get_many(
        self, scope: HouseholdScope, evidence_ids: tuple[UUID, ...]
    ) -> tuple[EvidenceRef, ...]:
        return self.repository._evidence_many(self.connection, scope, evidence_ids)

    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]:
        del scope, family_member_id
        return ()


def _medical_event(row: Mapping[str, Any]) -> MedicalEvent:
    values = row.get("facts_json")
    confirmations = row.get("confirmation_json")
    if not isinstance(values, Mapping) or not isinstance(confirmations, Mapping):
        raise DecisionRepositoryUnavailable
    try:
        facts = normalize_facts(
            cast(Mapping[str, object], values),
            confirmations=cast(Mapping[str, FactConfirmation], confirmations),
        )
    except FactNormalizationError:
        raise DecisionRepositoryUnavailable from None
    structured_facts = _structured_fact_records(row.get("structured_facts_json"))
    optional_questions = _structured_question_records(row.get("structured_questions_json"))
    return MedicalEvent(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        family_member_id=cast(UUID, row["family_member_id"]),
        mode=cast(Any, row["mode"]),
        situation=cast(str, row.get("situation_text", "")),
        event_date=cast(date | None, row.get("event_date")),
        visit_date=cast(date | None, row.get("visit_date")),
        facts=facts,
        structured_facts=tuple(
            {
                "fact_id": item.fact_id,
                "field_id": item.field_id,
                "value": item.value,
                "source": item.source,
                "state": item.state,
                "confidence": item.confidence,
                "evidence_ids": item.evidence_ids,
            }
            for item in structured_facts
        ),
        optional_questions=tuple(
            {"question_code": item.question_code, "field_id": item.field_id}
            for item in optional_questions
        ),
        confirmation=cast(Mapping[str, FactConfirmation], confirmations),
        version=int(row["version"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        deleted_at=cast(datetime | None, row.get("deleted_at")),
    )


def _event_json(
    facts: Mapping[str, FactValue],
) -> tuple[dict[str, object | None], dict[str, FactConfirmation]]:
    return (
        {key: _json_value(value.value) for key, value in facts.items()},
        {key: value.confirmation for key, value in facts.items()},
    )


def _structured_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DecisionInvalid
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DecisionInvalid from None


def _json_value(value: object | None) -> object | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _policy_snapshot(row: Mapping[str, Any], event_date: date | None) -> PolicySnapshot:
    policy_statuses = _strings(row.get("policy_snapshot_statuses"))
    rider_statuses = _strings(row.get("rider_snapshot_statuses"))
    policy_status = _snapshot_status(
        policy_statuses,
        cast(str, row["current_policy_status"]),
        event_date,
    )
    rider_status = _snapshot_status(
        rider_statuses,
        cast(str, row["current_rider_status"]),
        event_date,
    )
    evidence_ids = _uuid_values(
        row.get("party_evidence_ids"),
        row.get("source_evidence_ids"),
        row.get("policy_snapshot_evidence_ids"),
        row.get("rider_snapshot_evidence_ids"),
    )
    return PolicySnapshot(
        policy_id=cast(UUID, row["policy_id"]),
        rider_id=cast(UUID, row["rider_id"]),
        effective_status=policy_status,
        evidence_ids=evidence_ids,
        rider_type=cast(str, row.get("rider_type")),
        rider_label=cast(str | None, row.get("rider_label")),
        contract_start=cast(date | None, row.get("contract_start")),
        contract_end=cast(date | None, row.get("contract_end")),
        rider_coverage_start=cast(date | None, row.get("rider_coverage_start")),
        rider_coverage_end=cast(date | None, row.get("rider_coverage_end")),
        rider_status=rider_status,
        insured_amount=cast(Decimal | None, row.get("insured_amount")),
        currency=cast(str | None, row.get("currency")),
        renewable=cast(bool | None, row.get("renewable")),
        status_checked_at=cast(
            datetime | None,
            row.get("rider_snapshot_effective_at") or row.get("status_checked_at"),
        ),
    )


def _snapshot_status(statuses: tuple[str, ...], current: str, event_date: date | None) -> str:
    if event_date is None:
        return current
    if len(statuses) == 1:
        return statuses[0]
    if len(statuses) > 1:
        return "conflicting"
    return "unknown"


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _uuid_values(*groups: object) -> tuple[UUID, ...]:
    values: list[UUID] = []
    for group in groups:
        if not isinstance(group, Sequence) or isinstance(group, str | bytes | bytearray):
            continue
        for value in group:
            resolved: UUID | None = None
            if isinstance(value, UUID):
                resolved = value
            elif isinstance(value, str):
                try:
                    resolved = UUID(value)
                except ValueError:
                    continue
            if resolved is not None and resolved.int != 0 and resolved not in values:
                values.append(resolved)
    return tuple(values)


def _evidence(row: Mapping[str, Any]) -> EvidenceRef | None:
    evidence_id = row.get("evidence_id")
    if not isinstance(evidence_id, UUID):
        return None
    coordinates = (row.get("x0"), row.get("y0"), row.get("x1"), row.get("y1"))
    bbox = None if coordinates == (None, None, None, None) else cast(Any, coordinates)
    return EvidenceRef(
        evidence_id=evidence_id,
        document_version_id=cast(UUID, row["document_version_id"]),
        extraction_id=cast(UUID, row["extraction_id"]),
        content_sha256=cast(str, row["content_sha256"]),
        physical_page=int(row["physical_page"]),
        bbox=bbox,
        review_state=cast(Any, row["evidence_review_state"]),
    )


def _coverage_rule(row: Mapping[str, Any], evidence: Sequence[EvidenceRef]) -> CoverageRuleVersion:
    fields = row.get("input_field_paths")
    document = row.get("expression_json")
    if not isinstance(fields, list) or not isinstance(document, dict) or not evidence:
        raise DecisionRepositoryUnavailable
    return CoverageRuleVersion(
        id=cast(UUID, row["id"]),
        coverage_rule_id=cast(UUID, row["coverage_rule_id"]),
        candidate_version_id=cast(UUID, row["candidate_version_id"]),
        version_number=int(row["version_number"]),
        schema_version=cast(str, row["schema_version"]),
        rule_kind=cast(Any, row["rule_kind"]),
        required=bool(row["required"]),
        input_field_paths=tuple(cast(list[str], fields)),
        rule_document=cast(dict[str, object], document),
        result_reason_code=cast(str, row["result_reason_code"]),
        review_state=cast(Any, row["review_state"]),
        executable=bool(row["executable"]),
        generator_version=cast(str, row["generator_version"]),
        verifier_version=cast(str, row["verifier_version"]),
        created_at=cast(datetime, row["created_at"]),
        published_at=cast(datetime | None, row.get("published_at")),
        evidence=tuple(evidence),
    )


def _evaluation_facts(evaluation: RuleEvaluation) -> dict[str, object]:
    return {
        key: {
            "value": _json_value(value.value),
            "confirmation": value.confirmation,
            "evidence_ids": [str(item) for item in value.evidence_ids],
        }
        for key, value in evaluation.facts.items()
    }


def _evidence_snapshot(evidence: Sequence[EvidenceRef]) -> list[dict[str, object]]:
    return [
        {
            "evidence_id": str(item.evidence_id),
            "document_version_id": str(item.document_version_id),
            "extraction_id": str(item.extraction_id),
            "content_sha256": item.content_sha256,
            "physical_page": item.physical_page,
            "bbox": None if item.bbox is None else [str(value) for value in item.bbox],
            "review_state": item.review_state,
        }
        for item in evidence
    ]


def _evidence_snapshot_values(value: object) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, list):
        raise DecisionRepositoryUnavailable
    evidence: list[EvidenceRef] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise DecisionRepositoryUnavailable
        bbox_raw = raw.get("bbox")
        bbox = None
        if bbox_raw is not None:
            if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
                raise DecisionRepositoryUnavailable
            try:
                bbox = cast(Any, tuple(Decimal(str(item)) for item in bbox_raw))
            except InvalidOperation, TypeError, ValueError:
                raise DecisionRepositoryUnavailable from None
        physical_page = raw.get("physical_page")
        if not isinstance(physical_page, int) or isinstance(physical_page, bool):
            raise DecisionRepositoryUnavailable
        try:
            evidence.append(
                EvidenceRef(
                    evidence_id=UUID(str(raw.get("evidence_id"))),
                    document_version_id=UUID(str(raw.get("document_version_id"))),
                    extraction_id=UUID(str(raw.get("extraction_id"))),
                    content_sha256=cast(str, raw.get("content_sha256")),
                    physical_page=physical_page,
                    bbox=bbox,
                    review_state=cast(Any, raw.get("review_state")),
                )
            )
        except EvidenceInvalid, InvalidOperation, TypeError, ValueError:
            raise DecisionRepositoryUnavailable from None
    return tuple(evidence)


def _rule_evaluation(row: Mapping[str, Any]) -> RuleEvaluation:
    facts_json = row.get("facts_json")
    facts: dict[str, FactValue] = {}
    if isinstance(facts_json, Mapping):
        for field, raw in facts_json.items():
            if not isinstance(field, str) or not isinstance(raw, Mapping):
                continue
            confirmation = raw.get("confirmation")
            if confirmation not in {"user", "ai_structured", "unconfirmed", "conflicting"}:
                continue
            facts[field] = FactValue(
                value=raw.get("value"),
                confirmation=cast(FactConfirmation, confirmation),
                evidence_ids=_uuid_values(raw.get("evidence_ids")),
            )
    snapshot_evidence = _evidence_snapshot_values(row.get("evidence_snapshot_json"))
    missing = _strings(row.get("missing_fields_json"))
    conflicting = _strings(row.get("conflicting_fields_json"))
    return RuleEvaluation(
        id=cast(UUID, row["id"]),
        rider_id=cast(UUID, row["rider_id"]),
        rule_version_id=cast(UUID, row["coverage_rule_version_id"]),
        result=cast(Any, row["result"]),
        required=bool(row["required"]),
        reason_code=cast(str, row["reason_code"]),
        facts=facts,
        fact_paths=tuple(dict.fromkeys((*facts, *missing, *conflicting))),
        missing_fields=missing,
        conflicting_fields=conflicting,
        evidence_ids=tuple(item.evidence_id for item in snapshot_evidence),
        evidence=snapshot_evidence,
        evaluator_version=cast(str, row["evaluator_version"]),
    )


def _claim_candidate(
    row: Mapping[str, Any], evaluations: tuple[RuleEvaluation, ...]
) -> ClaimCandidate:
    raw_questions = row.get("questions_json")
    questions: list[Question] = []
    if isinstance(raw_questions, list):
        for raw in raw_questions:
            if isinstance(raw, Mapping):
                field = raw.get("field_path")
                reason = raw.get("reason_code")
                if isinstance(field, str) and isinstance(reason, str):
                    questions.append(Question(field, reason))
    return ClaimCandidate(
        id=cast(UUID, row["id"]),
        decision_run_id=cast(UUID, row["decision_run_id"]),
        rider_id=cast(UUID, row["rider_id"]),
        rider_type=cast(str, row["rider_type"]),
        rider_label=cast(str | None, row.get("rider_label")),
        aggregate_result=cast(Any, row["aggregate_result"]),
        evaluations=evaluations,
        questions=tuple(questions),
        hold_reason_codes=_strings(row.get("hold_reason_codes_json")),
        required_match_count=int(row["required_match_count"]),
        required_unknown_count=int(row["required_unknown_count"]),
        required_no_match_count=int(row["required_no_match_count"]),
        version=int(row["version"]),
    )


__all__ = ["DecisionRepository"]

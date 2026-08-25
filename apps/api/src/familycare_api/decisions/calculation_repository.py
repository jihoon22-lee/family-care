"""PostgreSQL persistence for manual receipt lines and immutable calculations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.calculation_validation import decimal_from_wire, validate_receipt_line
from familycare_api.decisions.calculations import (
    BenefitCalculationResult,
    Money,
    ReceiptLine,
    calculate_fixed_benefit,
    calculate_indemnity,
)
from familycare_api.decisions.domain import ClaimCandidate, FactContext, FactValue
from familycare_api.decisions.errors import (
    DecisionRepositoryUnavailable,
    DecisionResultNotFound,
    MedicalEventNotFound,
    ReceiptLineNotFound,
)
from familycare_api.policies.errors import VersionConflict

CALCULATION_ENGINE_VERSION = "benefit-calculation-v1"


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise DecisionRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class CalculationRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def create_receipt_line(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        line: ReceiptLine,
    ) -> ReceiptLine:
        validate_receipt_line(line)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO receipt_lines (
                      id, household_space_id, medical_event_id, category,
                      coverage_category, amount, currency, confirmation_level,
                      note_code, version
                    )
                    SELECT %s, event.household_space_id, event.id, %s, %s, %s, %s, %s, %s, 1
                    FROM medical_events AS event
                    WHERE event.id = %s
                      AND event.household_space_id = %s
                      AND event.deleted_at IS NULL
                    RETURNING *
                    """,
                    (
                        line.line_id,
                        line.category,
                        line.coverage_category,
                        line.amount.amount,
                        line.amount.currency,
                        line.confirmation_level,
                        line.note_code,
                        event_id,
                        scope.household_space_id,
                    ),
                ).fetchone()
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise MedicalEventNotFound
        return _receipt_line(row)

    def list_receipt_lines(
        self,
        scope: HouseholdScope,
        event_id: UUID,
    ) -> tuple[ReceiptLine, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                event = connection.execute(
                    """
                    SELECT id FROM medical_events
                    WHERE id = %s AND household_space_id = %s
                      AND deleted_at IS NULL
                    """,
                    (event_id, scope.household_space_id),
                ).fetchone()
                if event is None:
                    raise MedicalEventNotFound
                rows = connection.execute(
                    """
                    SELECT * FROM receipt_lines
                    WHERE household_space_id = %s AND medical_event_id = %s
                      AND deleted_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (scope.household_space_id, event_id),
                ).fetchall()
        except MedicalEventNotFound:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        return tuple(_receipt_line(row) for row in rows)

    def update_receipt_line(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        line_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> ReceiptLine:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._receipt_row(
                    connection,
                    scope,
                    event_id,
                    line_id,
                    for_update=True,
                )
                if current is None:
                    raise ReceiptLineNotFound
                if int(current["version"]) != expected_version:
                    raise VersionConflict
                values = _updated_receipt(current, changes)
                validate_receipt_line(values)
                row = connection.execute(
                    """
                    UPDATE receipt_lines
                    SET category = %s, coverage_category = %s, amount = %s,
                        currency = %s, confirmation_level = %s, note_code = %s,
                        version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s AND medical_event_id = %s
                      AND household_space_id = %s AND version = %s
                      AND deleted_at IS NULL
                    RETURNING *
                    """,
                    (
                        values.category,
                        values.coverage_category,
                        values.amount.amount,
                        values.amount.currency,
                        values.confirmation_level,
                        values.note_code,
                        line_id,
                        event_id,
                        scope.household_space_id,
                        expected_version,
                    ),
                ).fetchone()
        except ReceiptLineNotFound, VersionConflict:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        return _receipt_line(row)

    def soft_delete_receipt_line(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        line_id: UUID,
        *,
        expected_version: int,
    ) -> None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._receipt_row(
                    connection,
                    scope,
                    event_id,
                    line_id,
                    for_update=True,
                )
                if current is None:
                    raise ReceiptLineNotFound
                if int(current["version"]) != expected_version:
                    raise VersionConflict
                row = connection.execute(
                    """
                    UPDATE receipt_lines
                    SET deleted_at = clock_timestamp(), version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND medical_event_id = %s
                      AND household_space_id = %s AND version = %s
                      AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (line_id, event_id, scope.household_space_id, expected_version),
                ).fetchone()
        except ReceiptLineNotFound, VersionConflict:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise VersionConflict

    def calculate_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
    ) -> tuple[dict[str, object], ...]:
        """Calculate from one immutable decision run and persist an atomic trace."""
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                event = connection.execute(
                    """
                    SELECT event.id, event.version AS event_version,
                           run.id AS decision_run_id, run.engine_version AS decision_engine_version,
                           run.created_at AS run_created_at
                    FROM medical_events AS event
                    LEFT JOIN LATERAL (
                      SELECT decision.*
                      FROM decision_runs AS decision
                      WHERE decision.medical_event_id = event.id
                        AND decision.household_space_id = event.household_space_id
                        AND decision.event_version = event.version
                        AND decision.status = 'succeeded'
                      ORDER BY decision.created_at DESC, decision.id DESC
                      LIMIT 1
                    ) AS run ON true
                    WHERE event.id = %s AND event.household_space_id = %s
                      AND event.deleted_at IS NULL
                    """,
                    (event_id, scope.household_space_id),
                ).fetchone()
                if event is None:
                    raise MedicalEventNotFound
                if event.get("decision_run_id") is None:
                    raise DecisionResultNotFound
                candidates = connection.execute(
                    """
                    SELECT candidate.*, rider.insured_amount, rider.currency,
                           rider.updated_at AS rider_updated_at
                    FROM claim_candidates AS candidate
                    JOIN riders AS rider
                      ON rider.id = candidate.rider_id
                     AND rider.household_space_id = %s
                     AND rider.deleted_at IS NULL
                    WHERE candidate.decision_run_id = %s
                    ORDER BY candidate.rider_id, candidate.id
                    """,
                    (scope.household_space_id, event["decision_run_id"]),
                ).fetchall()
                receipt_rows = connection.execute(
                    """
                    SELECT * FROM receipt_lines
                    WHERE household_space_id = %s AND medical_event_id = %s
                      AND deleted_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (scope.household_space_id, event_id),
                ).fetchall()
                lines = tuple(_receipt_line(row) for row in receipt_rows)
                indemnity_count = sum(
                    row["rider_type"] == "indemnity" and row["aggregate_result"] != "NO_MATCH"
                    for row in candidates
                )
                calculated = (
                    self._calculate_candidate(
                        connection,
                        scope,
                        event,
                        row,
                        lines,
                        indemnity_count=indemnity_count,
                        receipt_rows=receipt_rows,
                    )
                    for row in candidates
                )
                values = tuple(value for value in calculated if value is not None)
        except MedicalEventNotFound, DecisionResultNotFound:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        return values

    def _calculate_candidate(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: Mapping[str, Any],
        row: Mapping[str, Any],
        lines: tuple[ReceiptLine, ...],
        *,
        indemnity_count: int,
        receipt_rows: list[dict[str, Any]],
    ) -> dict[str, object] | None:
        candidate = _claim_candidate(row)
        if candidate.id is None:
            raise DecisionRepositoryUnavailable
        locked = connection.execute(
            """
            SELECT candidate.id
            FROM claim_candidates AS candidate
            JOIN decision_runs AS run
              ON run.id = candidate.decision_run_id
             AND run.household_space_id = %s
            WHERE candidate.id = %s
            FOR UPDATE OF candidate
            """,
            (scope.household_space_id, candidate.id),
        ).fetchone()
        if locked is None:
            raise DecisionRepositoryUnavailable
        rules = self._calculation_rules(
            connection,
            scope,
            candidate.rider_id,
            cast(datetime, event["run_created_at"]),
        )
        if len(rules) != 1:
            return None
        rule = rules[0]
        run_created_at = cast(datetime, event["run_created_at"])
        rider_updated_at = cast(datetime, row["rider_updated_at"])
        receipt_updated_at = max(
            (cast(datetime, item["updated_at"]) for item in receipt_rows),
            default=run_created_at,
        )
        cutoff = max(
            run_created_at,
            rider_updated_at,
            receipt_updated_at if candidate.rider_type == "indemnity" else run_created_at,
        )
        existing = connection.execute(
            """
            SELECT * FROM benefit_calculations
            WHERE household_space_id = %s AND claim_candidate_id = %s
              AND rule_version_id = %s AND engine_version = %s
              AND created_at >= %s
            ORDER BY version DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (
                scope.household_space_id,
                candidate.id,
                rule.id,
                CALCULATION_ENGINE_VERSION,
                cutoff,
            ),
        ).fetchone()
        if existing is not None:
            return self._calculation_value(connection, existing)

        facts = FactContext(
            medical_event={},
            policy={},
            rider={
                "insured_amount": FactValue(row.get("insured_amount"), "ai_structured", ()),
                "currency": FactValue(row.get("currency"), "ai_structured", ()),
            },
            claim_history={},
        )
        if rider_updated_at > run_created_at:
            result = BenefitCalculationResult.unknown(
                cast(Any, candidate.rider_type or "fixed"), "STALE_POLICY_INPUT"
            )
        elif candidate.rider_type == "fixed":
            result = calculate_fixed_benefit(candidate, rule, facts)
        elif indemnity_count > 1:
            result = BenefitCalculationResult.unknown(
                "indemnity", "MULTIPLE_INDEMNITY_ALLOCATION_UNKNOWN"
            )
        else:
            result = calculate_indemnity(candidate, lines, rule, facts)
        persisted = self._persist_calculation(
            connection,
            scope,
            candidate,
            rule,
            result,
        )
        return self._calculation_value(connection, persisted)

    def _persist_calculation(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        candidate: ClaimCandidate,
        rule: CoverageRuleVersion,
        result: BenefitCalculationResult,
    ) -> dict[str, Any]:
        if candidate.id is None:
            raise DecisionRepositoryUnavailable
        calculation_id = uuid4()
        version_row = connection.execute(
            """
            SELECT COALESCE(max(version), 0) + 1 AS next_version
            FROM benefit_calculations
            WHERE household_space_id = %s AND claim_candidate_id = %s
            """,
            (scope.household_space_id, candidate.id),
        ).fetchone()
        version = int(version_row["next_version"]) if version_row else 1
        currency = _result_currency(result)
        rounding_rule = next(
            (
                item.rounding_rule
                for item in reversed(result.steps)
                if item.rounding_rule is not None
            ),
            None,
        )
        row = connection.execute(
            """
            INSERT INTO benefit_calculations (
              id, household_space_id, claim_candidate_id, calculation_kind,
              status, currency, confirmed_amount, additional_amount,
              excluded_amount, deductible_amount, applied_rate, applied_limit,
              rounding_rule, hold_reason_code, excluded_reason_codes, rule_version_id,
              engine_version, version
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            RETURNING *
            """,
            (
                calculation_id,
                scope.household_space_id,
                candidate.id,
                result.kind,
                result.status,
                currency,
                _amount(result.confirmed),
                _amount(result.additional),
                _amount(result.excluded),
                _amount(result.deductible),
                result.applied_rate,
                _amount(result.applied_limit),
                rounding_rule,
                result.hold_reason_codes[0] if result.hold_reason_codes else None,
                list(result.excluded_reason_codes),
                rule.id,
                CALCULATION_ENGINE_VERSION,
                version,
            ),
        ).fetchone()
        if row is None:
            raise DecisionRepositoryUnavailable
        for step in result.steps:
            connection.execute(
                """
                INSERT INTO benefit_calculation_steps (
                  benefit_calculation_id, step_number, operation,
                  input_amount, input_currency, output_amount, output_currency,
                  rounding_rule, reason_code
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    calculation_id,
                    step.step_number,
                    step.operation,
                    _amount(step.input_amount),
                    _currency(step.input_amount),
                    _amount(step.output_amount),
                    _currency(step.output_amount),
                    step.rounding_rule,
                    step.reason_code,
                ),
            )
        return row

    @staticmethod
    def _calculation_rules(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        rider_id: UUID,
        cutoff: datetime,
    ) -> tuple[CoverageRuleVersion, ...]:
        rows = connection.execute(
            """
            SELECT version.id, version.coverage_rule_id, version.candidate_version_id,
                   version.version_number, version.schema_version, version.rule_kind,
                   version.required, version.input_field_paths, version.expression_json,
                   version.result_reason_code, version.review_state, version.executable,
                   version.generator_version, version.verifier_version,
                   version.created_at, version.published_at,
                   evidence.id AS evidence_id, evidence.document_version_id,
                   evidence.extraction_id, evidence.content_sha256,
                   evidence.physical_page, evidence.x0, evidence.y0,
                   evidence.x1, evidence.y1,
                   evidence.review_state AS evidence_review_state,
                   count(*) OVER (PARTITION BY version.id) AS valid_evidence_count,
                   (
                     SELECT count(*)
                     FROM coverage_rule_evidence AS required_evidence
                     WHERE required_evidence.coverage_rule_version_id = version.id
                   ) AS linked_evidence_count
            FROM coverage_rules AS rule
            JOIN rider_clause_links AS link
              ON link.id = rule.rider_clause_link_id
             AND link.household_space_id = %(scope)s
             AND link.rider_id = %(rider)s
             AND link.deleted_at IS NULL
             AND link.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            JOIN LATERAL (
              SELECT candidate.*
              FROM coverage_rule_versions AS candidate
              WHERE candidate.coverage_rule_id = rule.id
                AND candidate.executable
                AND candidate.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
                AND candidate.published_at IS NOT NULL
                AND candidate.published_at <= %(cutoff)s
                AND candidate.expression_json ? 'calculation'
              ORDER BY candidate.version_number DESC, candidate.id DESC
              LIMIT 1
            ) AS version ON true
            JOIN coverage_rule_evidence AS linked
              ON linked.coverage_rule_version_id = version.id
            JOIN evidence
              ON evidence.id = linked.evidence_id
             AND evidence.household_space_id = %(scope)s
             AND evidence.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            JOIN document_versions AS document_version
              ON document_version.id = evidence.document_version_id
             AND document_version.content_sha256 = evidence.content_sha256
            JOIN documents AS document
              ON document.id = document_version.document_id
             AND document.deleted_at IS NULL
            JOIN extractions AS extraction
              ON extraction.id = evidence.extraction_id
             AND extraction.document_version_id = evidence.document_version_id
             AND extraction.status = 'succeeded'
            JOIN extraction_pages AS page
              ON page.extraction_id = extraction.id
             AND page.page_number = evidence.physical_page
            WHERE rule.household_space_id = %(scope)s
              AND rule.deleted_at IS NULL
              AND evidence.physical_page BETWEEN 1 AND document_version.page_count
              AND (
                evidence.x0 IS NULL OR (
                  evidence.x0 >= 0 AND evidence.y0 >= 0
                  AND evidence.x1 <= page.width_points
                  AND evidence.y1 <= page.height_points
                )
              )
            ORDER BY rule.id, version.id, evidence.physical_page NULLS LAST,
                     evidence.id NULLS LAST
            """,
            {"scope": scope.household_space_id, "rider": rider_id, "cutoff": cutoff},
        ).fetchall()
        grouped: dict[UUID, tuple[dict[str, Any], list[EvidenceRef]]] = {}
        for row in rows:
            if int(row["valid_evidence_count"]) != int(row["linked_evidence_count"]):
                continue
            version_id = cast(UUID, row["id"])
            grouped.setdefault(version_id, (row, []))
            evidence = _evidence(row)
            if evidence is not None:
                grouped[version_id][1].append(evidence)
        return tuple(_coverage_rule(row, evidence) for row, evidence in grouped.values())

    @staticmethod
    def _receipt_row(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event_id: UUID,
        line_id: UUID,
        *,
        for_update: bool,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE OF line" if for_update else ""
        return connection.execute(
            f"""
            SELECT line.*
            FROM receipt_lines AS line
            JOIN medical_events AS event
              ON event.id = line.medical_event_id
             AND event.household_space_id = %(scope)s
             AND event.deleted_at IS NULL
            WHERE line.id = %(line)s AND line.medical_event_id = %(event)s
              AND line.household_space_id = %(scope)s AND line.deleted_at IS NULL
            {suffix}
            """,
            {"scope": scope.household_space_id, "event": event_id, "line": line_id},
        ).fetchone()

    @staticmethod
    def _calculation_value(
        connection: psycopg.Connection[dict[str, Any]],
        row: Mapping[str, Any],
    ) -> dict[str, object]:
        step_rows = connection.execute(
            """
            SELECT * FROM benefit_calculation_steps
            WHERE benefit_calculation_id = %s
            ORDER BY step_number
            """,
            (row["id"],),
        ).fetchall()
        currency = cast(str | None, row.get("currency"))

        def money(name: str) -> dict[str, object] | None:
            amount = row.get(name)
            if amount is None or currency is None:
                return None
            return {"amount": cast(Decimal, amount), "currency": currency}

        steps = tuple(_step_value(value) for value in step_rows)
        evidence_rows = connection.execute(
            """
            SELECT evidence_id FROM coverage_rule_evidence
            WHERE coverage_rule_version_id = %s
            ORDER BY evidence_id
            """,
            (row["rule_version_id"],),
        ).fetchall()
        hold = row.get("hold_reason_code")
        return {
            "schema_version": "1",
            "kind": row["calculation_kind"],
            "status": row["status"],
            "calculation_id": row["id"],
            "claim_candidate_id": row["claim_candidate_id"],
            "rule_version_id": row["rule_version_id"],
            "currency": currency,
            "confirmed": money("confirmed_amount"),
            "additional": money("additional_amount"),
            "excluded": money("excluded_amount"),
            "deductible": money("deductible_amount"),
            "applied_rate": row.get("applied_rate"),
            "applied_limit": money("applied_limit"),
            "rounding_rule": row.get("rounding_rule"),
            "engine_version": row["engine_version"],
            "version": row["version"],
            "created_at": row["created_at"],
            "steps": steps,
            "hold_reason_codes": (hold,) if isinstance(hold, str) and hold else (),
            "excluded_reason_codes": tuple(row.get("excluded_reason_codes") or ()),
            "evidence_ids": tuple(value["evidence_id"] for value in evidence_rows),
        }


def _receipt_line(row: Mapping[str, Any]) -> ReceiptLine:
    return ReceiptLine(
        line_id=cast(UUID, row["id"]),
        category=cast(Any, row["category"]),
        coverage_category=cast(Any, row["coverage_category"]),
        amount=Money(cast(Decimal, row["amount"]), cast(str, row["currency"])),
        confirmation_level=cast(Any, row["confirmation_level"]),
        note_code=cast(str | None, row.get("note_code")),
        version=int(row["version"]),
    )


def _updated_receipt(row: Mapping[str, Any], changes: Mapping[str, object]) -> ReceiptLine:
    amount = changes.get("amount", row["amount"])
    parsed_amount = decimal_from_wire(amount) if isinstance(amount, str) else cast(Decimal, amount)
    return ReceiptLine(
        line_id=cast(UUID, row["id"]),
        category=cast(Any, changes.get("category", row["category"])),
        coverage_category=cast(
            Any,
            changes.get("coverage_category", row["coverage_category"]),
        ),
        amount=Money(parsed_amount, cast(str, changes.get("currency", row["currency"]))),
        confirmation_level=cast(
            Any,
            changes.get("confirmation_level", row["confirmation_level"]),
        ),
        note_code=cast(str | None, changes.get("note_code", row.get("note_code"))),
        version=int(row["version"]),
    )


def _step_value(row: Mapping[str, Any]) -> dict[str, object]:
    def money(prefix: str) -> dict[str, object] | None:
        amount = row.get(f"{prefix}_amount")
        currency = row.get(f"{prefix}_currency")
        if amount is None or currency is None:
            return None
        return {"amount": cast(Decimal, amount), "currency": cast(str, currency)}

    return {
        "step_number": row["step_number"],
        "operation": row["operation"],
        "input_amount": money("input"),
        "output_amount": money("output"),
        "rounding_rule": row.get("rounding_rule"),
        "reason_code": row["reason_code"],
    }


def _claim_candidate(row: Mapping[str, Any]) -> ClaimCandidate:
    return ClaimCandidate(
        id=cast(UUID, row["id"]),
        decision_run_id=cast(UUID, row["decision_run_id"]),
        rider_id=cast(UUID, row["rider_id"]),
        rider_type=cast(str, row["rider_type"]),
        aggregate_result=cast(Any, row["aggregate_result"]),
        required_match_count=int(row["required_match_count"]),
        required_unknown_count=int(row["required_unknown_count"]),
        required_no_match_count=int(row["required_no_match_count"]),
        version=int(row["version"]),
    )


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


def _coverage_rule(
    row: Mapping[str, Any],
    evidence: list[EvidenceRef],
) -> CoverageRuleVersion:
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


def _result_currency(result: BenefitCalculationResult) -> str | None:
    for value in (
        result.confirmed,
        result.additional,
        result.excluded,
        result.deductible,
        result.applied_limit,
    ):
        if value is not None:
            return value.currency
    return None


def _amount(value: Money | None) -> Decimal | None:
    return value.amount if value is not None else None


def _currency(value: Money | None) -> str | None:
    return value.currency if value is not None else None


__all__ = ["CalculationRepository"]

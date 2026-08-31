"""Scoped PostgreSQL adapter for deterministic private-knowledge decisions.

The adapter reads one immutable current publication for one household member.
It never calls a model and never treats a terms-only coverage as enrollment.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import KnowledgeCatalogCoverage, MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeBenefitCalculation,
    KnowledgeCalculationPublication,
    KnowledgeCalculationStep,
    KnowledgeCitation,
    KnowledgeClaimCandidate,
    KnowledgeCoverageContext,
    KnowledgeDecisionContext,
    KnowledgeDecisionResult,
    KnowledgeFact,
    KnowledgeFactContext,
    KnowledgeFactNormalizer,
    KnowledgeQuestion,
    KnowledgeRuleEvaluation,
    KnowledgeRulePublication,
    KnowledgeStatusInterval,
)
from familycare_api.decisions.knowledge_engine import summarize_knowledge_results


@dataclass(frozen=True)
class KnowledgeContextRead:
    """One exact member-scoped input snapshot and its catalog accounting."""

    context: KnowledgeDecisionContext | None
    catalog_coverage: KnowledgeCatalogCoverage
    knowledge_import_run_id: UUID | None
    rule_import_run_id: UUID | None
    status_projection_digest_sha256: str | None
    reason_codes: tuple[str, ...] = ()


def _canonical(value: object) -> str:
    def default(item: object) -> str:
        if isinstance(item, Decimal):
            return format(item.normalize(), "f")
        if isinstance(item, UUID | date):
            return str(item)
        raise TypeError

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(value)


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, str))


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except ValueError, ArithmeticError:
        return None
    return result if result.is_finite() else None


def _citation(row: Mapping[str, Any]) -> KnowledgeCitation | None:
    key = row.get("citation_key")
    if not isinstance(key, str) or not key:
        return None
    return KnowledgeCitation(
        citation_key=key,
        terms_section_id=cast(UUID, row["terms_section_id"]),
        source_clause_id=cast(UUID | None, row.get("source_clause_id")),
        fact_id=cast(UUID | None, row.get("fact_id")),
        evidence_purpose=cast(str, row["evidence_purpose"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        source_text_sha256=cast(str, row["source_text_sha256"]),
        lineage_valid=bool(row.get("lineage_valid", False)),
    )


class PostgresKnowledgeDecisionRepository:
    """Read and persist private results inside the caller's transaction."""

    def read_context(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
    ) -> KnowledgeContextRead:
        header = connection.execute(
            """
            /* private-knowledge:current-runs */
            SELECT knowledge.id AS knowledge_import_run_id,
                   rules.id AS rule_import_run_id,
                   rules.projection_digest_sha256 AS rule_projection_digest_sha256
            FROM private_knowledge_import_runs AS knowledge
            LEFT JOIN private_knowledge_rule_import_runs AS rules
              ON rules.knowledge_import_run_id = knowledge.id
             AND rules.household_space_id = knowledge.household_space_id
             AND rules.state = 'APPLIED' AND rules.is_current
            WHERE knowledge.household_space_id = %(household)s
              AND knowledge.state = 'APPLIED' AND knowledge.is_current
            LIMIT 1
            """,
            {"household": scope.household_space_id},
        ).fetchone()
        if header is None:
            return KnowledgeContextRead(
                context=None,
                catalog_coverage=KnowledgeCatalogCoverage(),
                knowledge_import_run_id=None,
                rule_import_run_id=None,
                status_projection_digest_sha256=None,
            )

        knowledge_run_id = cast(UUID, header["knowledge_import_run_id"])
        rule_run_id = cast(UUID | None, header.get("rule_import_run_id"))
        coverage_rows = connection.execute(
            """
            /* private-knowledge:coverage-context */
            SELECT contract.id AS knowledge_contract_id,
                   coverage.id AS knowledge_coverage_id,
                   contract.product_display AS contract_label,
                   coverage.display_name AS coverage_label,
                   coverage.benefit_type,
                   coverage.insured_amount, coverage.currency,
                   coalesce(coverage.coverage_start, contract.contract_start)
                     AS contract_start,
                   coalesce(coverage.coverage_end, contract.contract_end)
                     AS contract_end,
                   disposition.disposition,
                   subject.binding_decision AS subject_binding_decision,
                   coverage.enrollment_decision
                     AS raw_certificate_enrollment_decision,
                   CASE
                     WHEN disposition.enrollment_authority =
                            'USER_CONFIRMED_COVERAGE_ENROLLMENT'
                      AND disposition.enrollment_confirmed_by IS NOT NULL
                     THEN 'MATCH'
                     ELSE coverage.enrollment_decision
                   END AS effective_enrollment_decision,
                   disposition.enrollment_decision_snapshot
                     AS publication_enrollment_decision_snapshot,
                   disposition.enrollment_authority
                     AS publication_enrollment_authority,
                   disposition.enrollment_confirmed_by
                     AS publication_enrollment_confirmed_by,
                   coverage.component_classification,
                   mapping.mapping_count,
                   mapping.mapping_applicability,
                   mapping.mapping_enrollment_decision,
                   mapping.document_identity_decision,
                   mapping.edition_applicability_decision,
                   mapping.section_mapping_decision,
                   mapping.overall_mapping_decision,
                   mapping.mapped_terms_section_id,
                   confirmation.decision AS current_confirmation_decision,
                   confirmation.confirmed_status AS current_confirmed_status,
                   confirmation.confirmation_digest_sha256,
                   coverage.operational_binding_decision,
                   coverage.rider_id
            FROM private_knowledge_subjects AS subject
            JOIN private_knowledge_contracts AS contract
              ON contract.subject_id = subject.id
             AND contract.import_run_id = subject.import_run_id
            JOIN private_knowledge_coverages AS coverage
              ON coverage.knowledge_contract_id = contract.id
             AND coverage.import_run_id = contract.import_run_id
            LEFT JOIN LATERAL (
              SELECT count(*)::integer AS mapping_count,
                     CASE
                       WHEN count(*) = 0 THEN 'UNKNOWN'
                       WHEN bool_or(item.mapping_applicability = 'NOT_APPLICABLE')
                         THEN 'NOT_APPLICABLE'
                       WHEN bool_and(item.mapping_applicability = 'APPLICABLE')
                         THEN 'APPLICABLE'
                       ELSE 'UNKNOWN'
                     END AS mapping_applicability,
                     CASE
                       WHEN count(*) = 0 THEN 'UNKNOWN'
                       WHEN bool_or(item.enrollment_decision = 'NO_MATCH')
                         THEN 'NO_MATCH'
                       WHEN bool_and(item.enrollment_decision = 'MATCH') THEN 'MATCH'
                       ELSE 'UNKNOWN'
                     END AS mapping_enrollment_decision,
                     CASE
                       WHEN count(*) = 0 THEN 'UNKNOWN'
                       WHEN bool_or(item.document_identity_decision = 'NO_MATCH')
                         THEN 'NO_MATCH'
                       WHEN bool_and(item.document_identity_decision = 'MATCH')
                         THEN 'MATCH'
                       ELSE 'UNKNOWN'
                     END AS document_identity_decision,
                     CASE
                       WHEN count(*) = 0 THEN 'UNKNOWN'
                       WHEN bool_or(item.edition_applicability_decision = 'NO_MATCH')
                         THEN 'NO_MATCH'
                       WHEN bool_and(item.edition_applicability_decision = 'MATCH')
                         THEN 'MATCH'
                       ELSE 'UNKNOWN'
                     END AS edition_applicability_decision,
                     CASE
                       WHEN count(*) = 0 THEN 'UNKNOWN'
                       WHEN bool_or(item.section_mapping_decision = 'NO_MATCH')
                         THEN 'NO_MATCH'
                       WHEN bool_and(item.section_mapping_decision = 'MATCH')
                         THEN 'MATCH'
                       ELSE 'UNKNOWN'
                     END AS section_mapping_decision,
                     CASE
                       WHEN count(*) = 0 THEN 'UNKNOWN'
                       WHEN bool_or(item.overall_decision = 'NO_MATCH') THEN 'NO_MATCH'
                       WHEN bool_and(item.overall_decision = 'MATCH') THEN 'MATCH'
                       ELSE 'UNKNOWN'
                     END AS overall_mapping_decision,
                     (array_agg(item.terms_section_id ORDER BY item.id)
                       FILTER (WHERE item.terms_section_id IS NOT NULL))[1]
                       AS mapped_terms_section_id
              FROM private_knowledge_coverage_terms_mappings AS item
              WHERE item.coverage_id = coverage.id
                AND item.import_run_id = coverage.import_run_id
            ) AS mapping ON true
            LEFT JOIN private_knowledge_contract_confirmations AS confirmation
              ON confirmation.knowledge_contract_id = contract.id
             AND confirmation.import_run_id = contract.import_run_id
             AND confirmation.is_current
            LEFT JOIN private_knowledge_coverage_execution_dispositions AS disposition
              ON disposition.knowledge_coverage_id = coverage.id
             AND disposition.knowledge_import_run_id = coverage.import_run_id
             AND disposition.rule_import_run_id = %(rule_run)s
             AND disposition.household_space_id = %(household)s
            WHERE subject.import_run_id = %(knowledge_run)s
              AND subject.household_space_id = %(household)s
              AND subject.family_member_id = %(member)s
              AND subject.binding_decision = 'MATCH'
              AND contract.household_space_id = %(household)s
              AND coverage.household_space_id = %(household)s
            ORDER BY contract.id, coverage.id
            """,
            {
                "household": scope.household_space_id,
                "member": event.family_member_id,
                "knowledge_run": knowledge_run_id,
                "rule_run": rule_run_id,
            },
        ).fetchall()
        catalog = self._catalog(coverage_rows)
        if rule_run_id is None:
            reasons = (
                ("KNOWLEDGE_PUBLICATION_UNAVAILABLE",) if catalog.benefit_coverage_count else ()
            )
            return KnowledgeContextRead(
                context=None,
                catalog_coverage=catalog,
                knowledge_import_run_id=knowledge_run_id,
                rule_import_run_id=None,
                status_projection_digest_sha256=None,
                reason_codes=reasons,
            )

        benefit_rows = tuple(
            row
            for row in coverage_rows
            if row.get("component_classification") == "BENEFIT_COVERAGE"
        )
        fatal: list[str] = []
        if any(row.get("disposition") is None for row in benefit_rows):
            fatal.append("KNOWLEDGE_DISPOSITION_INCOMPLETE")
        if any(
            row.get("disposition") == "PUBLISHED"
            and (
                int(row.get("mapping_count") or 0) < 1
                or row.get("mapped_terms_section_id") is None
                or row.get("mapping_applicability") != "APPLICABLE"
                or row.get("mapping_enrollment_decision") != "MATCH"
                or row.get("document_identity_decision") != "MATCH"
                or row.get("edition_applicability_decision") != "MATCH"
                or row.get("section_mapping_decision") != "MATCH"
                or row.get("overall_mapping_decision") != "MATCH"
            )
            for row in benefit_rows
        ):
            fatal.append("KNOWLEDGE_MAPPING_INCOMPLETE")
        if fatal:
            return KnowledgeContextRead(
                context=None,
                catalog_coverage=catalog,
                knowledge_import_run_id=knowledge_run_id,
                rule_import_run_id=rule_run_id,
                status_projection_digest_sha256=None,
                reason_codes=tuple(dict.fromkeys(fatal)),
            )

        contract_ids = tuple(
            dict.fromkeys(cast(UUID, row["knowledge_contract_id"]) for row in benefit_rows)
        )
        coverage_ids = tuple(cast(UUID, row["knowledge_coverage_id"]) for row in benefit_rows)
        rider_ids = tuple(
            cast(UUID, rider)
            for row in benefit_rows
            if row.get("operational_binding_decision") == "MATCH"
            and (rider := row.get("rider_id")) is not None
        )
        interval_rows = connection.execute(
            """
            /* private-knowledge:status-intervals */
            SELECT knowledge_contract_id, effective_from, effective_through,
                   decision, confirmed_status, authority,
                   interval_digest_sha256
            FROM private_knowledge_contract_status_intervals
            WHERE household_space_id = %(household)s
              AND import_run_id = %(knowledge_run)s
              AND rule_import_run_id = %(rule_run)s
              AND knowledge_contract_id = ANY(%(contracts)s)
              AND review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            ORDER BY knowledge_contract_id, effective_from, effective_through, id
            """,
            {
                "household": scope.household_space_id,
                "knowledge_run": knowledge_run_id,
                "rule_run": rule_run_id,
                "contracts": list(contract_ids),
            },
        ).fetchall()
        history_rows = connection.execute(
            """
            /* private-knowledge:claim-history */
            SELECT rider_id,
                   count(*) FILTER (WHERE counted_occurrence)::integer
                     AS counted_occurrence
            FROM claim_history
            WHERE household_space_id = %(household)s
              AND family_member_id = %(member)s
              AND rider_id = ANY(%(riders)s)
            GROUP BY rider_id
            ORDER BY rider_id
            """,
            {
                "household": scope.household_space_id,
                "member": event.family_member_id,
                "riders": list(rider_ids),
            },
        ).fetchall()
        receipt_rows = connection.execute(
            """
            /* private-knowledge:receipt-facts */
            SELECT amount, currency, coverage_category, confirmation_level
            FROM receipt_lines
            WHERE household_space_id = %(household)s
              AND medical_event_id = %(event)s
              AND deleted_at IS NULL
            ORDER BY id
            """,
            {"household": scope.household_space_id, "event": event.id},
        ).fetchall()
        normalizer_rows = connection.execute(
            """
            /* private-knowledge:normalizers */
            SELECT normalizer_digest_sha256 AS normalizer_key, field_path,
                   normalized_tokens_json, normalized_value_json, priority
            FROM private_knowledge_fact_normalizer_publications
            WHERE household_space_id = %(household)s
              AND knowledge_import_run_id = %(knowledge_run)s
              AND rule_import_run_id = %(rule_run)s
              AND review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            ORDER BY field_path, priority DESC, id
            """,
            {
                "household": scope.household_space_id,
                "knowledge_run": knowledge_run_id,
                "rule_run": rule_run_id,
            },
        ).fetchall()
        rule_rows = self._rule_rows(
            connection,
            scope,
            knowledge_run_id,
            rule_run_id,
            coverage_ids,
        )
        calculation_rows = self._calculation_rows(
            connection,
            scope,
            knowledge_run_id,
            rule_run_id,
            coverage_ids,
        )

        intervals = self._intervals(interval_rows)
        history = {
            cast(UUID, row["rider_id"]): int(row.get("counted_occurrence") or 0)
            for row in history_rows
        }
        rules = self._rules(rule_rows)
        calculations, calculation_errors = self._calculations(calculation_rows)
        supporting_facts, receipt_currency, receipt_errors = self._receipt_facts(receipt_rows)
        context_reasons = tuple(dict.fromkeys((*calculation_errors, *receipt_errors)))
        status_digest = self._status_digest(
            header,
            benefit_rows,
            interval_rows,
        )
        coverages: list[KnowledgeCoverageContext] = []
        for row in benefit_rows:
            coverage_id = cast(UUID, row["knowledge_coverage_id"])
            rider_id = cast(UUID | None, row.get("rider_id"))
            history_fact = None
            if row.get("operational_binding_decision") == "MATCH" and rider_id is not None:
                history_fact = KnowledgeFact(
                    value=history.get(rider_id, 0),
                    provenance="DERIVED_CONFIRMED",
                )
            coverages.append(
                KnowledgeCoverageContext(
                    knowledge_contract_id=cast(UUID, row["knowledge_contract_id"]),
                    knowledge_coverage_id=coverage_id,
                    contract_label=cast(str, row["contract_label"]),
                    coverage_label=cast(str, row["coverage_label"]),
                    benefit_type=cast(Any, row["benefit_type"]),
                    insured_amount=_decimal(row.get("insured_amount")),
                    currency=cast(str | None, row.get("currency")),
                    contract_start=cast(date | None, row.get("contract_start")),
                    contract_end=cast(date | None, row.get("contract_end")),
                    disposition=cast(Any, row["disposition"]),
                    subject_binding_decision=cast(Any, row["subject_binding_decision"]),
                    enrollment_decision=cast(Any, row["effective_enrollment_decision"]),
                    component_classification=cast(Any, row["component_classification"]),
                    mapping_applicability=cast(Any, row["mapping_applicability"]),
                    mapping_enrollment_decision=cast(Any, row["mapping_enrollment_decision"]),
                    document_identity_decision=cast(Any, row["document_identity_decision"]),
                    edition_applicability_decision=cast(Any, row["edition_applicability_decision"]),
                    section_mapping_decision=cast(Any, row["section_mapping_decision"]),
                    overall_mapping_decision=cast(Any, row["overall_mapping_decision"]),
                    current_confirmation_decision=cast(
                        Any, row.get("current_confirmation_decision")
                    ),
                    current_confirmed_status=cast(Any, row.get("current_confirmed_status")),
                    status_intervals=intervals.get(cast(UUID, row["knowledge_contract_id"]), ()),
                    rules=rules.get(coverage_id, ()),
                    calculation=calculations.get(coverage_id),
                    claim_history_counted_occurrence=history_fact,
                )
            )
        normalizers = tuple(
            KnowledgeFactNormalizer(
                normalizer_key=cast(str, row["normalizer_key"]),
                field_path=cast(str, row["field_path"]),
                normalized_tokens=tuple(
                    cast(str, item) for item in _sequence(row["normalized_tokens_json"])
                ),
                normalized_value=cast(Any, row["normalized_value_json"]),
                priority=int(row["priority"]),
            )
            for row in normalizer_rows
        )
        context = KnowledgeDecisionContext(
            household_space_id=scope.household_space_id,
            family_member_id=event.family_member_id,
            knowledge_import_run_id=knowledge_run_id,
            rule_import_run_id=rule_run_id,
            status_projection_digest_sha256=status_digest,
            coverages=tuple(coverages),
            normalizers=normalizers,
            supporting_facts=supporting_facts,
            receipt_currency=receipt_currency,
        )
        return KnowledgeContextRead(
            context=context,
            catalog_coverage=catalog,
            knowledge_import_run_id=knowledge_run_id,
            rule_import_run_id=rule_run_id,
            status_projection_digest_sha256=status_digest,
            reason_codes=context_reasons,
        )

    @staticmethod
    def _catalog(rows: Sequence[Mapping[str, Any]]) -> KnowledgeCatalogCoverage:
        contract_ids = {
            row["knowledge_contract_id"]
            for row in rows
            if row.get("knowledge_contract_id") is not None
        }
        benefits = tuple(
            row for row in rows if row.get("component_classification") == "BENEFIT_COVERAGE"
        )
        return KnowledgeCatalogCoverage(
            contract_count=len(contract_ids),
            benefit_coverage_count=len(benefits),
            published_coverage_count=sum(row.get("disposition") == "PUBLISHED" for row in benefits),
            advisory_coverage_count=sum(row.get("disposition") == "ADVISORY" for row in benefits),
            blocked_coverage_count=sum(row.get("disposition") == "BLOCKED" for row in benefits),
            not_applicable_coverage_count=sum(
                row.get("disposition") == "NOT_APPLICABLE" for row in benefits
            ),
        )

    @staticmethod
    def _intervals(
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[UUID, tuple[KnowledgeStatusInterval, ...]]:
        grouped: dict[UUID, list[KnowledgeStatusInterval]] = defaultdict(list)
        for row in rows:
            grouped[cast(UUID, row["knowledge_contract_id"])].append(
                KnowledgeStatusInterval(
                    effective_from=cast(date, row["effective_from"]),
                    effective_through=cast(date, row["effective_through"]),
                    decision=cast(Any, row["decision"]),
                    confirmed_status=cast(Any, row["confirmed_status"]),
                    authority=cast(Any, row["authority"]),
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _receipt_facts(
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, KnowledgeFact], str | None, tuple[str, ...]]:
        if not rows:
            return {}, None, ()
        currencies = {cast(str, row["currency"]) for row in rows}
        if len(currencies) != 1:
            return {}, None, ("RECEIPT_CURRENCY_CONFLICT",)
        currency = next(iter(currencies))
        all_confirmed = all(row.get("confirmation_level") == "user" for row in rows)
        if not all_confirmed:
            return {}, currency, ()
        amounts = tuple(_decimal(row.get("amount")) for row in rows)
        if any(value is None for value in amounts):
            return {}, currency, ("RECEIPT_AMOUNT_INVALID",)
        confirmed = sum(cast(Decimal, value) for value in amounts)
        facts = {
            "Receipt.confirmed_amount": KnowledgeFact(
                value=confirmed,
                provenance="USER_CONFIRMED",
            )
        }
        resolved = all(row.get("coverage_category") in {"covered", "excluded"} for row in rows)
        if resolved:
            facts["Receipt.covered_amount"] = KnowledgeFact(
                value=sum(
                    cast(Decimal, amount)
                    for row, amount in zip(rows, amounts, strict=True)
                    if row.get("coverage_category") == "covered"
                ),
                provenance="USER_CONFIRMED",
            )
        return facts, currency, ()

    @staticmethod
    def _status_digest(
        header: Mapping[str, Any],
        coverage_rows: Sequence[Mapping[str, Any]],
        interval_rows: Sequence[Mapping[str, Any]],
    ) -> str:
        return _digest(
            {
                "knowledge_run": header["knowledge_import_run_id"],
                "rule_run": header.get("rule_import_run_id"),
                "rule_projection": header.get("rule_projection_digest_sha256"),
                "coverage_admission": sorted(
                    (
                        str(row["knowledge_contract_id"]),
                        str(row["knowledge_coverage_id"]),
                        row.get("raw_certificate_enrollment_decision"),
                        row.get("effective_enrollment_decision"),
                        row.get("publication_enrollment_decision_snapshot"),
                        row.get("publication_enrollment_authority"),
                        (
                            str(row["publication_enrollment_confirmed_by"])
                            if row.get("publication_enrollment_confirmed_by") is not None
                            else None
                        ),
                        row.get("confirmation_digest_sha256"),
                    )
                    for row in coverage_rows
                ),
                "intervals": sorted(
                    (
                        str(row["knowledge_contract_id"]),
                        row.get("interval_digest_sha256"),
                    )
                    for row in interval_rows
                ),
            }
        )

    @staticmethod
    def _rule_rows(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        knowledge_run_id: UUID,
        rule_run_id: UUID,
        coverage_ids: tuple[UUID, ...],
    ) -> list[dict[str, Any]]:
        return connection.execute(
            """
            /* private-knowledge:rules */
            SELECT publication.id AS publication_id,
                   publication.knowledge_coverage_id,
                   publication.rule_key, publication.rule_kind,
                   publication.required, publication.result_reason_code,
                   publication.rule_json,
                   citation.citation_key, citation.terms_section_id,
                   citation.source_clause_id, citation.fact_id,
                   citation.evidence_purpose, citation.page_start,
                   citation.page_end, citation.source_text_sha256,
                   (
                     (
                       EXISTS (
                         SELECT 1
                         FROM private_knowledge_coverage_terms_mappings AS mapping
                         WHERE mapping.coverage_id = publication.knowledge_coverage_id
                           AND mapping.import_run_id = publication.knowledge_import_run_id
                           AND mapping.terms_section_id = citation.terms_section_id
                           AND mapping.overall_decision = 'MATCH'
                       )
                       OR EXISTS (
                         SELECT 1
                         FROM private_knowledge_coverage_execution_dispositions
                              AS disposition
                         JOIN private_knowledge_coverages AS coverage
                           ON coverage.id = disposition.knowledge_coverage_id
                          AND coverage.import_run_id =
                              disposition.knowledge_import_run_id
                         JOIN private_knowledge_terms_assignments AS assignment
                           ON assignment.import_run_id = coverage.import_run_id
                          AND assignment.household_space_id =
                              disposition.household_space_id
                          AND assignment.knowledge_contract_id =
                              coverage.knowledge_contract_id
                          AND assignment.document_identity_decision = 'MATCH'
                          AND assignment.edition_applicability_decision = 'MATCH'
                          AND assignment.overall_decision = 'MATCH'
                         JOIN private_knowledge_terms_assignment_sources
                              AS assignment_source
                           ON assignment_source.import_run_id = assignment.import_run_id
                          AND assignment_source.terms_assignment_id = assignment.id
                         JOIN private_knowledge_terms_sections AS assigned_section
                           ON assigned_section.id = citation.terms_section_id
                          AND assigned_section.import_run_id =
                              assignment_source.import_run_id
                          AND assigned_section.terms_source_alias_digest_sha256 =
                              assignment_source.source_alias_digest_sha256
                          AND assigned_section.review_state IN
                              ('DIRECT_REVIEWED', 'USER_CONFIRMED')
                         WHERE disposition.knowledge_coverage_id =
                               publication.knowledge_coverage_id
                           AND disposition.knowledge_import_run_id =
                               publication.knowledge_import_run_id
                           AND disposition.rule_import_run_id =
                               publication.rule_import_run_id
                           AND disposition.household_space_id =
                               publication.household_space_id
                           AND disposition.disposition = 'ADVISORY'
                       )
                     )
                     AND (
                       citation.source_clause_id IS NULL OR EXISTS (
                         SELECT 1 FROM private_knowledge_source_clauses AS clause
                         WHERE clause.id = citation.source_clause_id
                           AND clause.import_run_id = citation.knowledge_import_run_id
                           AND clause.terms_section_id = citation.terms_section_id
                       )
                     )
                     AND (
                       citation.fact_id IS NULL OR EXISTS (
                         SELECT 1 FROM private_knowledge_facts AS fact
                         WHERE fact.id = citation.fact_id
                           AND fact.import_run_id = citation.knowledge_import_run_id
                           AND fact.terms_section_id = citation.terms_section_id
                       )
                     )
                   ) AS lineage_valid
            FROM private_knowledge_rule_publications AS publication
            LEFT JOIN private_knowledge_rule_citations AS citation
              ON citation.rule_publication_id = publication.id
             AND citation.rule_import_run_id = publication.rule_import_run_id
             AND citation.knowledge_import_run_id = publication.knowledge_import_run_id
             AND citation.household_space_id = publication.household_space_id
            WHERE publication.household_space_id = %(household)s
              AND publication.knowledge_import_run_id = %(knowledge_run)s
              AND publication.rule_import_run_id = %(rule_run)s
              AND publication.knowledge_coverage_id = ANY(%(coverages)s)
              AND publication.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            ORDER BY publication.knowledge_coverage_id, publication.rule_key,
                     publication.id, citation.page_start, citation.id
            """,
            {
                "household": scope.household_space_id,
                "knowledge_run": knowledge_run_id,
                "rule_run": rule_run_id,
                "coverages": list(coverage_ids),
            },
        ).fetchall()

    @staticmethod
    def _calculation_rows(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        knowledge_run_id: UUID,
        rule_run_id: UUID,
        coverage_ids: tuple[UUID, ...],
    ) -> list[dict[str, Any]]:
        return connection.execute(
            """
            /* private-knowledge:calculations */
            SELECT publication.id AS publication_id,
                   publication.knowledge_coverage_id,
                   publication.calculation_key, publication.calculation_kind,
                   publication.result_reason_code,
                   publication.calculation_json,
                   citation.citation_key, citation.terms_section_id,
                   citation.source_clause_id, citation.fact_id,
                   citation.evidence_purpose, citation.page_start,
                   citation.page_end, citation.source_text_sha256,
                   (
                     (
                       EXISTS (
                         SELECT 1
                         FROM private_knowledge_coverage_terms_mappings AS mapping
                         WHERE mapping.coverage_id = publication.knowledge_coverage_id
                           AND mapping.import_run_id = publication.knowledge_import_run_id
                           AND mapping.terms_section_id = citation.terms_section_id
                           AND mapping.overall_decision = 'MATCH'
                       )
                       OR EXISTS (
                         SELECT 1
                         FROM private_knowledge_coverage_execution_dispositions
                              AS disposition
                         JOIN private_knowledge_coverages AS coverage
                           ON coverage.id = disposition.knowledge_coverage_id
                          AND coverage.import_run_id =
                              disposition.knowledge_import_run_id
                         JOIN private_knowledge_terms_assignments AS assignment
                           ON assignment.import_run_id = coverage.import_run_id
                          AND assignment.household_space_id =
                              disposition.household_space_id
                          AND assignment.knowledge_contract_id =
                              coverage.knowledge_contract_id
                          AND assignment.document_identity_decision = 'MATCH'
                          AND assignment.edition_applicability_decision = 'MATCH'
                          AND assignment.overall_decision = 'MATCH'
                         JOIN private_knowledge_terms_assignment_sources
                              AS assignment_source
                           ON assignment_source.import_run_id = assignment.import_run_id
                          AND assignment_source.terms_assignment_id = assignment.id
                         JOIN private_knowledge_terms_sections AS assigned_section
                           ON assigned_section.id = citation.terms_section_id
                          AND assigned_section.import_run_id =
                              assignment_source.import_run_id
                          AND assigned_section.terms_source_alias_digest_sha256 =
                              assignment_source.source_alias_digest_sha256
                          AND assigned_section.review_state IN
                              ('DIRECT_REVIEWED', 'USER_CONFIRMED')
                         WHERE disposition.knowledge_coverage_id =
                               publication.knowledge_coverage_id
                           AND disposition.knowledge_import_run_id =
                               publication.knowledge_import_run_id
                           AND disposition.rule_import_run_id =
                               publication.rule_import_run_id
                           AND disposition.household_space_id =
                               publication.household_space_id
                           AND disposition.disposition = 'ADVISORY'
                       )
                     )
                     AND (
                       citation.source_clause_id IS NULL OR EXISTS (
                         SELECT 1 FROM private_knowledge_source_clauses AS clause
                         WHERE clause.id = citation.source_clause_id
                           AND clause.import_run_id = citation.knowledge_import_run_id
                           AND clause.terms_section_id = citation.terms_section_id
                       )
                     )
                     AND (
                       citation.fact_id IS NULL OR EXISTS (
                         SELECT 1 FROM private_knowledge_facts AS fact
                         WHERE fact.id = citation.fact_id
                           AND fact.import_run_id = citation.knowledge_import_run_id
                           AND fact.terms_section_id = citation.terms_section_id
                       )
                     )
                   ) AS lineage_valid
            FROM private_knowledge_calculation_publications AS publication
            LEFT JOIN private_knowledge_calculation_citations AS citation
              ON citation.calculation_publication_id = publication.id
             AND citation.rule_import_run_id = publication.rule_import_run_id
             AND citation.knowledge_import_run_id = publication.knowledge_import_run_id
             AND citation.household_space_id = publication.household_space_id
            WHERE publication.household_space_id = %(household)s
              AND publication.knowledge_import_run_id = %(knowledge_run)s
              AND publication.rule_import_run_id = %(rule_run)s
              AND publication.knowledge_coverage_id = ANY(%(coverages)s)
              AND publication.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            ORDER BY publication.knowledge_coverage_id,
                     publication.calculation_key, publication.id,
                     citation.page_start, citation.id
            """,
            {
                "household": scope.household_space_id,
                "knowledge_run": knowledge_run_id,
                "rule_run": rule_run_id,
                "coverages": list(coverage_ids),
            },
        ).fetchall()

    @staticmethod
    def _rules(
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[UUID, tuple[KnowledgeRulePublication, ...]]:
        grouped: dict[UUID, tuple[Mapping[str, Any], list[KnowledgeCitation]]] = {}
        for row in rows:
            publication_id = cast(UUID, row["publication_id"])
            grouped.setdefault(publication_id, (row, []))
            citation = _citation(row)
            if citation is not None:
                grouped[publication_id][1].append(citation)
        by_coverage: dict[UUID, list[KnowledgeRulePublication]] = defaultdict(list)
        for row, citations in grouped.values():
            by_coverage[cast(UUID, row["knowledge_coverage_id"])].append(
                KnowledgeRulePublication(
                    publication_id=cast(UUID, row["publication_id"]),
                    rule_key=cast(str, row["rule_key"]),
                    rule_kind=cast(Any, row["rule_kind"]),
                    required=bool(row["required"]),
                    result_reason_code=cast(str, row["result_reason_code"]),
                    rule_document=cast(Mapping[str, object], row["rule_json"]),
                    citations=tuple(citations),
                )
            )
        return {key: tuple(value) for key, value in by_coverage.items()}

    @staticmethod
    def _calculations(
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[
        dict[UUID, KnowledgeCalculationPublication],
        tuple[str, ...],
    ]:
        grouped: dict[UUID, tuple[Mapping[str, Any], list[KnowledgeCitation]]] = {}
        for row in rows:
            publication_id = cast(UUID, row["publication_id"])
            grouped.setdefault(publication_id, (row, []))
            citation = _citation(row)
            if citation is not None:
                grouped[publication_id][1].append(citation)
        by_coverage: dict[UUID, list[KnowledgeCalculationPublication]] = defaultdict(list)
        for row, citations in grouped.values():
            kind = row["calculation_kind"]
            if kind not in {"FIXED", "INDEMNITY"}:
                continue
            by_coverage[cast(UUID, row["knowledge_coverage_id"])].append(
                KnowledgeCalculationPublication(
                    publication_id=cast(UUID, row["publication_id"]),
                    calculation_key=cast(str, row["calculation_key"]),
                    calculation_kind=cast(Any, kind),
                    result_reason_code=cast(str, row["result_reason_code"]),
                    calculation_document=cast(Mapping[str, object], row["calculation_json"]),
                    citations=tuple(citations),
                )
            )
        errors = (
            ("KNOWLEDGE_CALCULATION_PUBLICATION_CONFLICT",)
            if any(len(value) > 1 for value in by_coverage.values())
            else ()
        )
        return (
            {key: value[0] for key, value in by_coverage.items() if len(value) == 1},
            errors,
        )

    def persist_result(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        result: KnowledgeDecisionResult,
    ) -> None:
        for evaluation in result.evaluations:
            connection.execute(
                """
                INSERT INTO private_knowledge_rule_evaluations (
                  id, household_space_id, decision_run_id,
                  knowledge_import_run_id, knowledge_rule_import_run_id,
                  knowledge_coverage_id, rule_publication_id, result,
                  required, reason_code, fact_paths_json, missing_fields_json,
                  conflicting_fields_json, citation_snapshot_json,
                  evaluator_version
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    evaluation.evaluation_id,
                    scope.household_space_id,
                    result.run_id,
                    result.knowledge_import_run_id,
                    result.rule_import_run_id,
                    evaluation.knowledge_coverage_id,
                    evaluation.rule_publication_id,
                    evaluation.result,
                    evaluation.required,
                    evaluation.reason_code,
                    Jsonb(list(evaluation.fact_paths)),
                    Jsonb(list(evaluation.missing_fields)),
                    Jsonb(list(evaluation.conflicting_fields)),
                    Jsonb([self._citation_snapshot(item) for item in evaluation.citations]),
                    evaluation.evaluator_version,
                ),
            )
        self._persist_candidates_and_calculations(connection, scope, result)

    def load_result(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        run: Mapping[str, Any],
    ) -> KnowledgeDecisionResult | None:
        knowledge_run_id = cast(UUID | None, run.get("knowledge_import_run_id"))
        rule_run_id = cast(UUID | None, run.get("knowledge_rule_import_run_id"))
        status_digest = cast(str | None, run.get("knowledge_status_projection_digest"))
        if knowledge_run_id is None or rule_run_id is None or status_digest is None:
            return None
        evaluation_rows = connection.execute(
            """
            /* private-knowledge:stored-evaluations */
            SELECT * FROM private_knowledge_rule_evaluations
            WHERE household_space_id = %s AND decision_run_id = %s
            ORDER BY knowledge_coverage_id, rule_publication_id, id
            """,
            (scope.household_space_id, run["id"]),
        ).fetchall()
        evaluations = tuple(self._stored_evaluation(row) for row in evaluation_rows)
        by_coverage: dict[UUID, list[KnowledgeRuleEvaluation]] = defaultdict(list)
        for evaluation in evaluations:
            by_coverage[evaluation.knowledge_coverage_id].append(evaluation)
        candidate_rows = connection.execute(
            """
            /* private-knowledge:stored-candidates */
            SELECT * FROM private_knowledge_claim_candidates
            WHERE household_space_id = %s AND decision_run_id = %s
            ORDER BY knowledge_coverage_id, id
            """,
            (scope.household_space_id, run["id"]),
        ).fetchall()
        candidates = tuple(
            self._stored_candidate(
                row,
                tuple(by_coverage.get(cast(UUID, row["knowledge_coverage_id"]), ())),
            )
            for row in candidate_rows
        )
        calculation_rows = connection.execute(
            """
            /* private-knowledge:stored-calculations */
            SELECT calculation.*, step.step_number, step.operation,
                   step.input_amount, step.input_currency,
                   step.output_amount, step.output_currency,
                   step.rounding_rule AS step_rounding_rule,
                   step.reason_code AS step_reason_code
            FROM private_knowledge_benefit_calculations AS calculation
            LEFT JOIN private_knowledge_calculation_steps AS step
              ON step.private_benefit_calculation_id = calculation.id
            WHERE calculation.household_space_id = %s
              AND calculation.decision_run_id = %s
            ORDER BY calculation.knowledge_coverage_id, calculation.id,
                     step.step_number, step.id
            """,
            (scope.household_space_id, run["id"]),
        ).fetchall()
        grouped: dict[UUID, tuple[Mapping[str, Any], list[KnowledgeCalculationStep]]] = {}
        for row in calculation_rows:
            calculation_id = cast(UUID, row["id"])
            grouped.setdefault(calculation_id, (row, []))
            if row.get("step_number") is not None:
                grouped[calculation_id][1].append(
                    KnowledgeCalculationStep(
                        step_number=int(row["step_number"]),
                        operation=cast(str, row["operation"]),
                        input_amount=_decimal(row.get("input_amount")),
                        output_amount=_decimal(row.get("output_amount")),
                        currency=cast(
                            str | None,
                            row.get("output_currency") or row.get("input_currency"),
                        ),
                        rounding_rule=cast(str | None, row.get("step_rounding_rule")),
                        reason_code=cast(str, row["step_reason_code"]),
                    )
                )
        calculations = tuple(
            self._stored_calculation(row, tuple(steps)) for row, steps in grouped.values()
        )
        for calculation, (stored_row, _) in zip(calculations, grouped.values(), strict=True):
            if self._trace_digest(calculation) != stored_row.get("trace_digest_sha256"):
                raise ValueError("private calculation trace digest mismatch")
        fixed_subtotals, indemnity_summary = summarize_knowledge_results(
            candidates,
            calculations,
        )
        return KnowledgeDecisionResult(
            run_id=cast(UUID, run["id"]),
            knowledge_import_run_id=knowledge_run_id,
            rule_import_run_id=rule_run_id,
            status_projection_digest_sha256=status_digest,
            fact_context=KnowledgeFactContext({}),
            candidates=candidates,
            evaluations=evaluations,
            calculations=calculations,
            fixed_subtotals=fixed_subtotals,
            indemnity_summary=indemnity_summary,
            completeness=cast(Any, run.get("analysis_completeness", "UNAVAILABLE")),
            source_failure_codes=_strings(run.get("source_failure_codes_json")),
        )

    def is_stale(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        run: Mapping[str, Any],
    ) -> bool:
        if int(run["event_version"]) != event.version:
            return True
        header = connection.execute(
            """
            /* private-knowledge:stale-current-runs */
            SELECT knowledge.id AS knowledge_import_run_id,
                   rules.id AS rule_import_run_id,
                   rules.projection_digest_sha256 AS rule_projection_digest_sha256
            FROM private_knowledge_import_runs AS knowledge
            LEFT JOIN private_knowledge_rule_import_runs AS rules
              ON rules.knowledge_import_run_id = knowledge.id
             AND rules.household_space_id = knowledge.household_space_id
             AND rules.state = 'APPLIED' AND rules.is_current
            WHERE knowledge.household_space_id = %(household)s
              AND knowledge.state = 'APPLIED' AND knowledge.is_current
            LIMIT 1
            """,
            {"household": scope.household_space_id},
        ).fetchone()
        current_knowledge = header.get("knowledge_import_run_id") if header else None
        current_rule = header.get("rule_import_run_id") if header else None
        if current_knowledge != run.get("knowledge_import_run_id") or current_rule != run.get(
            "knowledge_rule_import_run_id"
        ):
            return True
        captured_digest = run.get("knowledge_status_projection_digest")
        if current_knowledge is None or current_rule is None:
            return captured_digest is not None
        confirmation_rows = connection.execute(
            """
            /* private-knowledge:stale-confirmations */
            SELECT contract.id AS knowledge_contract_id,
                   coverage.id AS knowledge_coverage_id,
                   coverage.enrollment_decision
                     AS raw_certificate_enrollment_decision,
                   CASE
                     WHEN disposition.enrollment_authority =
                            'USER_CONFIRMED_COVERAGE_ENROLLMENT'
                      AND disposition.enrollment_confirmed_by IS NOT NULL
                     THEN 'MATCH'
                     ELSE coverage.enrollment_decision
                   END AS effective_enrollment_decision,
                   disposition.enrollment_decision_snapshot
                     AS publication_enrollment_decision_snapshot,
                   disposition.enrollment_authority
                     AS publication_enrollment_authority,
                   disposition.enrollment_confirmed_by
                     AS publication_enrollment_confirmed_by,
                   confirmation.confirmation_digest_sha256
            FROM private_knowledge_subjects AS subject
            JOIN private_knowledge_contracts AS contract
              ON contract.subject_id = subject.id
             AND contract.import_run_id = subject.import_run_id
            JOIN private_knowledge_coverages AS coverage
              ON coverage.knowledge_contract_id = contract.id
             AND coverage.import_run_id = contract.import_run_id
            LEFT JOIN private_knowledge_contract_confirmations AS confirmation
              ON confirmation.knowledge_contract_id = contract.id
             AND confirmation.import_run_id = contract.import_run_id
             AND confirmation.is_current
            LEFT JOIN private_knowledge_coverage_execution_dispositions AS disposition
              ON disposition.knowledge_coverage_id = coverage.id
             AND disposition.knowledge_import_run_id = coverage.import_run_id
             AND disposition.rule_import_run_id = %(rule_run)s
             AND disposition.household_space_id = %(household)s
            WHERE subject.household_space_id = %(household)s
              AND subject.import_run_id = %(knowledge_run)s
              AND subject.family_member_id = %(member)s
              AND subject.binding_decision = 'MATCH'
              AND coverage.component_classification = 'BENEFIT_COVERAGE'
            ORDER BY contract.id, coverage.id
            """,
            {
                "household": scope.household_space_id,
                "knowledge_run": current_knowledge,
                "rule_run": current_rule,
                "member": event.family_member_id,
            },
        ).fetchall()
        contract_ids = list(
            dict.fromkeys(row["knowledge_contract_id"] for row in confirmation_rows)
        )
        interval_rows = connection.execute(
            """
            /* private-knowledge:stale-status-intervals */
            SELECT knowledge_contract_id, interval_digest_sha256
            FROM private_knowledge_contract_status_intervals
            WHERE household_space_id = %(household)s
              AND import_run_id = %(knowledge_run)s
              AND rule_import_run_id = %(rule_run)s
              AND knowledge_contract_id = ANY(%(contracts)s)
              AND review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            ORDER BY knowledge_contract_id, effective_from, effective_through, id
            """,
            {
                "household": scope.household_space_id,
                "knowledge_run": current_knowledge,
                "rule_run": current_rule,
                "contracts": contract_ids,
            },
        ).fetchall()
        assert header is not None
        current_digest = self._status_digest(header, confirmation_rows, interval_rows)
        return current_digest != captured_digest

    @classmethod
    def _stored_evaluation(cls, row: Mapping[str, Any]) -> KnowledgeRuleEvaluation:
        citations = tuple(
            cls._stored_citation(item)
            for item in _sequence(row.get("citation_snapshot_json"))
            if isinstance(item, Mapping)
        )
        return KnowledgeRuleEvaluation(
            evaluation_id=cast(UUID, row["id"]),
            knowledge_coverage_id=cast(UUID, row["knowledge_coverage_id"]),
            rule_publication_id=cast(UUID, row["rule_publication_id"]),
            result=cast(Any, row["result"]),
            required=bool(row["required"]),
            reason_code=cast(str, row["reason_code"]),
            fact_paths=_strings(row.get("fact_paths_json")),
            missing_fields=_strings(row.get("missing_fields_json")),
            conflicting_fields=_strings(row.get("conflicting_fields_json")),
            citations=citations,
            evaluator_version=cast(str, row["evaluator_version"]),
        )

    @staticmethod
    def _stored_citation(value: Mapping[str, Any]) -> KnowledgeCitation:
        return KnowledgeCitation(
            citation_key=cast(str, value["citation_key"]),
            terms_section_id=UUID(cast(str, value["terms_section_id"])),
            source_clause_id=(
                UUID(cast(str, value["source_clause_id"]))
                if value.get("source_clause_id") is not None
                else None
            ),
            fact_id=(
                UUID(cast(str, value["fact_id"])) if value.get("fact_id") is not None else None
            ),
            evidence_purpose=cast(str, value["evidence_purpose"]),
            page_start=int(value["page_start"]),
            page_end=int(value["page_end"]),
            source_text_sha256=cast(str, value["source_text_sha256"]),
            lineage_valid=bool(value["lineage_valid"]),
        )

    @staticmethod
    def _stored_candidate(
        row: Mapping[str, Any],
        evaluations: tuple[KnowledgeRuleEvaluation, ...],
    ) -> KnowledgeClaimCandidate:
        questions = tuple(
            KnowledgeQuestion(
                field_path=cast(str, item["field_path"]),
                reason_code=cast(str, item["reason_code"]),
            )
            for item in _sequence(row.get("questions_json"))
            if isinstance(item, Mapping)
        )
        return KnowledgeClaimCandidate(
            candidate_id=cast(UUID, row["id"]),
            knowledge_contract_id=cast(UUID, row["knowledge_contract_id"]),
            knowledge_coverage_id=cast(UUID, row["knowledge_coverage_id"]),
            contract_label=cast(str, row["contract_label_snapshot"]),
            coverage_label=cast(str, row["coverage_label_snapshot"]),
            benefit_type=cast(Any, row["benefit_type"]),
            result=cast(Any, row["aggregate_result"]),
            evaluations=evaluations,
            questions=questions,
            hold_reason_codes=_strings(row.get("hold_reason_codes_json")),
            required_match_count=int(row["required_match_count"]),
            required_unknown_count=int(row["required_unknown_count"]),
            required_no_match_count=int(row["required_no_match_count"]),
        )

    @staticmethod
    def _stored_calculation(
        row: Mapping[str, Any],
        steps: tuple[KnowledgeCalculationStep, ...],
    ) -> KnowledgeBenefitCalculation:
        return KnowledgeBenefitCalculation(
            calculation_id=cast(UUID, row["id"]),
            candidate_id=cast(UUID, row["private_claim_candidate_id"]),
            knowledge_coverage_id=cast(UUID, row["knowledge_coverage_id"]),
            calculation_publication_id=cast(UUID | None, row.get("calculation_publication_id")),
            kind=cast(Any, row["calculation_kind"]),
            status=cast(Any, row["calculation_status"]),
            currency=cast(str | None, row.get("currency")),
            confirmed_amount=_decimal(row.get("confirmed_amount")),
            conditional_amount=_decimal(row.get("conditional_amount")),
            excluded_amount=_decimal(row.get("excluded_amount")),
            deductible_amount=_decimal(row.get("deductible_amount")),
            applied_rate=_decimal(row.get("applied_rate")),
            applied_limit=_decimal(row.get("applied_limit")),
            rounding_rule=cast(str | None, row.get("rounding_rule")),
            hold_reason_code=cast(str | None, row.get("hold_reason_code")),
            steps=steps,
        )

    def _persist_candidates_and_calculations(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        result: KnowledgeDecisionResult,
    ) -> None:
        for candidate in result.candidates:
            connection.execute(
                """
                INSERT INTO private_knowledge_claim_candidates (
                  id, household_space_id, decision_run_id,
                  knowledge_import_run_id, knowledge_rule_import_run_id,
                  knowledge_contract_id, knowledge_coverage_id,
                  contract_label_snapshot, coverage_label_snapshot,
                  benefit_type, aggregate_result, required_match_count,
                  required_unknown_count, required_no_match_count,
                  questions_json, hold_reason_codes_json, claim_start_ready
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, false
                )
                """,
                (
                    candidate.candidate_id,
                    scope.household_space_id,
                    result.run_id,
                    result.knowledge_import_run_id,
                    result.rule_import_run_id,
                    candidate.knowledge_contract_id,
                    candidate.knowledge_coverage_id,
                    candidate.contract_label,
                    candidate.coverage_label,
                    candidate.benefit_type,
                    candidate.result,
                    candidate.required_match_count,
                    candidate.required_unknown_count,
                    candidate.required_no_match_count,
                    Jsonb(
                        [
                            {
                                "field_path": question.field_path,
                                "reason_code": question.reason_code,
                            }
                            for question in candidate.questions
                        ]
                    ),
                    Jsonb(list(candidate.hold_reason_codes)),
                ),
            )
        for calculation in result.calculations:
            trace = self._trace_digest(calculation)
            connection.execute(
                """
                INSERT INTO private_knowledge_benefit_calculations (
                  id, household_space_id, decision_run_id,
                  private_claim_candidate_id, knowledge_import_run_id,
                  knowledge_rule_import_run_id, knowledge_coverage_id,
                  calculation_publication_id, calculation_kind,
                  calculation_status, currency, confirmed_amount,
                  conditional_amount, excluded_amount, deductible_amount,
                  applied_rate, applied_limit, rounding_rule,
                  hold_reason_code, trace_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    calculation.calculation_id,
                    scope.household_space_id,
                    result.run_id,
                    calculation.candidate_id,
                    result.knowledge_import_run_id,
                    result.rule_import_run_id,
                    calculation.knowledge_coverage_id,
                    calculation.calculation_publication_id,
                    calculation.kind,
                    calculation.status,
                    calculation.currency,
                    calculation.confirmed_amount,
                    calculation.conditional_amount,
                    calculation.excluded_amount,
                    calculation.deductible_amount,
                    calculation.applied_rate,
                    calculation.applied_limit,
                    calculation.rounding_rule,
                    calculation.hold_reason_code,
                    trace,
                ),
            )
            for step in calculation.steps:
                connection.execute(
                    """
                    INSERT INTO private_knowledge_calculation_steps (
                      private_benefit_calculation_id, step_number, operation,
                      input_amount, input_currency, output_amount,
                      output_currency, rounding_rule, reason_code
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        calculation.calculation_id,
                        step.step_number,
                        step.operation,
                        step.input_amount,
                        step.currency if step.input_amount is not None else None,
                        step.output_amount,
                        step.currency if step.output_amount is not None else None,
                        step.rounding_rule,
                        step.reason_code,
                    ),
                )

    @staticmethod
    def _citation_snapshot(citation: KnowledgeCitation) -> dict[str, object]:
        return {
            "citation_key": citation.citation_key,
            "terms_section_id": str(citation.terms_section_id),
            "source_clause_id": (
                str(citation.source_clause_id) if citation.source_clause_id is not None else None
            ),
            "fact_id": str(citation.fact_id) if citation.fact_id is not None else None,
            "evidence_purpose": citation.evidence_purpose,
            "page_start": citation.page_start,
            "page_end": citation.page_end,
            "source_text_sha256": citation.source_text_sha256,
            "lineage_valid": citation.lineage_valid,
        }

    @staticmethod
    def _trace_digest(calculation: KnowledgeBenefitCalculation) -> str:
        return _digest(
            {
                "calculation_id": calculation.calculation_id,
                "candidate_id": calculation.candidate_id,
                "knowledge_coverage_id": calculation.knowledge_coverage_id,
                "calculation_publication_id": calculation.calculation_publication_id,
                "kind": calculation.kind,
                "status": calculation.status,
                "currency": calculation.currency,
                "confirmed_amount": calculation.confirmed_amount,
                "conditional_amount": calculation.conditional_amount,
                "excluded_amount": calculation.excluded_amount,
                "deductible_amount": calculation.deductible_amount,
                "applied_rate": calculation.applied_rate,
                "applied_limit": calculation.applied_limit,
                "rounding_rule": calculation.rounding_rule,
                "hold_reason_code": calculation.hold_reason_code,
                "steps": [
                    {
                        "step_number": step.step_number,
                        "operation": step.operation,
                        "input_amount": step.input_amount,
                        "output_amount": step.output_amount,
                        "currency": step.currency,
                        "rounding_rule": step.rounding_rule,
                        "reason_code": step.reason_code,
                    }
                    for step in calculation.steps
                ],
            }
        )

"""Bounded household-scoped PostgreSQL reads for private knowledge."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row
from pydantic import ValidationError

from familycare_api.common.scope import HouseholdScope
from familycare_api.private_knowledge.reconciliation import KnowledgeEntityCounts
from familycare_api.private_knowledge.schemas import (
    CurrentKnowledgeResponse,
    KnowledgeContractDetailResponse,
    KnowledgeContractListItemResponse,
    KnowledgeContractPageResponse,
    KnowledgeCoverageMappingResponse,
    KnowledgeCoverageResponse,
    KnowledgeFactCitationResponse,
    KnowledgeFactConditionsResponse,
    KnowledgeFactResponse,
    KnowledgeTermsAssignmentResponse,
    KnowledgeTermsSectionResponse,
)

_MAX_COVERAGES = 2_000
_MAX_ASSIGNMENTS = 8
_MAX_MAPPINGS = 2_000
_MAX_SECTIONS = 2_000
_MAX_FACTS = 5_000
_MAX_CITATIONS = 160_000


class PrivateKnowledgeQueryRepositoryError(RuntimeError):
    pass


class PrivateKnowledgeQueryTooLargeError(PrivateKnowledgeQueryRepositoryError):
    pass


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PrivateKnowledgeQueryRepositoryError
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class PostgresPrivateKnowledgeQueryRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def current(self, scope: HouseholdScope) -> CurrentKnowledgeResponse | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    row = connection.execute(
                        """
                        SELECT id, entity_counts_json
                        FROM private_knowledge_import_runs
                        WHERE household_space_id = %s
                          AND state = 'APPLIED' AND is_current
                        """,
                        (scope.household_space_id,),
                    ).fetchone()
                    if row is None:
                        return None
                    safety = self._safety_counts(connection, cast(UUID, row["id"]))
                    return CurrentKnowledgeResponse(
                        schema_version="1",
                        run_id=cast(UUID, row["id"]),
                        counts=KnowledgeEntityCounts.model_validate(row["entity_counts_json"]),
                        executable_fact_count=safety[0],
                        executable_mapping_count=safety[1],
                        unsafe_operational_binding_count=safety[2],
                    )
        except psycopg.Error, KeyError, TypeError, ValueError, ValidationError:
            raise PrivateKnowledgeQueryRepositoryError from None

    def list_contracts(
        self,
        scope: HouseholdScope,
        *,
        limit: int,
        after: UUID | None,
    ) -> KnowledgeContractPageResponse | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    run_id = self._current_run_id(connection, scope)
                    if run_id is None:
                        return None
                    rows = self._contract_rows(
                        connection,
                        run_id=run_id,
                        after=after,
                        limit=limit + 1,
                    )
                    has_more = len(rows) > limit
                    items = tuple(self._contract_item(row) for row in rows[:limit])
                    return KnowledgeContractPageResponse(
                        schema_version="1",
                        items=items,
                        next_cursor=items[-1].id if has_more and items else None,
                    )
        except psycopg.Error, KeyError, TypeError, ValueError, ValidationError:
            raise PrivateKnowledgeQueryRepositoryError from None

    def get_contract(
        self,
        scope: HouseholdScope,
        contract_id: UUID,
    ) -> KnowledgeContractDetailResponse | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.isolation_level = IsolationLevel.REPEATABLE_READ
                connection.read_only = True
                with connection.transaction():
                    run_id = self._current_run_id(connection, scope)
                    if run_id is None:
                        return None
                    contract_rows = self._contract_rows(
                        connection,
                        run_id=run_id,
                        contract_id=contract_id,
                        limit=1,
                    )
                    if not contract_rows:
                        return None
                    contract = self._contract_item(contract_rows[0])
                    coverages = self._coverages(connection, run_id, contract_id)
                    assignments = self._assignments(connection, run_id, contract_id)
                    mappings = self._mappings(connection, run_id, contract_id)
                    sections = self._sections(connection, run_id, contract_id)
                    return KnowledgeContractDetailResponse(
                        schema_version="1",
                        contract=contract,
                        coverages=coverages,
                        terms_assignments=assignments,
                        coverage_mappings=mappings,
                        terms_sections=sections,
                    )
        except PrivateKnowledgeQueryTooLargeError:
            raise
        except psycopg.Error, KeyError, TypeError, ValueError, ValidationError:
            raise PrivateKnowledgeQueryRepositoryError from None

    @staticmethod
    def _current_run_id(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
    ) -> UUID | None:
        row = connection.execute(
            """
            SELECT id
            FROM private_knowledge_import_runs
            WHERE household_space_id = %s AND state = 'APPLIED' AND is_current
            """,
            (scope.household_space_id,),
        ).fetchone()
        return cast(UUID, row["id"]) if row is not None else None

    @staticmethod
    def _safety_counts(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
    ) -> tuple[int, int, int]:
        row = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM private_knowledge_facts
               WHERE import_run_id = %(run)s AND executable) AS executable_facts,
              (SELECT count(*) FROM private_knowledge_coverage_terms_mappings
               WHERE import_run_id = %(run)s AND executable) AS executable_mappings,
              (
                (SELECT count(*) FROM private_knowledge_subjects
                 WHERE import_run_id = %(run)s
                   AND (family_member_id IS NOT NULL
                        OR binding_decision <> 'UNKNOWN')) +
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
        if row is None:
            raise PrivateKnowledgeQueryRepositoryError
        return (
            int(row["executable_facts"]),
            int(row["executable_mappings"]),
            int(row["unsafe_bindings"]),
        )

    @staticmethod
    def _contract_rows(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        run_id: UUID,
        limit: int,
        after: UUID | None = None,
        contract_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        filters = ["contract.import_run_id = %(run)s"]
        parameters: dict[str, object] = {"run": run_id, "limit": limit}
        if after is not None:
            filters.append("contract.id > %(after)s")
            parameters["after"] = after
        if contract_id is not None:
            filters.append("contract.id = %(contract)s")
            parameters["contract"] = contract_id
        where = " AND ".join(filters)
        return connection.execute(
            f"""
            SELECT
              contract.id, contract.subject_id, subject.family_alias,
              contract.insurer_display, contract.product_display,
              contract.contract_start, contract.contract_end,
              contract.certificate_decision, contract.current_status,
              count(coverage.id) AS coverage_count,
              count(coverage.id) FILTER (
                WHERE coverage.enrollment_decision = 'MATCH'
              ) AS enrollment_match_count,
              count(coverage.id) FILTER (
                WHERE coverage.enrollment_decision = 'NO_MATCH'
              ) AS enrollment_no_match_count,
              count(coverage.id) FILTER (
                WHERE coverage.enrollment_decision = 'UNKNOWN'
              ) AS enrollment_unknown_count,
              coalesce(assignment.document_identity_decision, 'UNKNOWN')
                AS document_identity_decision,
              coalesce(assignment.edition_applicability_decision, 'UNKNOWN')
                AS edition_applicability_decision,
              coalesce(assignment.overall_decision, 'UNKNOWN')
                AS terms_overall_decision
            FROM private_knowledge_contracts AS contract
            JOIN private_knowledge_subjects AS subject
              ON subject.id = contract.subject_id
             AND subject.import_run_id = contract.import_run_id
            LEFT JOIN private_knowledge_coverages AS coverage
              ON coverage.knowledge_contract_id = contract.id
             AND coverage.import_run_id = contract.import_run_id
            LEFT JOIN LATERAL (
              SELECT document_identity_decision,
                     edition_applicability_decision, overall_decision
              FROM private_knowledge_terms_assignments
              WHERE import_run_id = contract.import_run_id
                AND knowledge_contract_id = contract.id
              ORDER BY id
              LIMIT 1
            ) AS assignment ON true
            WHERE {where}
            GROUP BY contract.id, subject.id,
                     assignment.document_identity_decision,
                     assignment.edition_applicability_decision,
                     assignment.overall_decision
            ORDER BY contract.id
            LIMIT %(limit)s
            """,
            parameters,
        ).fetchall()

    @staticmethod
    def _contract_item(row: dict[str, Any]) -> KnowledgeContractListItemResponse:
        return KnowledgeContractListItemResponse.model_validate(row)

    @staticmethod
    def _coverages(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
        contract_id: UUID,
    ) -> tuple[KnowledgeCoverageResponse, ...]:
        rows = connection.execute(
            """
            SELECT id, display_name, component_role, component_classification,
                   enrollment_decision, benefit_type, insured_amount, currency,
                   coverage_start, coverage_end, renewal_state, current_status
            FROM private_knowledge_coverages
            WHERE import_run_id = %s AND knowledge_contract_id = %s
            ORDER BY id
            LIMIT %s
            """,
            (run_id, contract_id, _MAX_COVERAGES + 1),
        ).fetchall()
        if len(rows) > _MAX_COVERAGES:
            raise PrivateKnowledgeQueryTooLargeError
        return tuple(KnowledgeCoverageResponse.model_validate(row) for row in rows)

    @staticmethod
    def _assignments(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
        contract_id: UUID,
    ) -> tuple[KnowledgeTermsAssignmentResponse, ...]:
        rows = connection.execute(
            """
            SELECT assignment.id, assignment.document_identity_decision,
                   assignment.edition_applicability_decision,
                   assignment.overall_decision,
                   assignment.reason_codes_json AS reason_codes,
                   count(source.id) AS selected_source_count
            FROM private_knowledge_terms_assignments AS assignment
            LEFT JOIN private_knowledge_terms_assignment_sources AS source
              ON source.terms_assignment_id = assignment.id
             AND source.import_run_id = assignment.import_run_id
            WHERE assignment.import_run_id = %s
              AND assignment.knowledge_contract_id = %s
            GROUP BY assignment.id
            ORDER BY assignment.id
            LIMIT %s
            """,
            (run_id, contract_id, _MAX_ASSIGNMENTS + 1),
        ).fetchall()
        if len(rows) > _MAX_ASSIGNMENTS:
            raise PrivateKnowledgeQueryTooLargeError
        return tuple(KnowledgeTermsAssignmentResponse.model_validate(row) for row in rows)

    @staticmethod
    def _mappings(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
        contract_id: UUID,
    ) -> tuple[KnowledgeCoverageMappingResponse, ...]:
        rows = connection.execute(
            """
            SELECT mapping.coverage_id, mapping.terms_section_id,
                   mapping.mapping_applicability, mapping.enrollment_decision,
                   mapping.document_identity_decision,
                   mapping.edition_applicability_decision,
                   mapping.section_mapping_decision, mapping.overall_decision,
                   mapping.reason_codes_json AS reason_codes, mapping.executable
            FROM private_knowledge_coverage_terms_mappings AS mapping
            JOIN private_knowledge_coverages AS coverage
              ON coverage.id = mapping.coverage_id
             AND coverage.import_run_id = mapping.import_run_id
            WHERE mapping.import_run_id = %s
              AND coverage.knowledge_contract_id = %s
            ORDER BY mapping.coverage_id, mapping.id
            LIMIT %s
            """,
            (run_id, contract_id, _MAX_MAPPINGS + 1),
        ).fetchall()
        if len(rows) > _MAX_MAPPINGS:
            raise PrivateKnowledgeQueryTooLargeError
        return tuple(KnowledgeCoverageMappingResponse.model_validate(row) for row in rows)

    @staticmethod
    def _sections(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: UUID,
        contract_id: UUID,
    ) -> tuple[KnowledgeTermsSectionResponse, ...]:
        section_rows = connection.execute(
            """
            WITH selected_aliases AS (
              SELECT source.source_alias_digest_sha256 AS digest
              FROM private_knowledge_terms_assignments AS assignment
              JOIN private_knowledge_terms_assignment_sources AS source
                ON source.terms_assignment_id = assignment.id
               AND source.import_run_id = assignment.import_run_id
              WHERE assignment.import_run_id = %(run)s
                AND assignment.knowledge_contract_id = %(contract)s
            ), selected_sections AS (
              SELECT section.id
              FROM private_knowledge_terms_sections AS section
              JOIN selected_aliases
                ON selected_aliases.digest = section.terms_source_alias_digest_sha256
              WHERE section.import_run_id = %(run)s
              UNION
              SELECT mapping.terms_section_id
              FROM private_knowledge_coverage_terms_mappings AS mapping
              JOIN private_knowledge_coverages AS coverage
                ON coverage.id = mapping.coverage_id
               AND coverage.import_run_id = mapping.import_run_id
              WHERE mapping.import_run_id = %(run)s
                AND coverage.knowledge_contract_id = %(contract)s
                AND mapping.terms_section_id IS NOT NULL
            )
            SELECT section.id, section.heading, section.page_start, section.page_end,
                   section.review_state, review.section_summary,
                   review.confidence,
                   review.found_categories_json AS found_categories,
                   review.missing_categories_json AS missing_categories,
                   review.warnings_json AS warnings
            FROM selected_sections
            JOIN private_knowledge_terms_sections AS section
              ON section.id = selected_sections.id
             AND section.import_run_id = %(run)s
            JOIN private_knowledge_semantic_reviews AS review
              ON review.terms_section_id = section.id
             AND review.import_run_id = section.import_run_id
            ORDER BY section.page_start, section.id
            LIMIT %(limit)s
            """,
            {
                "run": run_id,
                "contract": contract_id,
                "limit": _MAX_SECTIONS + 1,
            },
        ).fetchall()
        if len(section_rows) > _MAX_SECTIONS:
            raise PrivateKnowledgeQueryTooLargeError
        if not section_rows:
            return ()
        section_ids = [cast(UUID, row["id"]) for row in section_rows]
        fact_rows = connection.execute(
            """
            SELECT id, terms_section_id, fact_type, statement,
                   conditions_json AS conditions,
                   numeric_terms_json AS numeric_terms,
                   review_state, executable
            FROM private_knowledge_facts
            WHERE import_run_id = %s AND terms_section_id = ANY(%s)
            ORDER BY terms_section_id, fact_type, id
            LIMIT %s
            """,
            (run_id, section_ids, _MAX_FACTS + 1),
        ).fetchall()
        if len(fact_rows) > _MAX_FACTS:
            raise PrivateKnowledgeQueryTooLargeError
        fact_ids = [cast(UUID, row["id"]) for row in fact_rows]
        citation_rows: list[dict[str, Any]] = []
        if fact_ids:
            citation_rows = connection.execute(
                """
                SELECT citation.fact_id, citation.page_start, citation.page_end,
                       clause.clause_label, clause.title AS clause_title
                FROM private_knowledge_fact_citations AS citation
                JOIN private_knowledge_source_clauses AS clause
                  ON clause.id = citation.source_clause_id
                 AND clause.import_run_id = citation.import_run_id
                WHERE citation.import_run_id = %s AND citation.fact_id = ANY(%s)
                ORDER BY citation.fact_id, citation.citation_ordinal
                LIMIT %s
                """,
                (run_id, fact_ids, _MAX_CITATIONS + 1),
            ).fetchall()
            if len(citation_rows) > _MAX_CITATIONS:
                raise PrivateKnowledgeQueryTooLargeError
        citations_by_fact: dict[UUID, list[KnowledgeFactCitationResponse]] = defaultdict(list)
        for row in citation_rows:
            citations_by_fact[cast(UUID, row["fact_id"])].append(
                KnowledgeFactCitationResponse.model_validate(
                    {
                        "page_start": row["page_start"],
                        "page_end": row["page_end"],
                        "clause_label": row["clause_label"],
                        "clause_title": row["clause_title"],
                    }
                )
            )
        facts_by_section: dict[UUID, list[KnowledgeFactResponse]] = defaultdict(list)
        for row in fact_rows:
            fact_id = cast(UUID, row["id"])
            conditions = cast(dict[str, object], row["conditions"])
            facts_by_section[cast(UUID, row["terms_section_id"])].append(
                KnowledgeFactResponse(
                    id=fact_id,
                    fact_type=cast(Any, row["fact_type"]),
                    statement=cast(str, row["statement"]),
                    conditions=KnowledgeFactConditionsResponse.model_validate(conditions),
                    numeric_terms=tuple(cast(list[str], row["numeric_terms"])),
                    review_state=cast(Any, row["review_state"]),
                    executable=False,
                    citations=tuple(citations_by_fact[fact_id]),
                )
            )
        return tuple(
            KnowledgeTermsSectionResponse(
                id=cast(UUID, row["id"]),
                heading=cast(str, row["heading"]),
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
                review_state=cast(Any, row["review_state"]),
                section_summary=cast(str, row["section_summary"]),
                confidence=cast(Any, row["confidence"]),
                found_categories=tuple(cast(list[str], row["found_categories"])),
                missing_categories=tuple(cast(list[str], row["missing_categories"])),
                warnings=tuple(cast(list[str], row["warnings"])),
                facts=tuple(facts_by_section[cast(UUID, row["id"])]),
            )
            for row in section_rows
        )

"""PostgreSQL projection for member-scoped related-clause recommendations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

import psycopg

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.assistance import (
    AnalysisAssistance,
    AnalysisAssistanceNotFound,
    AnalysisRecommendation,
    candidate_digest,
    normalize_search_tokens,
)
from familycare_api.decisions.domain import MedicalEvent

_RECOMMENDATION_NAMESPACE = UUID("3f61380f-2fda-5fb2-9f56-1721f89c52b9")


class AnalysisAssistanceRepository:
    """Create and load immutable assistance without changing verified decisions."""

    def create_search_projection(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        decision_run_id: UUID,
    ) -> AnalysisAssistance:
        fact_values = tuple(
            value.value for _, value in sorted(event.facts.items()) if isinstance(value.value, str)
        )
        tokens = normalize_search_tokens(event.situation, fact_values)
        rows = self._search_rows(
            connection,
            scope,
            event,
            decision_run_id,
            tokens,
        )
        recommendations = tuple(
            _recommendation(row, rank=index) for index, row in enumerate(rows, start=1)
        )
        digest = candidate_digest(recommendations)
        has_candidates = bool(recommendations)
        job_row = connection.execute(
            """
            INSERT INTO analysis_assistance_jobs (
              household_space_id, medical_event_id, event_version,
              candidate_digest_sha256, state, attempts, outcome_code,
              completed_at
            ) VALUES (
              %s, %s, %s, %s, %s, 0, %s,
              CASE WHEN %s = 'SUCCEEDED' THEN clock_timestamp() END
            )
            ON CONFLICT (
              household_space_id, medical_event_id, event_version,
              candidate_digest_sha256
            ) DO UPDATE SET id = analysis_assistance_jobs.id
            RETURNING id, state
            """,
            (
                scope.household_space_id,
                event.id,
                event.version,
                digest,
                "QUEUED" if has_candidates else "SUCCEEDED",
                None if has_candidates else "NO_SEARCH_CANDIDATES",
                "QUEUED" if has_candidates else "SUCCEEDED",
            ),
        ).fetchone()
        if job_row is None:
            raise ValueError("analysis assistance job was not persisted")

        job_state = cast(str, job_row["state"])
        mode = "STRUCTURED_SEARCH" if has_candidates else "NONE"
        state = (
            "LLM_PENDING"
            if has_candidates and job_state in {"QUEUED", "RUNNING"}
            else "SEARCH_READY"
        )
        outcome_code = "LOCAL_SEARCH_READY" if has_candidates else "NO_SEARCH_CANDIDATES"
        assistance_id = uuid4()
        run_row = connection.execute(
            """
            INSERT INTO analysis_assistance_runs (
              id, analysis_job_id, household_space_id, medical_event_id,
              decision_run_id, event_version, candidate_digest_sha256,
              mode, state, outcome_code
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (decision_run_id, mode, state) DO NOTHING
            RETURNING id, created_at
            """,
            (
                assistance_id,
                cast(UUID, job_row["id"]),
                scope.household_space_id,
                event.id,
                decision_run_id,
                event.version,
                digest,
                mode,
                state,
                outcome_code,
            ),
        ).fetchone()
        if run_row is None:
            return self.get_latest(connection, scope, decision_run_id)

        for recommendation in recommendations:
            source_row = rows[recommendation.rank - 1]
            connection.execute(
                """
                INSERT INTO analysis_recommendations (
                  id, analysis_assistance_run_id, household_space_id,
                  decision_run_id, private_claim_candidate_id,
                  knowledge_import_run_id, knowledge_coverage_id,
                  coverage_execution_disposition_id,
                  enrollment_decision_snapshot, enrollment_authority_snapshot,
                  terms_section_id, knowledge_fact_id, source_clause_id,
                  fact_citation_id, candidate_digest_sha256, rank, score,
                  contract_label_snapshot, coverage_label_snapshot,
                  clause_label_snapshot, excerpt, page_start, page_end,
                  citation_kind, reason_code
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    recommendation.id,
                    cast(UUID, run_row["id"]),
                    scope.household_space_id,
                    decision_run_id,
                    recommendation.private_claim_candidate_id,
                    cast(UUID, source_row["knowledge_import_run_id"]),
                    recommendation.knowledge_coverage_id,
                    cast(UUID, source_row["coverage_execution_disposition_id"]),
                    cast(str, source_row["enrollment_decision_snapshot"]),
                    cast(str, source_row["enrollment_authority_snapshot"]),
                    recommendation.terms_section_id,
                    recommendation.knowledge_fact_id,
                    recommendation.source_clause_id,
                    recommendation.fact_citation_id,
                    digest,
                    recommendation.rank,
                    recommendation.score,
                    recommendation.contract_label,
                    recommendation.coverage_label,
                    recommendation.clause_label,
                    recommendation.excerpt,
                    recommendation.page_start,
                    recommendation.page_end,
                    recommendation.citation_kind,
                    recommendation.reason_code,
                ),
            )
        return AnalysisAssistance(
            run_id=cast(UUID, run_row["id"]),
            job_id=cast(UUID, job_row["id"]),
            decision_run_id=decision_run_id,
            event_version=event.version,
            candidate_digest_sha256=digest,
            mode=cast(Any, mode),
            state=cast(Any, state),
            outcome_code=outcome_code,
            recommendations=recommendations,
            created_at=cast(datetime, run_row["created_at"]),
        )

    def get_latest(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        decision_run_id: UUID,
    ) -> AnalysisAssistance:
        run = connection.execute(
            """
            SELECT assistance.*
            FROM analysis_assistance_runs AS assistance
            JOIN decision_runs AS decision
              ON decision.id = assistance.decision_run_id
             AND decision.household_space_id = assistance.household_space_id
            WHERE assistance.household_space_id = %s
              AND assistance.decision_run_id = %s
            ORDER BY assistance.created_at DESC,
                     CASE assistance.mode WHEN 'LLM_ASSISTED' THEN 0 ELSE 1 END,
                     assistance.id DESC
            LIMIT 1
            """,
            (scope.household_space_id, decision_run_id),
        ).fetchone()
        if run is None:
            raise AnalysisAssistanceNotFound
        recommendation_rows = connection.execute(
            """
            SELECT recommendation.*,
                   recommendation.contract_label_snapshot AS contract_label,
                   recommendation.coverage_label_snapshot AS coverage_label,
                   recommendation.clause_label_snapshot AS clause_label
            FROM analysis_recommendations AS recommendation
            WHERE recommendation.analysis_assistance_run_id = %s
              AND recommendation.household_space_id = %s
              AND recommendation.decision_run_id = %s
            ORDER BY recommendation.rank, recommendation.id
            """,
            (run["id"], scope.household_space_id, decision_run_id),
        ).fetchall()
        recommendations = tuple(
            _recommendation(row, rank=int(row["rank"])) for row in recommendation_rows
        )
        return AnalysisAssistance(
            run_id=cast(UUID, run["id"]),
            job_id=cast(UUID, run["analysis_job_id"]),
            decision_run_id=cast(UUID, run["decision_run_id"]),
            event_version=int(run["event_version"]),
            candidate_digest_sha256=cast(str, run["candidate_digest_sha256"]),
            mode=cast(Any, run["mode"]),
            state=cast(Any, run["state"]),
            outcome_code=cast(str, run["outcome_code"]),
            recommendations=recommendations,
            created_at=cast(datetime, run["created_at"]),
            provider_label=cast(str | None, run.get("provider_label")),
            model_label=cast(str | None, run.get("model_label")),
            config_version=cast(str | None, run.get("config_version")),
        )

    def _search_rows(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        decision_run_id: UUID,
        tokens: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if not tokens:
            return []
        return list(
            connection.execute(
                """
                WITH query_tokens AS (
                  SELECT DISTINCT unnest(%s::text[]) AS token
                ), scoped_coverages AS (
                  SELECT candidate.id AS private_claim_candidate_id,
                         import_run.id AS knowledge_import_run_id,
                         coverage.id AS knowledge_coverage_id,
                         contract.id AS knowledge_contract_id,
                         decision.household_space_id,
                         disposition.id AS coverage_execution_disposition_id,
                         disposition.disposition AS execution_disposition,
                         coverage.enrollment_decision AS enrollment_decision_snapshot,
                         disposition.enrollment_authority AS enrollment_authority_snapshot,
                         candidate.contract_label_snapshot AS contract_label,
                         candidate.coverage_label_snapshot AS coverage_label
                  FROM decision_runs AS decision
                  JOIN medical_events AS event
                    ON event.id = decision.medical_event_id
                   AND event.household_space_id = decision.household_space_id
                  JOIN private_knowledge_import_runs AS import_run
                    ON import_run.id = decision.knowledge_import_run_id
                   AND import_run.household_space_id = decision.household_space_id
                   AND import_run.state = 'APPLIED'
                   AND import_run.is_current = true
                  JOIN private_knowledge_subjects AS subject
                    ON subject.import_run_id = import_run.id
                   AND subject.household_space_id = decision.household_space_id
                   AND subject.family_member_id = event.family_member_id
                   AND subject.binding_decision = 'MATCH'
                   AND subject.binding_conflict = false
                  JOIN private_knowledge_contracts AS contract
                    ON contract.import_run_id = import_run.id
                   AND contract.household_space_id = decision.household_space_id
                   AND contract.subject_id = subject.id
                   AND contract.certificate_decision = 'MATCH'
                  JOIN private_knowledge_claim_candidates AS candidate
                    ON candidate.decision_run_id = decision.id
                   AND candidate.household_space_id = decision.household_space_id
                   AND candidate.knowledge_import_run_id = import_run.id
                   AND candidate.knowledge_contract_id = contract.id
                  JOIN private_knowledge_coverages AS coverage
                    ON coverage.id = candidate.knowledge_coverage_id
                   AND coverage.import_run_id = import_run.id
                   AND coverage.knowledge_contract_id = contract.id
                   AND coverage.component_classification = 'BENEFIT_COVERAGE'
                  JOIN private_knowledge_coverage_execution_dispositions AS disposition
                    ON disposition.rule_import_run_id = decision.knowledge_rule_import_run_id
                   AND disposition.knowledge_import_run_id = import_run.id
                   AND disposition.household_space_id = decision.household_space_id
                   AND disposition.knowledge_coverage_id = coverage.id
                  WHERE decision.id = %s
                    AND candidate.decision_run_id = %s
                    AND decision.household_space_id = %s
                    AND decision.medical_event_id = %s
                    AND decision.event_version = %s
                    AND event.family_member_id = %s
                    AND subject.family_member_id = %s
                    AND event.deleted_at IS NULL
                    AND ((coverage.enrollment_decision = 'MATCH'
                          AND disposition.enrollment_decision_snapshot = 'MATCH'
                          AND disposition.enrollment_authority = 'CERTIFICATE_SNAPSHOT'
                          AND disposition.disposition IN ('PUBLISHED', 'ADVISORY'))
                         OR (coverage.enrollment_decision = 'UNKNOWN'
                          AND disposition.enrollment_decision_snapshot = 'UNKNOWN'
                          AND disposition.disposition = 'ADVISORY'
                          AND disposition.enrollment_authority =
                              'USER_CONFIRMED_COVERAGE_ENROLLMENT'
                          AND disposition.enrollment_reason_code =
                              'USER_CONFIRMED_COVERAGE_ENROLLMENT'
                          AND disposition.enrollment_confirmed_by IS NOT NULL))
                ), reviewed_facts AS (
                  SELECT section.import_run_id,
                         section.id AS terms_section_id,
                         section.terms_source_alias_digest_sha256,
                         section.heading,
                         fact.id AS knowledge_fact_id,
                         fact.statement,
                         semantic.section_summary,
                         clause.id AS source_clause_id,
                         clause.clause_label AS source_clause_label,
                         clause.title AS source_clause_title,
                         citation.id AS fact_citation_id,
                         citation.page_start,
                         LEAST(citation.page_end, citation.page_start + 20) AS page_end
                  FROM private_knowledge_terms_sections AS section
                  JOIN (
                    SELECT DISTINCT knowledge_import_run_id
                    FROM scoped_coverages
                  ) AS scoped_run
                    ON scoped_run.knowledge_import_run_id = section.import_run_id
                  JOIN private_knowledge_facts AS fact
                    ON fact.import_run_id = section.import_run_id
                   AND fact.terms_section_id = section.id
                   AND fact.review_state IN ('DIRECT_REVIEWED', 'USER_CONFIRMED')
                  JOIN private_knowledge_semantic_reviews AS semantic
                    ON semantic.id = fact.semantic_review_id
                   AND semantic.import_run_id = section.import_run_id
                   AND semantic.terms_section_id = section.id
                   AND semantic.review_state = 'DIRECT_REVIEWED'
                  JOIN private_knowledge_fact_citations AS citation
                    ON citation.import_run_id = section.import_run_id
                   AND citation.fact_id = fact.id
                  JOIN private_knowledge_source_clauses AS clause
                    ON clause.id = citation.source_clause_id
                   AND clause.import_run_id = section.import_run_id
                   AND clause.terms_section_id = section.id
                   AND clause.review_state IN ('DIRECT_REVIEWED', 'USER_CONFIRMED')
                  WHERE section.review_state IN ('DIRECT_REVIEWED', 'USER_CONFIRMED')
                ), exact_mapping_candidates AS (
                  SELECT DISTINCT scoped.private_claim_candidate_id,
                         scoped.knowledge_import_run_id,
                         scoped.knowledge_coverage_id,
                         scoped.coverage_execution_disposition_id,
                         scoped.enrollment_decision_snapshot,
                         scoped.enrollment_authority_snapshot,
                         reviewed.terms_section_id,
                         reviewed.knowledge_fact_id,
                         reviewed.source_clause_id,
                         reviewed.fact_citation_id,
                         scoped.contract_label,
                         scoped.coverage_label,
                         COALESCE(NULLIF(reviewed.source_clause_label, ''),
                                  NULLIF(reviewed.source_clause_title, ''),
                                  reviewed.heading) AS clause_label,
                         left(regexp_replace(reviewed.statement, '\\s+', ' ', 'g'), 240)
                           AS excerpt,
                         reviewed.page_start,
                         reviewed.page_end,
                         'FACT_CITATION'::text AS citation_kind,
                         'TOKEN_OVERLAP'::text AS reason_code,
                         reviewed.heading,
                         reviewed.section_summary,
                         reviewed.statement,
                         reviewed.source_clause_label,
                         reviewed.source_clause_title,
                         2::integer AS association_priority
                  FROM scoped_coverages AS scoped
                  JOIN private_knowledge_coverage_terms_mappings AS mapping
                    ON mapping.import_run_id = scoped.knowledge_import_run_id
                   AND mapping.coverage_id = scoped.knowledge_coverage_id
                   AND mapping.mapping_applicability = 'APPLICABLE'
                   AND mapping.enrollment_decision = 'MATCH'
                   AND mapping.document_identity_decision = 'MATCH'
                   AND mapping.edition_applicability_decision = 'MATCH'
                   AND mapping.section_mapping_decision = 'MATCH'
                   AND mapping.overall_decision = 'MATCH'
                  JOIN reviewed_facts AS reviewed
                    ON reviewed.import_run_id = scoped.knowledge_import_run_id
                   AND reviewed.terms_section_id = mapping.terms_section_id
                  WHERE scoped.execution_disposition IN ('PUBLISHED', 'ADVISORY')
                ), contract_terms_candidates AS (
                  SELECT DISTINCT scoped.private_claim_candidate_id,
                         scoped.knowledge_import_run_id,
                         scoped.knowledge_coverage_id,
                         scoped.coverage_execution_disposition_id,
                         scoped.enrollment_decision_snapshot,
                         scoped.enrollment_authority_snapshot,
                         reviewed.terms_section_id,
                         reviewed.knowledge_fact_id,
                         reviewed.source_clause_id,
                         reviewed.fact_citation_id,
                         scoped.contract_label,
                         scoped.coverage_label,
                         COALESCE(NULLIF(reviewed.source_clause_label, ''),
                                  NULLIF(reviewed.source_clause_title, ''),
                                  reviewed.heading) AS clause_label,
                         left(regexp_replace(reviewed.statement, '\\s+', ' ', 'g'), 240)
                           AS excerpt,
                         reviewed.page_start,
                         reviewed.page_end,
                         'FACT_CITATION'::text AS citation_kind,
                         'CONTRACT_TERMS_TOKEN_OVERLAP'::text AS reason_code,
                         reviewed.heading,
                         reviewed.section_summary,
                         reviewed.statement,
                         reviewed.source_clause_label,
                         reviewed.source_clause_title,
                         1::integer AS association_priority
                  FROM scoped_coverages AS scoped
                  JOIN private_knowledge_terms_assignments AS assignment
                    ON assignment.import_run_id = scoped.knowledge_import_run_id
                   AND assignment.household_space_id = scoped.household_space_id
                   AND assignment.knowledge_contract_id = scoped.knowledge_contract_id
                   AND assignment.document_identity_decision = 'MATCH'
                   AND assignment.edition_applicability_decision = 'MATCH'
                   AND assignment.overall_decision = 'MATCH'
                  JOIN private_knowledge_terms_assignment_sources AS assignment_source
                    ON assignment_source.import_run_id = scoped.knowledge_import_run_id
                   AND assignment_source.terms_assignment_id = assignment.id
                  JOIN reviewed_facts AS reviewed
                    ON reviewed.import_run_id = scoped.knowledge_import_run_id
                   AND reviewed.terms_source_alias_digest_sha256 =
                       assignment_source.source_alias_digest_sha256
                  WHERE scoped.execution_disposition = 'ADVISORY'
                    AND NOT EXISTS (
                    SELECT 1
                    FROM private_knowledge_coverage_terms_mappings AS exact_mapping
                    WHERE exact_mapping.import_run_id = scoped.knowledge_import_run_id
                      AND exact_mapping.coverage_id = scoped.knowledge_coverage_id
                      AND exact_mapping.mapping_applicability = 'APPLICABLE'
                      AND exact_mapping.enrollment_decision = 'MATCH'
                      AND exact_mapping.document_identity_decision = 'MATCH'
                      AND exact_mapping.edition_applicability_decision = 'MATCH'
                      AND exact_mapping.section_mapping_decision = 'MATCH'
                      AND exact_mapping.overall_decision = 'MATCH'
                  )
                ), associated_candidates AS (
                  SELECT * FROM exact_mapping_candidates
                  UNION ALL
                  SELECT * FROM contract_terms_candidates
                ), scored_candidates AS (
                  SELECT associated.*,
                         (
                           SELECT count(*)
                           FROM query_tokens AS query_token
                           WHERE query_token.token = ANY(
                             regexp_split_to_array(
                               lower(associated.coverage_label),
                               '[^[:alnum:]_]+'
                             )
                           )
                         ) AS coverage_score,
                         (
                           SELECT count(*)
                           FROM query_tokens AS query_token
                           WHERE query_token.token = ANY(
                             regexp_split_to_array(
                               lower(concat_ws(
                                 ' ', associated.heading, associated.section_summary,
                                 associated.statement, associated.source_clause_label,
                                 associated.source_clause_title
                               )),
                               '[^[:alnum:]_]+'
                             )
                           )
                         ) AS clause_score,
                         (
                           SELECT count(*)
                           FROM query_tokens AS query_token
                           WHERE query_token.token = ANY(
                             regexp_split_to_array(
                               lower(concat_ws(
                                 ' ', associated.coverage_label,
                                 associated.heading, associated.section_summary,
                                 associated.statement, associated.source_clause_label,
                                 associated.source_clause_title
                               )),
                               '[^[:alnum:]_]+'
                             )
                           )
                         ) AS score
                  FROM associated_candidates AS associated
                ), ranked_candidates AS (
                  SELECT scored.*,
                         row_number() OVER (
                           PARTITION BY knowledge_coverage_id, association_priority
                           ORDER BY score DESC, terms_section_id, fact_citation_id
                         ) AS coverage_rank
                  FROM scored_candidates AS scored
                  WHERE (
                    association_priority = 2 AND score > 0
                  ) OR (
                    association_priority = 1
                    AND coverage_score > 0
                    AND clause_score > 0
                  )
                )
                SELECT private_claim_candidate_id, knowledge_import_run_id,
                       knowledge_coverage_id, coverage_execution_disposition_id,
                       enrollment_decision_snapshot, enrollment_authority_snapshot,
                       terms_section_id, knowledge_fact_id,
                       source_clause_id, fact_citation_id, contract_label,
                       coverage_label, clause_label, excerpt, page_start, page_end,
                       citation_kind, reason_code, score
                FROM ranked_candidates
                WHERE coverage_rank <= 2
                ORDER BY association_priority DESC, coverage_rank,
                         score DESC, knowledge_coverage_id, terms_section_id,
                         fact_citation_id
                LIMIT 12
                """,
                (
                    list(tokens),
                    decision_run_id,
                    decision_run_id,
                    scope.household_space_id,
                    event.id,
                    event.version,
                    event.family_member_id,
                    event.family_member_id,
                ),
            ).fetchall()
        )


def _recommendation(row: dict[str, Any], *, rank: int) -> AnalysisRecommendation:
    identity = ":".join(
        str(row[key])
        for key in (
            "private_claim_candidate_id",
            "knowledge_coverage_id",
            "terms_section_id",
            "knowledge_fact_id",
            "source_clause_id",
            "fact_citation_id",
        )
    )
    recommendation_id = row.get("id")
    if not isinstance(recommendation_id, UUID):
        recommendation_id = uuid5(_RECOMMENDATION_NAMESPACE, identity)
    return AnalysisRecommendation(
        id=recommendation_id,
        private_claim_candidate_id=cast(UUID, row["private_claim_candidate_id"]),
        knowledge_coverage_id=cast(UUID, row["knowledge_coverage_id"]),
        terms_section_id=cast(UUID, row["terms_section_id"]),
        knowledge_fact_id=cast(UUID, row["knowledge_fact_id"]),
        source_clause_id=cast(UUID, row["source_clause_id"]),
        fact_citation_id=cast(UUID, row["fact_citation_id"]),
        rank=rank,
        score=Decimal(str(row["score"])),
        contract_label=cast(str, row["contract_label"]),
        coverage_label=cast(str, row["coverage_label"]),
        clause_label=cast(str, row["clause_label"]),
        excerpt=cast(str, row["excerpt"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        citation_kind="FACT_CITATION",
        reason_code=cast(str, row["reason_code"]),
        explanation_code=cast(str | None, row.get("explanation_code")),
        question_code=cast(str | None, row.get("question_code")),
    )


__all__ = ["AnalysisAssistanceRepository"]

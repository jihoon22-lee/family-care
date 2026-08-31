"""At-most-once queue and sanitized persistence for recommendation refinement."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from familycare_worker.ai.recommender import RecommendationResult

JobState = Literal["RUNNING"]
FactConfirmation = Literal["USER_CONFIRMED", "AI_SUGGESTED"]
EnrollmentDecision = Literal["MATCH", "UNKNOWN"]
EnrollmentAuthority = Literal[
    "CERTIFICATE_SNAPSHOT",
    "USER_CONFIRMED_COVERAGE_ENROLLMENT",
]

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class RecommendationQueueUnavailable(RuntimeError):
    """Fixed-message queue failure without SQL or private values."""

    def __init__(self) -> None:
        super().__init__("RECOMMENDATION_QUEUE_UNAVAILABLE")


class InvalidRecommendationWork(RuntimeError):
    """The local candidates no longer match their immutable digest."""

    def __init__(self) -> None:
        super().__init__("INVALID_LOCAL_RECOMMENDATIONS")


@dataclass(frozen=True)
class RecommendationJobRecord:
    id: UUID = field(repr=False)
    household_space_id: UUID = field(repr=False)
    medical_event_id: UUID = field(repr=False)
    event_version: int
    candidate_digest_sha256: str = field(repr=False)
    state: JobState
    attempts: int

    def __post_init__(self) -> None:
        for value in (self.id, self.household_space_id, self.medical_event_id):
            _require_uuid(value)
        if isinstance(self.event_version, bool) or self.event_version < 1:
            raise InvalidRecommendationWork
        if not _DIGEST_PATTERN.fullmatch(self.candidate_digest_sha256):
            raise InvalidRecommendationWork
        if self.state != "RUNNING" or self.attempts != 1:
            raise InvalidRecommendationWork


@dataclass(frozen=True)
class LocalRecommendationRecord:
    id: UUID = field(repr=False)
    private_claim_candidate_id: UUID = field(repr=False)
    knowledge_import_run_id: UUID = field(repr=False)
    knowledge_coverage_id: UUID = field(repr=False)
    coverage_execution_disposition_id: UUID = field(repr=False)
    enrollment_decision_snapshot: EnrollmentDecision = field(repr=False)
    enrollment_authority_snapshot: EnrollmentAuthority = field(repr=False)
    terms_section_id: UUID = field(repr=False)
    knowledge_fact_id: UUID = field(repr=False)
    source_clause_id: UUID = field(repr=False)
    fact_citation_id: UUID = field(repr=False)
    candidate_digest_sha256: str = field(repr=False)
    rank: int = field(repr=False)
    score: Decimal = field(repr=False)
    contract_label: str = field(repr=False)
    coverage_label: str = field(repr=False)
    clause_label: str = field(repr=False)
    excerpt: str = field(repr=False)
    page_start: int = field(repr=False)
    page_end: int = field(repr=False)
    citation_kind: Literal["FACT_CITATION"] = field(repr=False)
    reason_code: str = field(repr=False)

    def __post_init__(self) -> None:
        for value in (
            self.id,
            self.private_claim_candidate_id,
            self.knowledge_import_run_id,
            self.knowledge_coverage_id,
            self.coverage_execution_disposition_id,
            self.terms_section_id,
            self.knowledge_fact_id,
            self.source_clause_id,
            self.fact_citation_id,
        ):
            _require_uuid(value)
        if not _DIGEST_PATTERN.fullmatch(self.candidate_digest_sha256):
            raise InvalidRecommendationWork
        if (
            self.enrollment_decision_snapshot,
            self.enrollment_authority_snapshot,
        ) not in {
            ("MATCH", "CERTIFICATE_SNAPSHOT"),
            ("UNKNOWN", "USER_CONFIRMED_COVERAGE_ENROLLMENT"),
        }:
            raise InvalidRecommendationWork
        if isinstance(self.rank, bool) or not 1 <= self.rank <= 12 or self.score < 0:
            raise InvalidRecommendationWork
        if not all(
            isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            for value, maximum in (
                (self.contract_label, 240),
                (self.coverage_label, 800),
                (self.clause_label, 800),
                (self.excerpt, 240),
            )
        ):
            raise InvalidRecommendationWork
        if (
            self.page_start < 1
            or self.page_end < self.page_start
            or self.page_end - self.page_start > 20
            or self.citation_kind != "FACT_CITATION"
            or not _CODE_PATTERN.fullmatch(self.reason_code)
        ):
            raise InvalidRecommendationWork

    @property
    def stable_identity(self) -> tuple[object, ...]:
        """Identity shared by repeated decision runs without their row IDs."""

        return (
            self.knowledge_coverage_id,
            self.coverage_execution_disposition_id,
            self.enrollment_decision_snapshot,
            self.enrollment_authority_snapshot,
            self.terms_section_id,
            self.knowledge_fact_id,
            self.source_clause_id,
            self.fact_citation_id,
            self.score,
            self.contract_label,
            self.coverage_label,
            self.clause_label,
            self.excerpt,
            self.page_start,
            self.page_end,
            self.citation_kind,
        )


@dataclass(frozen=True)
class RecommendationTarget:
    assistance_run_id: UUID = field(repr=False)
    decision_run_id: UUID = field(repr=False)
    event_version: int
    recommendations: tuple[LocalRecommendationRecord, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.assistance_run_id)
        _require_uuid(self.decision_run_id)
        if isinstance(self.event_version, bool) or self.event_version < 1:
            raise InvalidRecommendationWork
        if not 1 <= len(self.recommendations) <= 12:
            raise InvalidRecommendationWork
        if tuple(item.rank for item in self.recommendations) != tuple(
            range(1, len(self.recommendations) + 1)
        ):
            raise InvalidRecommendationWork


@dataclass(frozen=True)
class RecommendationWorkItem:
    job: RecommendationJobRecord = field(repr=False)
    situation: str = field(repr=False)
    facts: tuple[tuple[str, str, FactConfirmation], ...] = field(repr=False)
    targets: tuple[RecommendationTarget, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.job, RecommendationJobRecord):
            raise InvalidRecommendationWork
        if not isinstance(self.situation, str) or not self.situation.strip():
            raise InvalidRecommendationWork
        if len(self.facts) > 32:
            raise InvalidRecommendationWork
        if any(target.event_version != self.job.event_version for target in self.targets):
            raise InvalidRecommendationWork
        if not self.targets:
            return
        expected = tuple(item.stable_identity for item in self.targets[0].recommendations)
        if any(
            tuple(item.stable_identity for item in target.recommendations) != expected
            for target in self.targets[1:]
        ):
            raise InvalidRecommendationWork
        if any(
            item.candidate_digest_sha256 != self.job.candidate_digest_sha256
            for target in self.targets
            for item in target.recommendations
        ):
            raise InvalidRecommendationWork


class RecommendationQueue(Protocol):
    def claim_next_job(self, worker_id: str) -> RecommendationJobRecord | None: ...
    def load_work(self, job: RecommendationJobRecord) -> RecommendationWorkItem: ...
    def complete_with_fallback(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem | None,
        outcome_code: str,
    ) -> None: ...
    def complete_with_llm(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem,
        result: RecommendationResult,
        *,
        provider_label: str,
        model_label: str,
        config_version: str,
    ) -> None: ...


class PostgresRecommendationJobQueue:
    """Claim each event/digest job once and append sanitized result projections."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise RecommendationQueueUnavailable
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def claim_next_job(self, worker_id: str) -> RecommendationJobRecord | None:
        if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 120:
            raise ValueError("invalid worker identity")
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute(
                    """
                    UPDATE analysis_assistance_jobs AS job
                    SET state = 'SUCCEEDED', outcome_code = 'EVENT_VERSION_CHANGED',
                        completed_at = clock_timestamp()
                    FROM medical_events AS event
                    WHERE job.medical_event_id = event.id
                      AND job.household_space_id = event.household_space_id
                      AND job.state = 'QUEUED' AND job.attempts = 0
                      AND (event.deleted_at IS NOT NULL
                           OR event.version <> job.event_version)
                    """
                )
                row = connection.execute(
                    """
                    WITH picked AS (
                      SELECT job.id
                      FROM analysis_assistance_jobs AS job
                      JOIN medical_events AS event
                        ON event.id = job.medical_event_id
                       AND event.household_space_id = job.household_space_id
                       AND event.version = job.event_version
                       AND event.deleted_at IS NULL
                      WHERE job.state = 'QUEUED' AND job.attempts = 0
                      ORDER BY job.created_at, job.id
                      FOR UPDATE OF job SKIP LOCKED
                      LIMIT 1
                    )
                    UPDATE analysis_assistance_jobs AS job
                    SET state = 'RUNNING', attempts = 1,
                        claimed_at = clock_timestamp()
                    FROM picked
                    WHERE job.id = picked.id
                    RETURNING job.*
                    """
                ).fetchone()
        except psycopg.Error:
            raise RecommendationQueueUnavailable from None
        return _job(row) if row is not None else None

    def load_work(self, job: RecommendationJobRecord) -> RecommendationWorkItem:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                event = connection.execute(
                    """
                    SELECT event.situation_text, event.facts_json,
                           event.confirmation_json
                    FROM analysis_assistance_jobs AS job
                    JOIN medical_events AS event
                      ON event.id = job.medical_event_id
                     AND event.household_space_id = job.household_space_id
                     AND event.version = job.event_version
                     AND event.deleted_at IS NULL
                    WHERE job.id = %s AND job.household_space_id = %s
                      AND job.state = 'RUNNING' AND job.attempts = 1
                      AND job.candidate_digest_sha256 = %s
                    """,
                    (job.id, job.household_space_id, job.candidate_digest_sha256),
                ).fetchone()
                if event is None:
                    raise InvalidRecommendationWork
                target_rows = connection.execute(
                    """
                    SELECT assistance.id, assistance.decision_run_id,
                           assistance.event_version
                    FROM analysis_assistance_runs AS assistance
                    WHERE assistance.analysis_job_id = %s
                      AND assistance.household_space_id = %s
                      AND assistance.candidate_digest_sha256 = %s
                      AND assistance.mode = 'STRUCTURED_SEARCH'
                      AND assistance.state = 'LLM_PENDING'
                    ORDER BY assistance.created_at, assistance.id
                    """,
                    (job.id, job.household_space_id, job.candidate_digest_sha256),
                ).fetchall()
                run_ids = [cast(UUID, row["id"]) for row in target_rows]
                recommendation_rows = (
                    connection.execute(
                        """
                        SELECT recommendation.*
                        FROM analysis_recommendations AS recommendation
                        WHERE recommendation.analysis_assistance_run_id = ANY(%s)
                          AND recommendation.household_space_id = %s
                          AND recommendation.candidate_digest_sha256 = %s
                        ORDER BY recommendation.analysis_assistance_run_id,
                                 recommendation.rank, recommendation.id
                        """,
                        (run_ids, job.household_space_id, job.candidate_digest_sha256),
                    ).fetchall()
                    if run_ids
                    else []
                )
        except psycopg.Error:
            raise RecommendationQueueUnavailable from None
        grouped: dict[UUID, list[LocalRecommendationRecord]] = {run_id: [] for run_id in run_ids}
        for row in recommendation_rows:
            grouped[cast(UUID, row["analysis_assistance_run_id"])].append(_local(row))
        targets = tuple(
            RecommendationTarget(
                assistance_run_id=cast(UUID, row["id"]),
                decision_run_id=cast(UUID, row["decision_run_id"]),
                event_version=int(row["event_version"]),
                recommendations=tuple(grouped[cast(UUID, row["id"])]),
            )
            for row in target_rows
        )
        return RecommendationWorkItem(
            job=job,
            situation=cast(str, event["situation_text"]),
            facts=_facts(event.get("facts_json"), event.get("confirmation_json")),
            targets=targets,
        )

    def complete_with_fallback(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem | None,
        outcome_code: str,
    ) -> None:
        self._complete(
            job,
            work,
            result=None,
            outcome_code=outcome_code,
            provider_label=None,
            model_label=None,
            config_version=None,
        )

    def complete_with_llm(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem,
        result: RecommendationResult,
        *,
        provider_label: str,
        model_label: str,
        config_version: str,
    ) -> None:
        self._complete(
            job,
            work,
            result=result,
            outcome_code="LLM_RECOMMENDATIONS_READY",
            provider_label=provider_label,
            model_label=model_label,
            config_version=config_version,
        )

    def _complete(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem | None,
        *,
        result: RecommendationResult | None,
        outcome_code: str,
        provider_label: str | None,
        model_label: str | None,
        config_version: str | None,
    ) -> None:
        if not _CODE_PATTERN.fullmatch(outcome_code):
            raise ValueError("invalid recommendation outcome code")
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                locked = connection.execute(
                    """
                    SELECT id FROM analysis_assistance_jobs
                    WHERE id = %s AND household_space_id = %s
                      AND state = 'RUNNING' AND attempts = 1
                    FOR UPDATE
                    """,
                    (job.id, job.household_space_id),
                ).fetchone()
                if locked is None:
                    raise InvalidRecommendationWork
                if work is not None:
                    for target in work.targets:
                        self._append_projection(
                            connection,
                            job,
                            target,
                            result=result,
                            outcome_code=outcome_code,
                            provider_label=provider_label,
                            model_label=model_label,
                            config_version=config_version,
                        )
                updated = connection.execute(
                    """
                    UPDATE analysis_assistance_jobs
                    SET state = 'SUCCEEDED', outcome_code = %s,
                        completed_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND state = 'RUNNING' AND attempts = 1
                    RETURNING id
                    """,
                    (outcome_code, job.id, job.household_space_id),
                ).fetchone()
                if updated is None:
                    raise InvalidRecommendationWork
        except psycopg.Error:
            raise RecommendationQueueUnavailable from None

    def _append_projection(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        job: RecommendationJobRecord,
        target: RecommendationTarget,
        *,
        result: RecommendationResult | None,
        outcome_code: str,
        provider_label: str | None,
        model_label: str | None,
        config_version: str | None,
    ) -> None:
        mode = "LLM_ASSISTED" if result is not None else "STRUCTURED_SEARCH"
        state = "LLM_READY" if result is not None else "SEARCH_READY"
        assistance_run_id = uuid4()
        run = connection.execute(
            """
            INSERT INTO analysis_assistance_runs (
              id, analysis_job_id, household_space_id, medical_event_id,
              decision_run_id, event_version, candidate_digest_sha256,
              mode, state, provider_label, provider_request_id,
              model_label, config_version, outcome_code
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (decision_run_id, mode, state) DO NOTHING
            RETURNING id
            """,
            (
                assistance_run_id,
                job.id,
                job.household_space_id,
                job.medical_event_id,
                target.decision_run_id,
                target.event_version,
                job.candidate_digest_sha256,
                mode,
                state,
                provider_label,
                result.request_id if result is not None else None,
                model_label,
                config_version,
                outcome_code,
            ),
        ).fetchone()
        if run is None:
            return
        selected: tuple[tuple[LocalRecommendationRecord, str | None, str | None], ...]
        if result is None:
            selected = tuple((item, None, None) for item in target.recommendations)
        else:
            selected = tuple(
                (
                    target.recommendations[int(selection.token[-2:]) - 1],
                    selection.explanation_code,
                    selection.question_code,
                )
                for selection in result.selections
            )
        for rank, (item, explanation_code, question_code) in enumerate(selected, start=1):
            connection.execute(
                """
                INSERT INTO analysis_recommendations (
                  id, analysis_assistance_run_id, household_space_id,
                  decision_run_id, private_claim_candidate_id,
                  knowledge_import_run_id, knowledge_coverage_id,
                  coverage_execution_disposition_id,
                  enrollment_decision_snapshot, enrollment_authority_snapshot,
                  terms_section_id,
                  knowledge_fact_id, source_clause_id, fact_citation_id,
                  candidate_digest_sha256, rank, score,
                  contract_label_snapshot, coverage_label_snapshot,
                  clause_label_snapshot, excerpt, page_start, page_end,
                  citation_kind, reason_code, explanation_code, question_code
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s
                )
                """,
                (
                    uuid4(),
                    cast(UUID, run["id"]),
                    job.household_space_id,
                    target.decision_run_id,
                    item.private_claim_candidate_id,
                    item.knowledge_import_run_id,
                    item.knowledge_coverage_id,
                    item.coverage_execution_disposition_id,
                    item.enrollment_decision_snapshot,
                    item.enrollment_authority_snapshot,
                    item.terms_section_id,
                    item.knowledge_fact_id,
                    item.source_clause_id,
                    item.fact_citation_id,
                    item.candidate_digest_sha256,
                    rank,
                    item.score,
                    item.contract_label,
                    item.coverage_label,
                    item.clause_label,
                    item.excerpt,
                    item.page_start,
                    item.page_end,
                    item.citation_kind,
                    item.reason_code,
                    explanation_code,
                    question_code,
                ),
            )


def _job(row: Mapping[str, Any]) -> RecommendationJobRecord:
    return RecommendationJobRecord(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        medical_event_id=cast(UUID, row["medical_event_id"]),
        event_version=int(row["event_version"]),
        candidate_digest_sha256=cast(str, row["candidate_digest_sha256"]),
        state=cast(JobState, row["state"]),
        attempts=int(row["attempts"]),
    )


def _local(row: Mapping[str, Any]) -> LocalRecommendationRecord:
    return LocalRecommendationRecord(
        id=cast(UUID, row["id"]),
        private_claim_candidate_id=cast(UUID, row["private_claim_candidate_id"]),
        knowledge_import_run_id=cast(UUID, row["knowledge_import_run_id"]),
        knowledge_coverage_id=cast(UUID, row["knowledge_coverage_id"]),
        coverage_execution_disposition_id=cast(UUID, row["coverage_execution_disposition_id"]),
        enrollment_decision_snapshot=cast(EnrollmentDecision, row["enrollment_decision_snapshot"]),
        enrollment_authority_snapshot=cast(
            EnrollmentAuthority, row["enrollment_authority_snapshot"]
        ),
        terms_section_id=cast(UUID, row["terms_section_id"]),
        knowledge_fact_id=cast(UUID, row["knowledge_fact_id"]),
        source_clause_id=cast(UUID, row["source_clause_id"]),
        fact_citation_id=cast(UUID, row["fact_citation_id"]),
        candidate_digest_sha256=cast(str, row["candidate_digest_sha256"]),
        rank=int(row["rank"]),
        score=Decimal(str(row["score"])),
        contract_label=cast(str, row["contract_label_snapshot"]),
        coverage_label=cast(str, row["coverage_label_snapshot"]),
        clause_label=cast(str, row["clause_label_snapshot"]),
        excerpt=cast(str, row["excerpt"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        citation_kind=cast(Literal["FACT_CITATION"], row["citation_kind"]),
        reason_code=cast(str, row["reason_code"]),
    )


def _facts(
    values: object,
    confirmations: object,
) -> tuple[tuple[str, str, FactConfirmation], ...]:
    if not isinstance(values, Mapping) or not isinstance(confirmations, Mapping):
        raise InvalidRecommendationWork
    result: list[tuple[str, str, FactConfirmation]] = []
    for field_id in sorted(str(key) for key in values)[:32]:
        confirmation = confirmations.get(field_id)
        mapped: FactConfirmation | None = None
        if confirmation == "user":
            mapped = "USER_CONFIRMED"
        elif confirmation == "ai_structured":
            mapped = "AI_SUGGESTED"
        if mapped is None:
            continue
        value = values.get(field_id)
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, str | int | float):
            rendered = str(value)
        else:
            continue
        if 1 <= len(rendered) <= 120:
            result.append((field_id, rendered, mapped))
    return tuple(result)


def _require_uuid(value: object) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidRecommendationWork


__all__ = [
    "InvalidRecommendationWork",
    "LocalRecommendationRecord",
    "PostgresRecommendationJobQueue",
    "RecommendationJobRecord",
    "RecommendationQueue",
    "RecommendationQueueUnavailable",
    "RecommendationTarget",
    "RecommendationWorkItem",
]

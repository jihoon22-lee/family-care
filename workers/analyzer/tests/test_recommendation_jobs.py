"""One-attempt recommendation job routing with structured fallback."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from familycare_worker.ai.provider import (
    OpenAiResponsesAdapter,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderTimeoutError,
)
from familycare_worker.ai.recommender import (
    RECOMMENDER_SCHEMA_NAME,
    RecommendationResult,
    recommender_schema,
)
from familycare_worker.recommendation_jobs import (
    LocalRecommendationRecord,
    RecommendationJobRecord,
    RecommendationTarget,
    RecommendationWorkItem,
)
from familycare_worker.runner import RecommendationJobRunner


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _job(number: int = 1, *, version: int = 1) -> RecommendationJobRecord:
    return RecommendationJobRecord(
        id=_uuid(number),
        household_space_id=_uuid(100),
        medical_event_id=_uuid(101),
        event_version=version,
        candidate_digest_sha256="a" * 64,
        state="RUNNING",
        attempts=1,
    )


def _local(index: int = 1) -> LocalRecommendationRecord:
    return LocalRecommendationRecord(
        id=_uuid(200 + index),
        private_claim_candidate_id=_uuid(300 + index),
        knowledge_import_run_id=_uuid(400),
        knowledge_coverage_id=_uuid(500 + index),
        coverage_execution_disposition_id=_uuid(550 + index),
        enrollment_decision_snapshot="MATCH",
        enrollment_authority_snapshot="CERTIFICATE_SNAPSHOT",
        terms_section_id=_uuid(600 + index),
        knowledge_fact_id=_uuid(700 + index),
        source_clause_id=_uuid(800 + index),
        fact_citation_id=_uuid(900 + index),
        candidate_digest_sha256="a" * 64,
        rank=index,
        score=Decimal(3 - index),
        contract_label=f"Sample Policy {index}",
        coverage_label=f"Sample Coverage {index}",
        clause_label=f"Sample Clause {index}",
        excerpt=f"Sample bounded excerpt {index}",
        page_start=index,
        page_end=index,
        citation_kind="FACT_CITATION",
        reason_code="TOKEN_OVERLAP",
    )


def _work(job: RecommendationJobRecord | None = None) -> RecommendationWorkItem:
    item = job or _job()
    return RecommendationWorkItem(
        job=item,
        situation="Synthetic bounded event situation",
        facts=(("MedicalEvent.procedure_kind", "sample_procedure", "USER_CONFIRMED"),),
        targets=(
            RecommendationTarget(
                assistance_run_id=_uuid(1100 + item.event_version),
                decision_run_id=_uuid(1200 + item.event_version),
                event_version=item.event_version,
                recommendations=(_local(1), _local(2)),
            ),
        ),
    )


class _Queue:
    def __init__(
        self,
        jobs: list[RecommendationJobRecord],
        *,
        member_terms: tuple[str, ...] = ("Family Member A", "Member A"),
    ) -> None:
        self.jobs = jobs
        self.works = {job.id: _work(job) for job in jobs}
        self.member_terms = member_terms
        self.fallbacks: list[tuple[UUID, str]] = []
        self.llm_results: list[tuple[UUID, tuple[str, ...]]] = []

    def claim_next_job(self, worker_id: str) -> RecommendationJobRecord | None:
        assert worker_id == "worker-a"
        return self.jobs.pop(0) if self.jobs else None

    def load_work(self, job: RecommendationJobRecord) -> RecommendationWorkItem:
        return self.works[job.id]

    def load_member_terms(self, job: RecommendationJobRecord) -> tuple[str, ...]:
        assert job.household_space_id == _uuid(100)
        return self.member_terms

    def complete_with_fallback(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem | None,
        outcome_code: str,
    ) -> None:
        del work
        self.fallbacks.append((job.id, outcome_code))

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
        del work, provider_label, model_label, config_version
        self.llm_results.append((job.id, tuple(item.token for item in result.selections)))


class _Provider:
    def __init__(self, response: object | BaseException) -> None:
        self.response = response
        self.calls = 0

    def complete(self, **kwargs: object) -> ProviderResponse:
        del kwargs
        self.calls += 1
        if isinstance(self.response, BaseException):
            raise self.response
        return ProviderResponse(
            payload=self.response,  # type: ignore[arg-type]
            request_id="synthetic-request-001",
        )


def _success_payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "recommendations": [
            {
                "token": "candidate-02",
                "explanation_code": "RELATED_CLAUSE",
                "question_code": None,
            },
            {
                "token": "candidate-01",
                "explanation_code": "RELATED_CLAUSE",
                "question_code": None,
            },
        ],
    }


def test_missing_key_makes_zero_external_requests_and_keeps_search(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client_factory_calls: list[str] = []
    provider = OpenAiResponsesAdapter(
        {RECOMMENDER_SCHEMA_NAME: recommender_schema()},
        client_factory=lambda key: client_factory_calls.append(key),  # type: ignore[arg-type,return-value]
    )
    queue = _Queue([_job()])
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True

    assert client_factory_calls == []
    assert queue.llm_results == []
    assert queue.fallbacks == [(_job().id, "PROVIDER_NOT_CONFIGURED")]


def test_configured_provider_is_called_once_and_only_reorders_local_tokens() -> None:
    provider = _Provider(_success_payload())
    queue = _Queue([_job()])
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True
    assert runner.run_once("worker-a") is False

    assert provider.calls == 1
    assert queue.fallbacks == []
    assert queue.llm_results == [(_job().id, ("candidate-02", "candidate-01"))]


def test_unbounded_household_terms_make_zero_external_requests_and_keep_search() -> None:
    provider = _Provider(_success_payload())
    queue = _Queue(
        [_job()],
        member_terms=tuple(f"Synthetic Member {index}" for index in range(17)),
    )
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True

    assert provider.calls == 0
    assert queue.llm_results == []
    assert queue.fallbacks == [(_job().id, "PROVIDER_INPUT_REJECTED")]


def test_residual_synthetic_identifier_makes_zero_external_requests_and_keeps_search() -> None:
    job = _job()
    provider = _Provider(_success_payload())
    queue = _Queue([job])
    queue.works[job.id] = replace(
        queue.works[job.id],
        situation="Synthetic visit customer-id-synthetic-001",
    )
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True

    assert provider.calls == 0
    assert queue.llm_results == []
    assert queue.fallbacks == [(job.id, "PROVIDER_INPUT_REJECTED")]


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ProviderTimeoutError(), "PROVIDER_TIMEOUT"),
        (ProviderRateLimitError(), "PROVIDER_RATE_LIMIT"),
        (ProviderConfigurationError(), "PROVIDER_NOT_CONFIGURED"),
    ],
)
def test_provider_failure_is_not_retried_and_preserves_search(
    error: BaseException,
    outcome: str,
) -> None:
    provider = _Provider(error)
    queue = _Queue([_job()])
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True
    assert runner.run_once("worker-a") is False

    assert provider.calls == 1
    assert queue.llm_results == []
    assert queue.fallbacks == [(_job().id, outcome)]


def test_invalid_response_is_one_call_then_structured_fallback() -> None:
    provider = _Provider(
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "unknown-token",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                }
            ],
        }
    )
    queue = _Queue([_job()])
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True
    assert provider.calls == 1
    assert queue.fallbacks == [(_job().id, "PROVIDER_INVALID_RESPONSE")]


def test_different_event_version_job_may_make_one_new_call() -> None:
    provider = _Provider(_success_payload())
    first = _job(1, version=1)
    second = _job(2, version=2)
    queue = _Queue([first, second])
    runner = RecommendationJobRunner(queue=queue, provider=provider)

    assert runner.run_once("worker-a") is True
    assert runner.run_once("worker-a") is True
    assert runner.run_once("worker-a") is False

    assert provider.calls == 2
    assert [item[0] for item in queue.llm_results] == [first.id, second.id]


@pytest.mark.integration
def test_postgresql_job_success_and_missing_key_fallback_round_trip(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import psycopg
    from familycare_api.decisions.domain import MedicalEvent
    from familycare_api.decisions.repository import DecisionRepository
    from familycare_api.decisions.service import DecisionService
    from familycare_worker.recommendation_jobs import PostgresRecommendationJobQueue
    from psycopg.rows import dict_row

    from apps.api.tests.test_decision_integration import _psycopg_url, _reset_database, _seed
    from apps.api.tests.test_private_knowledge_decision_integration import (
        _seed_private_publication,
    )

    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    _reset_database(database_url)
    seed = _seed(database_url)
    _seed_private_publication(
        database_url,
        seed,
        tmp_path,
        advisory=True,
        user_confirmed_enrollment=True,
    )
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))

    def create_event() -> MedicalEvent:
        return service.create_medical_event(
            family_member_id=seed.member_a,
            mode="post_treatment",
            situation="Synthetic sample category phrase event.",
            event_date=date(2025, 6, 15),
            visit_date=date(2025, 6, 16),
            facts={"MedicalEvent.classification": "sample_category"},
            confirmation={"MedicalEvent.classification": "user"},
        )

    first_event = create_event()
    first = service.analyze_medical_event(first_event.id)
    assert first.assistance is not None
    assert first.assistance.state == "LLM_PENDING"

    class DynamicProvider:
        calls = 0

        def complete(self, **kwargs: object) -> ProviderResponse:
            self.calls += 1
            payload = kwargs["input_payload"]
            assert isinstance(payload, dict)
            candidates = payload["candidates"]
            assert isinstance(candidates, list)
            return ProviderResponse(
                payload={
                    "schema_version": "1",
                    "recommendations": [
                        {
                            "token": candidate["token"],
                            "explanation_code": "RELATED_CLAUSE",
                            "question_code": None,
                        }
                        for candidate in reversed(candidates)
                    ],
                },
                request_id="synthetic-request-001",
            )

    dynamic_provider = DynamicProvider()
    queue = PostgresRecommendationJobQueue(database_url)
    success_runner = RecommendationJobRunner(queue=queue, provider=dynamic_provider)

    assert success_runner.run_once("worker-a") is True
    assert success_runner.run_once("worker-a") is False
    assert dynamic_provider.calls == 1
    loaded_first = service.get_decision_result(first_event.id, first_event.version)
    assert loaded_first.assistance is not None
    assert loaded_first.assistance.mode == "LLM_ASSISTED"
    assert loaded_first.assistance.state == "LLM_READY"

    second_event = create_event()
    second = service.analyze_medical_event(second_event.id)
    assert second.assistance is not None
    factory_calls: list[str] = []
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    no_key_provider = OpenAiResponsesAdapter(
        {RECOMMENDER_SCHEMA_NAME: recommender_schema()},
        client_factory=lambda key: factory_calls.append(key),  # type: ignore[arg-type,return-value]
    )
    fallback_runner = RecommendationJobRunner(queue=queue, provider=no_key_provider)

    assert fallback_runner.run_once("worker-a") is True
    assert fallback_runner.run_once("worker-a") is False
    assert factory_calls == []
    loaded_second = service.get_decision_result(second_event.id, second_event.version)
    assert loaded_second.assistance is not None
    assert loaded_second.assistance.mode == "STRUCTURED_SEARCH"
    assert loaded_second.assistance.state == "SEARCH_READY"

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT state, attempts, outcome_code
            FROM analysis_assistance_jobs
            ORDER BY created_at, id
            """
        ).fetchall()
        assert rows == [
            {
                "state": "SUCCEEDED",
                "attempts": 1,
                "outcome_code": "LLM_RECOMMENDATIONS_READY",
            },
            {
                "state": "SUCCEEDED",
                "attempts": 1,
                "outcome_code": "PROVIDER_NOT_CONFIGURED",
            },
        ]
        lineage = connection.execute(
            """
            SELECT recommendation.enrollment_decision_snapshot,
                   recommendation.coverage_execution_disposition_id,
                   recommendation.enrollment_authority_snapshot
            FROM analysis_recommendations AS recommendation
            JOIN analysis_assistance_runs AS assistance
              ON assistance.id = recommendation.analysis_assistance_run_id
            WHERE assistance.state IN ('LLM_READY', 'SEARCH_READY')
            ORDER BY assistance.created_at, assistance.id, recommendation.rank
            """
        ).fetchall()
        assert lineage
        assert {row["enrollment_decision_snapshot"] for row in lineage} == {"UNKNOWN"}
        assert all(row["coverage_execution_disposition_id"] is not None for row in lineage)
        assert {row["enrollment_authority_snapshot"] for row in lineage} == {
            "USER_CONFIRMED_COVERAGE_ENROLLMENT"
        }

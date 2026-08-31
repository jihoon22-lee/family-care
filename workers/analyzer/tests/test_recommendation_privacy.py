"""Recommendation job errors, representations, and logs stay sanitized."""

from __future__ import annotations

import logging

import pytest
from familycare_worker.ai.provider import ProviderResponse, ProviderValidationError
from familycare_worker.runner import RecommendationJobRunner

from workers.analyzer.tests.test_recommendation_jobs import _job, _Queue, _work


class _FailingProvider:
    def complete(self, **kwargs: object) -> ProviderResponse:
        del kwargs
        raise ProviderValidationError


def test_job_and_work_repr_hide_scope_event_facts_and_excerpt() -> None:
    job = _job()
    work = _work(job)
    rendered = f"{job!r} {work!r}"

    assert str(job.household_space_id) not in rendered
    assert str(job.medical_event_id) not in rendered
    assert "Synthetic bounded event situation" not in rendered
    assert "sample_procedure" not in rendered
    assert "bounded excerpt" not in rendered


def test_provider_failure_logs_and_completion_have_only_stable_codes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue = _Queue([_job()])
    runner = RecommendationJobRunner(queue=queue, provider=_FailingProvider())

    with caplog.at_level(logging.DEBUG):
        assert runner.run_once("worker-a") is True

    rendered = f"{caplog.text} {queue.fallbacks!r}"
    for marker in (
        "Synthetic bounded event situation",
        "sample_procedure",
        "bounded excerpt",
        "OPENAI_API_KEY",
        "/private/source.pdf",
        str(_job().household_space_id),
        str(_job().medical_event_id),
    ):
        assert marker not in rendered
    assert queue.fallbacks == [(_job().id, "PROVIDER_INVALID_RESPONSE")]

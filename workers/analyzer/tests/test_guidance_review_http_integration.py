"""Installed SDK HTTP attempts share the actual durable review lifecycle and quotas."""

import json

import httpx2
import openai
import psycopg
import pytest
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_worker.ai.guidance_reviewer import SCHEMA_NAME, guidance_review_schema
from familycare_worker.ai.provider import OpenAiResponsesAdapter
from familycare_worker.guidance_review_budget import GuidanceReviewBudget, ReviewBudgetRejected
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue, _lease
from familycare_worker.guidance_review_runner import GuidanceReviewRunner
from psycopg.rows import dict_row

from apps.api.tests.test_guidance_review_reassessment_integration import (
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_review_reassessment_integration import (
    unreviewed_original as unreviewed_original,
)
from apps.api.tests.test_terms_change_integration import _psycopg_url
from workers.analyzer.tests.test_guidance_review_provider import _response
from workers.analyzer.tests.test_guidance_reviewer import FakeProvider

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "fault", ["success", "429", "timeout", "lost_settlement", "changed_version", "cancel"]
)
def test_real_http_and_database_prevent_duplicate_send_after_failure_or_restart(
    unreviewed_original,
    monkeypatch,
    fault,
):
    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        claimed = _lease(
            connection.execute(
                "SELECT * FROM guidance_review_jobs WHERE id=%s", (sample.job.id,)
            ).fetchone()
        )

    class ClaimedQueue(GuidanceReviewQueue):
        claimed_once = False

        def claim(self):
            if not self.claimed_once:
                self.claimed_once = True
                return claimed
            return super().claim()

    queue = ClaimedQueue(sample.url)
    budget = GuidanceReviewBudget(sample.url)
    attempts = []

    def handle(request):
        attempts.append(request)
        body = json.loads(request.content)
        assert body["max_output_tokens"] == 4000 and body["store"] is False
        with psycopg.connect(_psycopg_url(sample.url)) as connection:
            assert connection.execute("SELECT state FROM guidance_review_requests").fetchone() == (
                "RESERVED",
            )
        if fault == "429":
            return httpx2.Response(
                429, json={"error": {"message": "synthetic-rate-limit", "type": "rate_limit"}}
            )
        if fault == "timeout":
            raise httpx2.ReadTimeout("synthetic-timeout", request=request)
        if fault == "changed_version":
            with psycopg.connect(_psycopg_url(sample.url)) as connection:
                connection.execute(
                    "UPDATE medical_events SET version=version+1 WHERE id=%s", (sample.event.id,)
                )
        if fault == "cancel":
            GuidanceReviewRepository(sample.url).cancel(sample.scope, sample.job.id)
        proposal = FakeProvider().complete(input_payload=json.loads(body["input"])).payload
        response = _response()
        response["output"][0]["content"][0]["text"] = json.dumps(dict(proposal))
        return httpx2.Response(200, json=response)

    transport = httpx2.Client(transport=httpx2.MockTransport(handle))
    sdk = openai.OpenAI
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: sdk(
            **kwargs,
            base_url="https://synthetic.invalid/v1",
            organization="synthetic-org",
            project="synthetic-project",
            http_client=transport,
        ),
    )
    adapter = OpenAiResponsesAdapter(
        {SCHEMA_NAME: guidance_review_schema()},
        output_token_limits={SCHEMA_NAME: 4000},
        request_timeouts={SCHEMA_NAME: 40.0},
    )
    if fault == "lost_settlement":

        def lost(*args, **kwargs):
            raise ReviewBudgetRejected

        monkeypatch.setattr(budget, "finish", lost)
        # Failure injection models a process lost after the HTTP response, before housekeeping.
        monkeypatch.setattr(queue, "fail", lambda *args: False)
    with transport:
        runner = GuidanceReviewRunner(queue=queue, provider=adapter, request_budget=budget)
        assert runner.run_once("first-worker")
        if fault in ("success", "lost_settlement"):
            with psycopg.connect(_psycopg_url(sample.url)) as connection:
                connection.execute(
                    "UPDATE guidance_review_jobs SET "
                    "lease_expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE id=%s",
                    (sample.job.id,),
                )
        restarted = GuidanceReviewRunner(
            queue=GuidanceReviewQueue(sample.url),
            provider=adapter,
            request_budget=GuidanceReviewBudget(sample.url),
        )
        assert not restarted.run_once("restarted-worker")
    assert len(attempts) == 1
    current = GuidanceReviewRepository(sample.url).get_job(sample.scope, sample.job.id)
    assert current.usage.requests_reserved == 1
    assert current.state == ("cancelled" if fault == "cancel" else "failed")
    assert current.result is None
    if fault in ("429", "timeout", "lost_settlement"):
        assert current.usage.input_tokens is None
    else:
        assert current.usage.total_tokens == 140
    if fault != "changed_version":
        duplicate = GuidanceReviewRepository(sample.url).enqueue(
            sample.scope,
            sample.event.id,
            run_id=sample.original.run_id,
            expected_event_version=sample.event.version,
        )
        assert duplicate.id == current.id and len(attempts) == 1
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_requests").fetchone() == (
            1,
        )
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (sample.original.run_id,)
        ).fetchone() == (sample.original.local_guidance.model_dump(mode="json"),)

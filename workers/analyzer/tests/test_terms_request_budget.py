"""Terms and policy calls share durable reservations and bounded private cache."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from familycare_api.terms_knowledge.repository import TermsSemanticRepository
from familycare_api.terms_knowledge.work_repository import TermsSemanticWorkRepository
from familycare_worker.ai.provider import (
    ProviderRateLimitError,
    ProviderResponse,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from familycare_worker.ai.terms_structurer import structure_terms_region
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_request_budget import PolicyBudgetExhausted, PolicyRequestBudget
from familycare_worker.terms_request_budget import (
    BudgetedTermsProvider,
    TermsBudgetExhausted,
    TermsRequestBudget,
)
from familycare_worker.terms_semantic_jobs import TermsSemanticJobQueue

from apps.api.tests.test_terms_knowledge_repository import (
    seeded_policy_database,  # noqa: F401
    semantic_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_terms_semantic_jobs import (
    _psycopg_url,
    terms_jobs,  # noqa: F401
)
from workers.analyzer.tests.test_terms_structurer import FakeProvider

pytestmark = pytest.mark.integration


@pytest.fixture()
def terms_context(request):
    return request.getfixturevalue("terms_jobs")


def wrapper(url, job, raw=None, *, daily=8, per_document=4):
    return BudgetedTermsProvider(
        provider=raw or FakeProvider(),
        budget=TermsRequestBudget(url, daily=daily, per_document=per_document),
        job=job,
        worker_id="worker-a",
    )


def run(provider, job):
    terms = TermsSemanticJobQueue(provider.budget.database_url).load_sensitive_terms(
        job, "worker-a"
    )
    return structure_terms_region(
        envelope=job.envelope, provider=provider, model="synthetic-model", sensitive_terms=terms
    )


def counts(url):
    with psycopg.connect(_psycopg_url(url)) as connection:
        return dict(
            connection.execute(
                "SELECT state,count(*) FROM policy_provider_requests GROUP BY state"
            ).fetchall()
        )


def test_reservation_commits_before_provider_and_cache_reuses_minimized_response(terms_context):
    url, _, _, _ = terms_context
    job = TermsSemanticJobQueue(url).claim("worker-a")

    class InspectingProvider(FakeProvider):
        def complete(self, **kwargs):
            assert counts(url) == {"RESERVED": 1}
            return super().complete(**kwargs)

    raw = InspectingProvider()
    first = run(wrapper(url, job, raw, per_document=1), job)
    assert run(wrapper(url, job, raw, per_document=1), job) == first
    assert len(raw.calls) == 1 and counts(url) == {"SUCCEEDED": 1}
    with psycopg.connect(_psycopg_url(url)) as connection:
        cached = connection.execute(
            "SELECT response_json FROM policy_provider_requests"
        ).fetchone()[0]
    assert cached["sources"][0]["generation_id"] != job.envelope.source.generation_id
    assert first[0].sources[0] == job.envelope.source


def test_failed_calls_spend_budget_and_expose_document_scope(terms_context):
    url, _, _, _ = terms_context
    job = TermsSemanticJobQueue(url).claim("worker-a")

    class TimedOut:
        def complete(self, **kwargs):
            raise ProviderTimeoutError

    provider = wrapper(url, job, TimedOut(), per_document=1)
    with pytest.raises(ProviderTimeoutError):
        run(provider, job)
    assert counts(url) == {"FAILED": 1}
    with pytest.raises(ProviderRateLimitError, match="RETRYABLE_PROVIDER_ERROR"):
        run(provider, job)
    assert provider.exhausted_scope == "document"
    assert counts(url) == {"FAILED": 1}


def test_policy_and_terms_contend_for_last_shared_daily_slot(terms_context):
    url, _, _, _ = terms_context
    terms_job = TermsSemanticJobQueue(url).claim("worker-a")
    policy_job = PolicyStructuringJobQueue(url).claim_next_job("policy-worker")
    assert policy_job is not None

    def reserve(kind):
        try:
            if kind == "policy":
                return PolicyRequestBudget(url, daily=1).reserve(
                    policy_job, "policy-worker", "a" * 64
                )
            return TermsRequestBudget(url, daily=1).reserve(terms_job, "worker-a", "b" * 64)
        except PolicyBudgetExhausted, TermsBudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ["policy", "terms"]))
    assert sum(result is not None for result in results) == 1
    assert counts(url) == {"RESERVED": 1}


def test_expired_reservation_is_failed_and_never_refunded(terms_context):
    url, _, _, _ = terms_context
    job = TermsSemanticJobQueue(url).claim("worker-a")
    budget = TermsRequestBudget(url, per_document=1)
    # Insert an already-expired reservation; immutable timestamps cannot be rewritten.
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "INSERT INTO policy_provider_requests(terms_job_id,document_id,fingerprint,state,"
            "reserved_at,expires_at) SELECT %s,document_id,%s,'RESERVED',"
            "clock_timestamp()-interval '4 minutes',clock_timestamp()-interval '1 minute' FROM "
            "document_versions WHERE id=%s",
            (job.id, "c" * 64, job.document_version_id),
        )
    with pytest.raises(TermsBudgetExhausted):
        budget.reserve(job, "worker-a", "c" * 64)
    assert counts(url) == {"FAILED": 1}


def test_wrong_owner_scope_or_stale_privacy_cannot_even_reuse_cache(terms_context):
    url, scope, _, _ = terms_context
    job = TermsSemanticJobQueue(url).claim("worker-a")
    budget = TermsRequestBudget(url)
    reservation = budget.reserve(job, "worker-a", "d" * 64)
    budget.finish(reservation, ProviderResponse({"synthetic": True}, "synthetic-request"))
    for invalid in (replace(job, lease_token=uuid4()), replace(job, household_space_id=uuid4())):
        with pytest.raises(ProviderUnavailableError):
            budget.reserve(invalid, "worker-a", "d" * 64)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (scope.household_space_id,),
        )
    with pytest.raises(ProviderUnavailableError):
        budget.reserve(job, "worker-a", "d" * 64)
    assert counts(url) == {"SUCCEEDED": 1}


def test_busy_same_document_fingerprint_across_terms_jobs_cannot_double_reserve(terms_context):
    url, _, _, _ = terms_context
    queue = TermsSemanticJobQueue(url)
    first, second = queue.claim("worker-a"), queue.claim("worker-b")
    budget = TermsRequestBudget(url)
    budget.reserve(first, "worker-a", "e" * 64)
    with pytest.raises(ProviderUnavailableError):
        budget.reserve(second, "worker-b", "e" * 64)
    assert counts(url) == {"RESERVED": 1}


def test_same_minimized_content_reuses_cache_after_current_source_revision(terms_context):
    url, scope, edition, _ = terms_context
    queue = TermsSemanticJobQueue(url)
    first = queue.claim("worker-a")
    raw = FakeProvider()
    old = run(wrapper(url, first, raw), first)[0]
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("UPDATE terms_editions SET version=version+1 WHERE id=%s", (edition,))
    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    old_primary_label = next(
        r.label for r in first.envelope.regions if r.region_id in first.envelope.primary_region_ids
    )
    primary = next(
        r.region_id for r in plan.snapshot.layout.regions if r.label == old_primary_label
    )
    TermsSemanticWorkRepository(url).enqueue(scope, edition, (primary,))
    later = queue.claim("worker-a")
    assert later.id != first.id and later.input_digest != first.input_digest
    new = run(wrapper(url, later, raw), later)[0]
    assert len(raw.calls) == 1 and counts(url) == {"SUCCEEDED": 1}
    assert new.sources[0] == later.envelope.source
    assert [n.node_id for n in old.nodes] == [n.node_id for n in new.nodes]


@pytest.mark.parametrize("fault", ["nonfinite", "oversized", "wrong_quote"])
def test_invalid_provider_response_cannot_enter_success_cache(terms_context, fault):
    url, _, _, _ = terms_context
    job = TermsSemanticJobQueue(url).claim("worker-a")

    def mutate(graph):
        if fault == "nonfinite":
            graph["citations"][0]["bbox"][0] = float("nan")
        elif fault == "oversized":
            graph["extra"] = "X" * 131073
        else:
            graph["citations"][0]["text"] = "Synthetic forged quote"

    with pytest.raises(ProviderValidationError):
        run(wrapper(url, job, FakeProvider(mutate)), job)
    assert counts(url) == {"FAILED": 1}

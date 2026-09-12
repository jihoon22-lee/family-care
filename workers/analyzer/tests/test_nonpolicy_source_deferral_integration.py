"""Local REVIEW decisions preserve paid history in the dedicated synthetic database."""

from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.policy_draft_replay import PolicyDraftNormalizationRepository
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import (
    NONPOLICY_DEFERRAL_REASON,
    NONPOLICY_DEFERRAL_REVISION,
    PolicyRangeConflict,
    PolicyRangeRepository,
)
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row

from workers.analyzer.tests.test_initial_policy_draft_normalization import _raw_request
from workers.analyzer.tests.test_policy_range_repository import (
    WORKER,
    _no_facts,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_policy_range_repository import (
    ranges_database as ranges_database,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def nonpolicy_source(ranges_database):
    url, original = ranges_database
    job = replace(original, pipeline_version="policy-range-normalized-v2")
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET pipeline_version=%s WHERE id=%s",
            (job.pipeline_version, job.id),
        )
    loader = PolicyEvidenceLoader(url)
    terms = loader.load_member_terms(
        household_space_id=job.household_space_id, family_member_id=job.family_member_id
    )
    work = PolicyRangeRepository(url).next(job, WORKER, sensitive_terms=terms)
    assert work is not None
    assert not any(item.primary and item.source_role == "policy" for item in work.envelope.evidence)
    return url, job, work, terms


def test_all_nonpolicy_ranges_advance_without_spending_and_remain_partial(nonpolicy_source):
    url, job, work, _ = nonpolicy_source
    provider, publisher = Mock(), Mock()
    budget = Mock(spec=PolicyRequestBudget)
    queue = PolicyStructuringJobQueue(url)
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=PolicyEvidenceLoader(url),
        provider=provider,
        publisher=publisher,
        request_budget=budget,
        range_repository=PolicyRangeRepository(url),
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        before = connection.execute(
            "SELECT envelope_id,envelope_json FROM document_policy_ranges "
            "WHERE job_id=%s ORDER BY position",
            (job.id,),
        ).fetchall()
    for index in range(len(before)):
        runner._run_range(job, WORKER)
        if index + 1 < len(before):
            job = queue.claim_next_job(WORKER)
            assert job is not None
    assert not provider.mock_calls and not budget.mock_calls and not publisher.mock_calls
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            "SELECT envelope_id,envelope_json,state,result_json FROM document_policy_ranges "
            "WHERE job_id=%s ORDER BY position",
            (job.id,),
        ).fetchall()
        assert [(row["envelope_id"], row["envelope_json"]) for row in rows] == before
        assert all(row["state"] == "REVIEW" for row in rows)
        for row in rows:
            assert row["result_json"] == {
                "schema_version": "1",
                "local_processing": {
                    "revision": NONPOLICY_DEFERRAL_REVISION,
                    "reason_code": NONPOLICY_DEFERRAL_REASON,
                },
                "preserved_job_provider_requests": [],
            }
        assert (
            connection.execute(
                "SELECT state FROM document_policy_range_plans WHERE job_id=%s", (job.id,)
            ).fetchone()["state"]
            == "PARTIAL"
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM policy_provider_requests WHERE job_id=%s", (job.id,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE structuring_job_id=%s",
                (job.id,),
            ).fetchone()["n"]
            == 0
        )
    assert queue.get_job(job.id).state == "permanently_failed"
    with pytest.raises(PolicyRangeConflict):
        PolicyRangeRepository(url).defer_nonpolicy(job, WORKER, work, sensitive_terms=())


def test_existing_paid_response_and_normalized_receipt_survive_local_review(nonpolicy_source):
    url, job, work, terms = nonpolicy_source
    raw = _no_facts(work)
    request = _raw_request(url, job, raw)
    PolicyDraftNormalizationRepository(url).normalize(
        job, WORKER, work, raw, "synthetic-normalization-request"
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        before = connection.execute(
            "SELECT to_jsonb(r) FROM policy_range_replay_sources r WHERE job_id=%s", (job.id,)
        ).fetchall()
        paid = connection.execute(
            "SELECT to_jsonb(r) FROM policy_provider_requests r WHERE job_id=%s", (job.id,)
        ).fetchall()
    PolicyRangeRepository(url).defer_nonpolicy(job, WORKER, work, sensitive_terms=terms)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(r) FROM policy_range_replay_sources r WHERE job_id=%s", (job.id,)
            ).fetchall()
            == before
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(r) FROM policy_provider_requests r WHERE job_id=%s", (job.id,)
            ).fetchall()
            == paid
        )
        state, result = connection.execute(
            "SELECT state,result_json FROM document_policy_ranges "
            "WHERE job_id=%s AND envelope_id=%s",
            (job.id, work.envelope.envelope_id),
        ).fetchone()
        assert state == "REVIEW" and "batch" not in result and "result" not in result
        assert result["draft_normalization"]["source_provider_request_id"] == str(request)
        assert result["preserved_job_provider_requests"] == [
            {"reservation_id": str(request), "state": "SUCCEEDED"}
        ]


@pytest.mark.parametrize(
    "fault", ["scope", "generation", "privacy", "members", "lease", "cancelled"]
)
def test_local_deferral_preserves_source_lease_member_and_privacy_guards(nonpolicy_source, fault):
    url, job, work, terms = nonpolicy_source
    if fault == "scope":
        job = replace(job, family_member_id=uuid4())
    elif fault == "generation":
        work = replace(work, generation_id=uuid4())
    elif fault == "privacy":
        terms = (*terms, "Synthetic New Alias")
    else:
        with psycopg.connect(_psycopg_url(url)) as connection:
            if fault == "members":
                connection.execute(
                    "UPDATE family_members SET display_name='Synthetic Changed Member' WHERE id=%s",
                    (job.family_member_id,),
                )
            elif fault == "lease":
                connection.execute(
                    "UPDATE policy_structuring_jobs SET "
                    "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                    (job.id,),
                )
            else:
                connection.execute(
                    "UPDATE document_structure_generations SET cancelled=true WHERE id=%s",
                    (work.generation_id,),
                )
    with pytest.raises(PolicyRangeConflict):
        PolicyRangeRepository(url).defer_nonpolicy(job, WORKER, work, sensitive_terms=terms)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM document_policy_ranges "
            "WHERE state<>'PENDING' OR result_json IS NOT NULL"
        ).fetchone() == (0,)

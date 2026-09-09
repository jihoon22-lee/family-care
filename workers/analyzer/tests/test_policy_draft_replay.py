"""Explicit replay binds an old draft to unchanged private source and a fresh lease."""

from uuid import uuid4

import psycopg
import pytest
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workers.analyzer.tests.test_retained_policy_resubmission import (
    WORKER,
    _assert_original_preserved,
    _enqueue,
    _no_facts,
    _psycopg_url,
    _target,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_retained_policy_resubmission import (
    retained_source as retained_source,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def replay_source(retained_source, monkeypatch, request):
    from familycare_worker import retained_policy

    sample = retained_source
    with monkeypatch.context() as old:
        old.setattr(
            retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", "retained-policy-association-v2"
        )
        source = _enqueue(sample, pipeline_revision="retained-policy-association-v2")
        source_queue = retained_policy.RetainedPolicyJobQueue(
            sample.url,
            household_space_id=source.household_space_id,
            job_id=source.id,
            pipeline_revision="retained-policy-association-v2",
        )
    source = source_queue.claim_next_job(WORKER)
    ranges = PolicyRangeRepository(sample.url)
    work = ranges.next(source, WORKER, sensitive_terms=())
    response = _no_facts(work).model_dump(mode="json")
    if getattr(request, "param", None) == "loss":
        key = str(uuid4())
        response["candidates"] = [
            {
                "schema_version": "1",
                "candidate_id": key,
                "candidate_kind": "policy_contract",
                "fields": [
                    {
                        "field_id": "product_name",
                        "value": "Unsupported Synthetic Plan",
                        "evidence_ids": [str(work.envelope.primary_evidence_ids[0])],
                    }
                ],
            }
        ]
        response["ranges"][0].update(outcome="CANDIDATES", candidate_ids=[key])
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        request = connection.execute(
            "INSERT INTO policy_provider_requests(job_id, document_id, fingerprint, state, "
            "response_json, request_id) SELECT %s,document_id,%s,'SUCCEEDED',%s,%s "
            "FROM document_versions WHERE id=%s RETURNING id",
            (
                source.id,
                "f" * 64,
                Jsonb(response),
                "synthetic-old-structurer",
                source.document_version_id,
            ),
        ).fetchone()["id"]
    ranges.reject(source, WORKER, work)
    target = _enqueue(sample)
    target = _target(sample, target.id).claim_next_job(WORKER)
    target_work = ranges.next(target, WORKER, sensitive_terms=())
    return sample, source, request, target, target_work, ranges


@pytest.mark.parametrize("replay_source", ["loss"], indirect=True)
def test_discarded_facts_stay_review_even_when_remaining_batch_is_success(replay_source):
    from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository

    sample, _, request, job, work, ranges = replay_source
    replay = PolicyDraftReplayRepository(sample.url, source_provider_request_id=request)
    draft = replay.prepare(job, WORKER, work)
    assert not draft.batch.candidates
    ranges.save(
        job,
        WORKER,
        work,
        draft.batch,
        CandidatePipelineResult(classification="SUCCESS", candidates=()),
    )
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT r.state,p.partial,p.adjustments_json FROM document_policy_ranges r "
            "JOIN policy_range_replay_sources p USING(job_id,envelope_id) "
            "WHERE r.job_id=%s AND r.envelope_id=%s",
            (job.id, work.envelope.envelope_id),
        ).fetchone()
    assert row["state"] == "REVIEW" and row["partial"] and row["adjustments_json"]


def test_saved_batch_cannot_differ_from_receipted_local_draft(replay_source):
    from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository

    sample, _, request, job, work, ranges = replay_source
    replay = PolicyDraftReplayRepository(sample.url, source_provider_request_id=request)
    replay.prepare(job, WORKER, work)
    with pytest.raises(PolicyRangeConflict):
        ranges.save(
            job,
            WORKER,
            work,
            _no_facts(work, unresolved=True),
            CandidatePipelineResult(classification="SUCCESS", candidates=()),
        )


def test_replay_receipt_is_idempotent_preserves_source_and_can_save(replay_source):
    from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository

    sample, source, request, job, work, ranges = replay_source
    replay = PolicyDraftReplayRepository(sample.url, source_provider_request_id=request)
    draft = replay.prepare(job, WORKER, work)
    assert draft == replay.prepare(job, WORKER, work)
    assert draft.request_id == "synthetic-old-structurer"
    replay.assert_current(job, WORKER, work)
    ranges.save(
        job,
        WORKER,
        work,
        draft.batch,
        CandidatePipelineResult(classification="SUCCESS", candidates=()),
    )
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        receipt = connection.execute(
            "SELECT * FROM policy_range_replay_sources WHERE job_id=%s", (job.id,)
        ).fetchone()
        assert receipt["source_job_id"] == source.id
        assert receipt["source_provider_request_id"] == request
        assert (
            connection.execute("SELECT count(*) AS n FROM policy_provider_requests").fetchone()["n"]
            == 1
        )
        for sql in (
            "UPDATE policy_range_replay_sources SET partial=true",
            "DELETE FROM policy_range_replay_sources",
        ):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(sql)
    _assert_original_preserved(sample)


@pytest.mark.parametrize("mutation", ["response", "privacy", "lease", "request"])
def test_replay_rejects_changed_authority_before_reuse_and_save(replay_source, mutation):
    from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository

    sample, source, request, job, work, ranges = replay_source
    replay = PolicyDraftReplayRepository(sample.url, source_provider_request_id=request)
    draft = replay.prepare(job, WORKER, work)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        if mutation == "response":
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(
                    "UPDATE policy_provider_requests SET response_json='{}' WHERE id=%s", (request,)
                )
        elif mutation == "privacy":
            connection.execute(
                "UPDATE family_members SET display_name='Synthetic changed member' WHERE id=%s",
                (job.family_member_id,),
            )
        elif mutation == "lease":
            connection.execute(
                "UPDATE policy_structuring_jobs SET "
                "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                (job.id,),
            )
        else:
            replay = PolicyDraftReplayRepository(sample.url, source_provider_request_id=uuid4())
    if mutation == "response":
        # The existing provider journal already rejects the change at its write boundary.
        replay.assert_current(job, WORKER, work)
        return
    with pytest.raises(PolicyRangeConflict):
        replay.prepare(job, WORKER, work)
    if mutation != "request":
        with pytest.raises(PolicyRangeConflict):
            ranges.save(
                job,
                WORKER,
                work,
                draft.batch,
                CandidatePipelineResult(classification="SUCCESS", candidates=()),
            )

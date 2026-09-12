"""Explicit retained-source jobs preserve history and never enter the automatic queue."""

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeRepository
from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION
from psycopg.rows import dict_row

from workers.analyzer.tests.test_policy_range_repository import (
    WORKER,
    _no_facts,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_policy_range_repository import (
    ranges_database as ranges_database,
)
from workers.analyzer.tests.test_policy_structuring_jobs import _psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def historical_v5_producer(monkeypatch):
    from familycare_worker import retained_policy

    monkeypatch.setattr(
        retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", "retained-policy-association-v5"
    )


@pytest.fixture()
def retained_source(ranges_database):
    url, job = ranges_database
    original = job
    ranges = PolicyRangeRepository(url)
    generation = None
    while job is not None:
        work = ranges.next(job, WORKER, sensitive_terms=())
        assert work is not None
        generation = work.generation_id
        ranges.save(
            job,
            WORKER,
            work,
            _no_facts(work),
            CandidatePipelineResult(classification="SUCCESS", candidates=()),
        )
        job = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
    assert generation is not None
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        old_job = connection.execute(
            "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s", (original.id,)
        ).fetchone()["value"]
        old_plan = connection.execute(
            "SELECT to_jsonb(p) AS value FROM document_policy_range_plans p WHERE job_id=%s",
            (original.id,),
        ).fetchone()["value"]
        old_ranges = connection.execute(
            "SELECT to_jsonb(r) AS value FROM document_policy_ranges r WHERE job_id=%s "
            "ORDER BY position",
            (original.id,),
        ).fetchall()
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM extractions) AS extractions,"
            "(SELECT count(*) FROM document_batch_items) AS items"
        ).fetchone()
    assert old_job["state"] == "succeeded"
    yield SimpleNamespace(
        url=url,
        original=original,
        generation=generation,
        old_job=old_job,
        old_plan=old_plan,
        old_ranges=old_ranges,
        counts=counts,
    )


def _enqueue(sample, **overrides):
    from familycare_worker.retained_policy import (
        RETAINED_POLICY_PIPELINE_REVISION,
        RetainedPolicyRepository,
    )

    arguments = {
        "pipeline_revision": RETAINED_POLICY_PIPELINE_REVISION,
        "household_space_id": sample.original.household_space_id,
        "source_job_id": sample.original.id,
        "expected_generation_id": sample.generation,
    }
    arguments.update(overrides)
    return RetainedPolicyRepository(sample.url).enqueue(**arguments)


def _target(sample, job_id):
    from familycare_worker.retained_policy import (
        RETAINED_POLICY_PIPELINE_REVISION,
        RetainedPolicyJobQueue,
    )

    return RetainedPolicyJobQueue(
        sample.url,
        household_space_id=sample.original.household_space_id,
        job_id=job_id,
        pipeline_revision=RETAINED_POLICY_PIPELINE_REVISION,
    )


def _assert_original_preserved(sample):
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s",
                (sample.original.id,),
            ).fetchone()["value"]
            == sample.old_job
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_policy_range_plans p WHERE job_id=%s",
                (sample.original.id,),
            ).fetchone()["value"]
            == sample.old_plan
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(r) AS value FROM document_policy_ranges r WHERE job_id=%s "
                "ORDER BY position",
                (sample.original.id,),
            ).fetchall()
            == sample.old_ranges
        )
        assert (
            connection.execute(
                "SELECT (SELECT count(*) FROM extractions) AS extractions,"
                "(SELECT count(*) FROM document_batch_items) AS items"
            ).fetchone()
            == sample.counts
        )


def test_resubmit_keeps_source_history_and_creates_one_targeted_revision(retained_source):
    from familycare_worker.retained_policy import RETAINED_POLICY_PIPELINE_REVISION

    sample = retained_source
    new = _enqueue(sample)
    assert new.id != sample.original.id
    assert new.extraction_id == sample.original.extraction_id
    assert new.batch_item_id == sample.original.batch_item_id
    assert new.source_generation_id == sample.generation
    assert new.resubmission_of_job_id == sample.original.id
    assert new.pipeline_version == RETAINED_POLICY_PIPELINE_REVISION
    assert new.processing_mode == "retained"
    assert _enqueue(sample).id == new.id
    assert PolicyStructuringJobQueue(sample.url).claim_next_job(WORKER) is None
    queue = _target(sample, new.id)
    job = queue.claim_next_job(WORKER)
    assert job is not None and job.id == new.id
    ranges = PolicyRangeRepository(sample.url)
    work = ranges.next(job, WORKER, sensitive_terms=())
    assert work is not None and work.generation_id == sample.generation
    # Partial progress belongs to the new job; a restart resumes the next range.
    ranges.save(
        job,
        WORKER,
        work,
        _no_facts(work),
        CandidatePipelineResult(classification="SUCCESS", candidates=()),
    )
    next_job = _target(sample, new.id).claim_next_job(WORKER)
    assert next_job is not None
    next_work = ranges.next(next_job, WORKER, sensitive_terms=())
    assert next_work is not None and next_work.envelope.envelope_id != work.envelope.envelope_id
    _assert_original_preserved(sample)


def test_concurrent_resubmissions_are_idempotent(retained_source):
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: _enqueue(retained_source), range(2)))
    assert jobs[0].id == jobs[1].id
    with psycopg.connect(_psycopg_url(retained_source.url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM policy_structuring_jobs WHERE processing_mode='retained'"
        ).fetchone() == (1,)
    _assert_original_preserved(retained_source)


def test_resubmission_rechecks_generation_after_waiting_for_its_lock(retained_source):
    from familycare_worker.retained_policy import RetainedPolicyConflict

    sample = retained_source
    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(_psycopg_url(sample.url)) as blocker:
            blocker.execute(
                "UPDATE document_structure_generations SET is_current=false WHERE id=%s",
                (sample.generation,),
            )
            pending = pool.submit(_enqueue, sample)
            waiting = False
            deadline = monotonic() + 5
            with psycopg.connect(_psycopg_url(sample.url), autocommit=True) as observer:
                while monotonic() < deadline and not waiting:
                    waiting = observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' "
                        "AND query LIKE '%%retained_policy_source_is_current(original.id%%')"
                    ).fetchone()[0]
                    if not waiting:
                        sleep(0.01)
            assert waiting
        with pytest.raises(RetainedPolicyConflict):
            pending.result(timeout=5)
    _assert_original_preserved(sample)


@pytest.mark.parametrize("fault", ["household", "generation", "revision", "member", "document"])
def test_resubmission_rejects_foreign_or_unavailable_inputs(retained_source, fault):
    from familycare_worker.retained_policy import RetainedPolicyConflict

    sample = retained_source
    arguments = {}
    if fault == "household":
        arguments["household_space_id"] = uuid4()
    elif fault == "generation":
        arguments["expected_generation_id"] = uuid4()
    elif fault == "revision":
        arguments["pipeline_revision"] = "unsupported-retained-revision"
    else:
        with psycopg.connect(_psycopg_url(sample.url)) as connection:
            if fault == "member":
                connection.execute(
                    "UPDATE family_members SET deleted_at=clock_timestamp() WHERE id=%s",
                    (sample.original.family_member_id,),
                )
            else:
                connection.execute(
                    "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=(SELECT "
                    "document_id FROM document_versions WHERE id=%s)",
                    (sample.original.document_version_id,),
                )
    with pytest.raises(RetainedPolicyConflict):
        _enqueue(sample, **arguments)


@pytest.mark.parametrize("stage", ["enqueue", "claim", "next", "save"])
@pytest.mark.parametrize("change", ["superseded", "cancelled"])
def test_generation_freshness_is_checked_before_dispatch_and_publication(
    retained_source, stage, change
):
    from familycare_worker.retained_policy import RetainedPolicyConflict

    sample = retained_source
    new = _enqueue(sample) if stage != "enqueue" else None
    queue = _target(sample, new.id) if new else None
    job = queue.claim_next_job(WORKER) if stage in {"next", "save"} else None
    ranges = PolicyRangeRepository(sample.url)
    work = ranges.next(job, WORKER, sensitive_terms=()) if stage == "save" else None
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE document_structure_generations SET "
            + ("is_current=false" if change == "superseded" else "cancelled=true")
            + " WHERE id=%s",
            (sample.generation,),
        )
    if stage == "enqueue":
        with pytest.raises(RetainedPolicyConflict):
            _enqueue(sample)
    elif stage == "claim":
        assert queue.claim_next_job(WORKER) is None
    elif stage == "next":
        with pytest.raises(PolicyRangeConflict):
            ranges.next(job, WORKER, sensitive_terms=())
        assert queue.heartbeat(job.id, WORKER) is False
    else:
        with pytest.raises(PolicyRangeConflict):
            ranges.save(
                job,
                WORKER,
                work,
                _no_facts(work),
                CandidatePipelineResult(classification="SUCCESS", candidates=()),
            )
    _assert_original_preserved(sample)


def test_target_queue_does_not_claim_or_recover_other_jobs(retained_source):
    sample = retained_source
    new = _enqueue(sample)
    queue = _target(sample, new.id)
    job = queue.claim_next_job(WORKER)
    assert job is not None
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET lease_expires_at=clock_timestamp()-interval '1s' "
            "WHERE id=%s",
            (new.id,),
        )
        before = connection.execute(
            "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s", (new.id,)
        ).fetchone()["value"]
        connection.execute(
            "UPDATE policy_structuring_jobs SET available_at=clock_timestamp()-interval '1s' "
            "WHERE processing_mode='automatic' AND state='queued'"
        )
    automatic = PolicyStructuringJobQueue(sample.url).claim_next_job("synthetic-automatic")
    assert automatic is not None and automatic.id != new.id
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s", (new.id,)
            ).fetchone()["value"]
            == before
        )
    recovered = queue.claim_next_job(WORKER)
    assert recovered is not None and recovered.id == new.id and recovered.attempts == 2
    assert queue.claim_next_job(WORKER) is None
    foreign = type(queue)(
        sample.url,
        household_space_id=uuid4(),
        job_id=new.id,
        pipeline_revision=new.pipeline_version,
    )
    assert foreign.claim_next_job(WORKER) is None


def test_automatic_queue_does_not_finalize_exhausted_retained_job(retained_source):
    sample = retained_source
    new = _enqueue(sample)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET attempts=max_attempts WHERE id=%s", (new.id,)
        )
    assert PolicyStructuringJobQueue(sample.url).claim_next_job(WORKER) is None
    assert PolicyStructuringJobQueue(sample.url).get_job(new.id).state == "queued"
    assert _target(sample, new.id).claim_next_job(WORKER) is None
    assert PolicyStructuringJobQueue(sample.url).get_job(new.id).state == "permanently_failed"


def test_database_rejects_unsupported_retained_revision(retained_source):
    sample = retained_source
    with (
        psycopg.connect(_psycopg_url(sample.url)) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute(
            "INSERT INTO policy_structuring_jobs (household_space_id,batch_item_id,"
            "family_member_id,document_version_id,extraction_id,pipeline_version,"
            "processing_mode,resubmission_of_job_id,source_generation_id) "
            "SELECT household_space_id,batch_item_id,family_member_id,document_version_id,"
            "extraction_id,'unsupported-retained-revision','retained',id,%s "
            "FROM policy_structuring_jobs WHERE id=%s",
            (sample.generation, sample.original.id),
        )


def test_database_preserves_initial_uniqueness_and_retained_source_identity(retained_source):
    sample = retained_source
    new = _enqueue(sample)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO policy_structuring_jobs (household_space_id,batch_item_id,"
                "family_member_id,document_version_id,extraction_id,pipeline_version) "
                "SELECT household_space_id,batch_item_id,family_member_id,document_version_id,"
                "extraction_id,pipeline_version FROM policy_structuring_jobs WHERE id=%s",
                (sample.original.id,),
            )
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "UPDATE policy_structuring_jobs SET source_generation_id=%s WHERE id=%s",
                (uuid4(), new.id),
            )
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "UPDATE policy_structuring_jobs SET pipeline_version='unsupported' WHERE id=%s",
                (new.id,),
            )
    _assert_original_preserved(sample)


def test_an_active_original_is_not_resubmitted(retained_source):
    from familycare_worker.retained_policy import RetainedPolicyConflict

    sample = retained_source
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET state='queued',completed_at=NULL WHERE id=%s",
            (sample.original.id,),
        )
    with pytest.raises(RetainedPolicyConflict):
        _enqueue(sample)


def test_migration_refuses_to_discard_retained_processing_history(retained_source):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError

    sample = retained_source
    new = _enqueue(sample)
    with pytest.raises(DBAPIError):
        command.downgrade(Config("apps/api/alembic.ini"), "0064_metadata_proven_prefix")
    assert PolicyStructuringJobQueue(sample.url).get_job(new.id) is not None
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            SUPPORTED_SCHEMA_REVISION,
        )
    _assert_original_preserved(sample)


@pytest.mark.parametrize("already_budgeted", [False, True])
def test_targeted_runner_uses_existing_provider_budget_without_resetting_document_quota(
    retained_source, already_budgeted
):
    from familycare_worker.ai.provider import ProviderResponse
    from familycare_worker.policy_request_budget import PolicyRequestBudget
    from familycare_worker.runner import PolicyStructuringJobRunner

    from workers.analyzer.tests.test_policy_structuring_runner import FakeLoader, RecordingPublisher

    sample = retained_source
    new = _enqueue(sample)
    if already_budgeted:
        with psycopg.connect(_psycopg_url(sample.url)) as connection:
            connection.execute(
                "INSERT INTO policy_provider_requests(job_id,document_id,fingerprint,state) "
                "SELECT %s,document_id,%s,'FAILED' FROM document_versions WHERE id=%s",
                (sample.original.id, "a" * 64, sample.original.document_version_id),
            )
    calls = []

    class Provider:
        def complete(self, **kwargs):
            calls.append(kwargs["schema_name"])
            return ProviderResponse(
                payload={
                    "schema_version": "3",
                    "candidates": [],
                    "ranges": [
                        {
                            "chunk_id": item["chunk_id"],
                            "outcome": "NO_ENROLLMENT_FACTS",
                            "candidate_ids": [],
                        }
                        for item in kwargs["input_payload"]["primary_ranges"]
                    ],
                },
                request_id="synthetic-retained-request",
            )

    runner = PolicyStructuringJobRunner(
        queue=_target(sample, new.id),
        evidence_loader=FakeLoader(()),
        provider=Provider(),
        publisher=RecordingPublisher(),
        request_budget=PolicyRequestBudget(sample.url, per_document=1),
        range_repository=PolicyRangeRepository(sample.url),
    )
    assert runner.run_once(WORKER)
    assert calls == ([] if already_budgeted else ["policy_range_structurer_v3"])
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM policy_provider_requests").fetchone() == (
            1,
        )
    _assert_original_preserved(sample)


def test_generation_change_during_provider_call_cannot_publish_or_retransmit(retained_source):
    from familycare_worker.ai.provider import ProviderResponse
    from familycare_worker.policy_request_budget import PolicyRequestBudget
    from familycare_worker.runner import PolicyStructuringJobRunner

    from workers.analyzer.tests.test_policy_structuring_runner import FakeLoader, RecordingPublisher

    sample = retained_source
    new = _enqueue(sample)
    calls = []

    class Provider:
        def complete(self, **kwargs):
            calls.append(1)
            with psycopg.connect(_psycopg_url(sample.url)) as connection:
                connection.execute(
                    "UPDATE document_structure_generations SET is_current=false WHERE id=%s",
                    (sample.generation,),
                )
            return ProviderResponse(
                payload={
                    "schema_version": "3",
                    "candidates": [],
                    "ranges": [
                        {
                            "chunk_id": item["chunk_id"],
                            "outcome": "NO_ENROLLMENT_FACTS",
                            "candidate_ids": [],
                        }
                        for item in kwargs["input_payload"]["primary_ranges"]
                    ],
                },
                request_id="synthetic-retained-late-response",
            )

    runner = PolicyStructuringJobRunner(
        queue=_target(sample, new.id),
        evidence_loader=FakeLoader(()),
        provider=Provider(),
        publisher=RecordingPublisher(),
        request_budget=PolicyRequestBudget(sample.url),
        range_repository=PolicyRangeRepository(sample.url),
    )
    assert runner.run_once(WORKER)
    assert not runner.run_once(WORKER)
    assert calls == [1]
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM policy_range_candidate_sources WHERE job_id=%s", (new.id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM document_policy_ranges WHERE job_id=%s AND state<>'PENDING'",
            (new.id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM policy_provider_requests WHERE job_id=%s", (new.id,)
        ).fetchone() == (1,)
    _assert_original_preserved(sample)

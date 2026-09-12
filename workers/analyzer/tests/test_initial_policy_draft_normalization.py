"""New initial analysis verifies a preserved normalized draft before publication."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.ai.provider import ProviderResponse, ProviderUnavailableError
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    StructurerCandidate,
)
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_draft_replay import PolicyDraftNormalizationRepository
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeRepository
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_range_enrollment_integration import (
    WORKER,
    _psycopg_url,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_range_enrollment_integration import (
    enrollment_database as enrollment_database,
)
from workers.analyzer.tests.test_policy_range_repository import _one_contract

pytestmark = pytest.mark.integration
INITIAL = "policy-range-normalized-v1"


@pytest.fixture()
def initial_source(enrollment_database):
    url, previous = enrollment_database
    job = replace(previous, pipeline_version=INITIAL)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET pipeline_version=%s WHERE id=%s",
            (INITIAL, job.id),
        )
        connection.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (job.family_member_id,),
        )
        connection.execute(
            "UPDATE extraction_blocks SET text=%s WHERE reading_order=0 "
            "AND page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (
                "보험증권 가입금액\n증권번호: synthetic-normalization-001\n"
                "피보험자: Family Member A\nSample Insurer Sample Plan\n"
                "Synthetic Inpatient Rider fixed sum assured: 251 KRW",
                job.extraction_id,
            ),
        )
    ranges = PolicyRangeRepository(url)
    terms = PolicyEvidenceLoader(url).load_member_terms(
        household_space_id=job.household_space_id, family_member_id=job.family_member_id
    )
    work = ranges.next(job, WORKER, sensitive_terms=terms)
    assert work is not None
    initial, _ = _one_contract(work)
    primary = work.envelope.primary_evidence_ids[0]
    policy = initial.candidates[0].model_copy(
        update={
            "fields": (
                *initial.candidates[0].fields,
                CandidateField(
                    field_id="contract_start", value="2025-02-03", evidence_ids=(primary,)
                ),
            )
        }
    )
    rider = StructurerCandidate(
        schema_version="1",
        candidate_id=uuid4(),
        candidate_kind="rider",
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(primary,))
            for key, value in (
                ("rider_name", "Synthetic Inpatient Rider"),
                ("sum_assured", 251),
                ("currency", "KRW"),
            )
        ),
    )
    raw = initial.model_copy(
        update={
            "candidates": (policy, rider),
            "ranges": (
                initial.ranges[0].model_copy(
                    update={"candidate_ids": (policy.candidate_id, rider.candidate_id)}
                ),
                *initial.ranges[1:],
            ),
        }
    )
    return url, job, work, raw


@pytest.mark.parametrize("mode", ["automatic", "retained"])
def test_initial_draft_is_normalized_before_verifier_and_partial_loss_stays_review(
    initial_source, mode
):
    url, job, work, raw = initial_source
    ranges = PolicyRangeRepository(url)
    if mode == "retained":
        from familycare_worker.retained_policy import (
            RetainedPolicyJobQueue,
            RetainedPolicyRepository,
        )

        original = job
        generation = work.generation_id
        automatic = PolicyStructuringJobQueue(url)
        terms = PolicyEvidenceLoader(url).load_member_terms(
            household_space_id=job.household_space_id, family_member_id=job.family_member_id
        )
        while job is not None:
            current_work = ranges.next(job, WORKER, sensitive_terms=terms)
            assert current_work is not None
            ranges.reject(job, WORKER, current_work)
            job = automatic.claim_next_job(WORKER)
        queued = RetainedPolicyRepository(url).enqueue(
            household_space_id=original.household_space_id,
            source_job_id=original.id,
            expected_generation_id=generation,
        )
        job = RetainedPolicyJobQueue(
            url,
            household_space_id=original.household_space_id,
            job_id=queued.id,
        ).claim_next_job(WORKER)
        assert job is not None and job.pipeline_version == "retained-policy-association-v6"
        work = ranges.next(job, WORKER, sensitive_terms=terms)
        assert work is not None
    calls = []

    class Provider:
        def complete(self, **kwargs):
            calls.append(kwargs["schema_name"])
            if kwargs["schema_name"] == "policy_range_structurer_v3":
                return ProviderResponse(
                    payload=raw.model_dump(mode="json"), request_id="synthetic-initial-structurer"
                )
            assert kwargs["schema_name"] == "policy_candidate_batch_verifier_v2"
            candidates = kwargs["input_payload"]["candidates"]
            by_kind = {item["candidate_kind"]: item for item in candidates}
            rider_fields = {item["field_id"]: item for item in by_kind["rider"]["fields"]}
            assert "rider_key" in rider_fields, "initial normalization must precede verifier"
            assert rider_fields["rider_key"]["value"] == "Synthetic Inpatient Rider"
            assert (
                rider_fields["rider_key"]["evidence_ids"]
                == rider_fields["rider_name"]["evidence_ids"]
            )
            assert "contract_start" not in {
                item["field_id"] for item in by_kind["policy_contract"]["fields"]
            }
            return ProviderResponse(
                request_id="synthetic-initial-verifier",
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": item["candidate_id"],
                            "decision": "approved",
                            "evidence_ids": sorted(
                                {key for field in item["fields"] for key in field["evidence_ids"]}
                            ),
                            "issue_codes": [],
                        }
                        for item in candidates
                    ],
                },
            )

    runner = PolicyStructuringJobRunner(
        queue=PolicyStructuringJobQueue(url),
        evidence_loader=PolicyEvidenceLoader(url),
        provider=Provider(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=ranges,
        request_budget=PolicyRequestBudget(url),
    )
    runner._run_range(job, WORKER)
    assert calls == ["policy_range_structurer_v3", "policy_candidate_batch_verifier_v2"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        receipt = connection.execute(
            "SELECT * FROM policy_range_replay_sources WHERE job_id=%s AND envelope_id=%s",
            (job.id, work.envelope.envelope_id),
        ).fetchone()
        assert receipt is not None and receipt["origin"] == "initial"
        assert receipt["source_job_id"] == job.id and receipt["partial"] is True
        assert {item["reason"] for item in receipt["adjustments_json"]} == {
            "OPTIONAL_FIELD_UNSUPPORTED",
            "RIDER_KEY_DERIVED_FROM_NAME",
        }
        assert connection.execute(
            "SELECT response_json FROM policy_provider_requests WHERE id=%s",
            (receipt["source_provider_request_id"],),
        ).fetchone()["response_json"] == raw.model_dump(mode="json")
        assert (
            connection.execute(
                "SELECT state FROM document_policy_ranges WHERE job_id=%s AND position=0",
                (job.id,),
            ).fetchone()["state"]
            == "REVIEW"
        )
        candidates = connection.execute(
            "SELECT status,generator_version FROM analysis_candidate_versions "
            "WHERE structuring_job_id=%s",
            (job.id,),
        ).fetchall()
        assert len(candidates) == 2 and all(item["status"] == "AI_VERIFIED" for item in candidates)
        assert {item["generator_version"] for item in candidates} == {
            "policy-draft-normalization-v1"
        }
    assert RangeEnrollmentProjector(url).project_pending() == 2


def _raw_request(url, job, batch, *, payload=None, request_id="synthetic-normalization-request"):
    with psycopg.connect(_psycopg_url(url)) as connection:
        return connection.execute(
            "INSERT INTO policy_provider_requests(job_id,document_id,fingerprint,state,"
            "response_json,request_id) SELECT %s,document_id,%s,'SUCCEEDED',%s,%s "
            "FROM document_versions WHERE id=%s RETURNING id",
            (
                job.id,
                uuid4().hex * 2,
                Jsonb(batch.model_dump(mode="json") if payload is None else payload),
                request_id,
                job.document_version_id,
            ),
        ).fetchone()[0]


def test_normalization_preserves_raw_uuid_spelling_and_receipt_is_immutable(initial_source):
    url, job, work, raw = initial_source
    payload = raw.model_dump(mode="json")
    # Valid UUID spelling is normalized by the schema, but the raw journal stays exact.
    payload["candidates"][0]["candidate_id"] = payload["candidates"][0]["candidate_id"].upper()
    request = _raw_request(url, job, raw, payload=payload)
    repository = PolicyDraftNormalizationRepository(url)
    draft = repository.normalize(job, WORKER, work, raw, "synthetic-normalization-request")
    assert repository.prepare(job, WORKER, work) == draft
    assert repository.normalize(job, WORKER, work, raw, draft.request_id) == draft
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        receipt = connection.execute(
            "SELECT * FROM policy_range_replay_sources WHERE job_id=%s", (job.id,)
        ).fetchone()
        assert receipt["source_provider_request_id"] == request
        assert (
            receipt["source_response_hash"]
            == hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        for sql in (
            "UPDATE policy_range_replay_sources SET origin='replay' WHERE job_id=%s",
            "DELETE FROM policy_range_replay_sources WHERE job_id=%s",
        ):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(sql, (job.id,))


def test_concurrent_normalization_retains_one_identical_receipt(initial_source):
    url, job, work, raw = initial_source
    _raw_request(url, job, raw)
    repository = PolicyDraftNormalizationRepository(url)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                repository.normalize, job, WORKER, work, raw, "synthetic-normalization-request"
            )
            for _ in range(2)
        ]
        drafts = [future.result(timeout=15) for future in futures]
    assert drafts[0] == drafts[1]
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM policy_range_replay_sources WHERE job_id=%s", (job.id,)
        ).fetchone() == (1,)


@pytest.mark.parametrize("mutation", ["duplicate_request", "wrong_batch", "missing_request"])
def test_normalization_requires_one_exact_successful_raw_response(initial_source, mutation):
    url, job, work, raw = initial_source
    _raw_request(url, job, raw)
    selected = "synthetic-normalization-request"
    if mutation == "duplicate_request":
        _raw_request(url, job, raw)
    elif mutation == "wrong_batch":
        raw = raw.model_copy(update={"candidates": ()})
    else:
        selected = "synthetic-missing-request"
    with pytest.raises(PolicyRangeConflict):
        PolicyDraftNormalizationRepository(url).normalize(job, WORKER, work, raw, selected)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM policy_range_replay_sources WHERE job_id=%s", (job.id,)
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "mutation", ["member", "generation", "batch", "extraction", "lease", "plan"]
)
def test_source_change_blocks_initial_draft_reuse_and_save(initial_source, mutation):
    url, job, work, raw = initial_source
    _raw_request(url, job, raw)
    repository = PolicyDraftNormalizationRepository(url)
    draft = repository.normalize(job, WORKER, work, raw, "synthetic-normalization-request")
    with psycopg.connect(_psycopg_url(url)) as connection:
        if mutation == "member":
            connection.execute(
                "UPDATE family_members SET display_name='Synthetic Changed Member' WHERE id=%s",
                (job.family_member_id,),
            )
        elif mutation == "generation":
            connection.execute(
                "UPDATE document_structure_generations SET is_current=false WHERE id=%s",
                (work.generation_id,),
            )
        elif mutation == "batch":
            connection.execute(
                "UPDATE document_batches SET state='cancelled',completed_at=clock_timestamp() "
                "WHERE id IN "
                "(SELECT batch_id FROM document_batch_items WHERE id=%s)",
                (job.batch_item_id,),
            )
        elif mutation == "plan":
            connection.execute(
                "UPDATE document_policy_range_plans SET state='PARTIAL' WHERE job_id=%s", (job.id,)
            )
        elif mutation == "extraction":
            connection.execute(
                "UPDATE extractions SET status='failed',succeeded_at=NULL WHERE id=%s",
                (job.extraction_id,),
            )
        else:
            connection.execute(
                "UPDATE policy_structuring_jobs SET lease_expires_at=clock_timestamp() "
                "-interval '1 second' WHERE id=%s",
                (job.id,),
            )
    for action in (
        lambda: repository.prepare(job, WORKER, work),
        lambda: repository.assert_current(job, WORKER, work),
        lambda: PolicyRangeRepository(url).save(
            job,
            WORKER,
            work,
            draft.batch,
            CandidatePipelineResult(classification="SUCCESS", candidates=()),
        ),
    ):
        with pytest.raises(PolicyRangeConflict):
            action()


def test_new_pipeline_cannot_publish_without_receipt_or_change_receipted_batch(initial_source):
    url, job, work, raw = initial_source
    ranges = PolicyRangeRepository(url)
    result = CandidatePipelineResult(classification="SUCCESS", candidates=())
    with pytest.raises(PolicyRangeConflict):
        ranges.save(job, WORKER, work, raw, result)
    _raw_request(url, job, raw)
    PolicyDraftNormalizationRepository(url).normalize(
        job, WORKER, work, raw, "synthetic-normalization-request"
    )
    with pytest.raises(PolicyRangeConflict):
        ranges.save(job, WORKER, work, raw, result)


def test_unbudgeted_initial_pipeline_stops_before_provider(initial_source):
    url, job, _, _ = initial_source

    class Forbidden:
        def complete(self, **kwargs):
            pytest.fail("unbudgeted provider call")

    runner = PolicyStructuringJobRunner(
        queue=PolicyStructuringJobQueue(url),
        evidence_loader=PolicyEvidenceLoader(url),
        provider=Forbidden(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=PolicyRangeRepository(url),
    )
    with pytest.raises(ValueError, match="durable request accounting"):
        runner._run_range(job, WORKER)


def test_all_unproven_candidates_leave_full_range_review_without_verifier(initial_source):
    url, job, work, raw = initial_source
    candidate = raw.candidates[0].model_copy(
        update={
            "fields": (
                CandidateField(
                    field_id="product_name",
                    value="Unsupported Synthetic Plan",
                    evidence_ids=(work.envelope.primary_evidence_ids[0],),
                ),
            ),
        }
    )
    raw = raw.model_copy(
        update={
            "candidates": (candidate,),
            "ranges": (
                raw.ranges[0].model_copy(update={"candidate_ids": (candidate.candidate_id,)}),
                *raw.ranges[1:],
            ),
        }
    )
    calls = []

    class Provider:
        def complete(self, **kwargs):
            calls.append(kwargs["schema_name"])
            assert calls == ["policy_range_structurer_v3"]
            return ProviderResponse(
                payload=raw.model_dump(mode="json"), request_id="synthetic-unproven-raw"
            )

    PolicyStructuringJobRunner(
        queue=PolicyStructuringJobQueue(url),
        evidence_loader=PolicyEvidenceLoader(url),
        provider=Provider(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=PolicyRangeRepository(url),
        request_budget=PolicyRequestBudget(url),
    )._run_range(job, WORKER)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT state,result_json FROM document_policy_ranges WHERE job_id=%s "
            "AND envelope_id=%s",
            (job.id, work.envelope.envelope_id),
        ).fetchone()
        assert row["state"] == "REVIEW"
        batch = row["result_json"]["batch"]
        assert batch["candidates"] == []
        assert len(batch["ranges"]) == len(raw.ranges)
        assert batch["ranges"][0]["outcome"] == "UNRESOLVED"
        assert row["result_json"]["draft_normalization"]["partial"] is True
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE structuring_job_id=%s",
                (job.id,),
            ).fetchone()["n"]
            == 0
        )


def test_verifier_failure_keeps_normalized_draft_and_retry_skips_structurer(initial_source):
    url, job, work, raw = initial_source
    calls = []

    class Provider:
        def complete(self, **kwargs):
            name = kwargs["schema_name"]
            calls.append(name)
            if name == "policy_range_structurer_v3":
                assert calls.count(name) == 1
                return ProviderResponse(
                    payload=raw.model_dump(mode="json"), request_id="synthetic-resume-raw"
                )
            if calls.count(name) == 1:
                raise ProviderUnavailableError
            return ProviderResponse(
                request_id="synthetic-resume-verifier",
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": candidate["candidate_id"],
                            "decision": "approved",
                            "issue_codes": [],
                            "evidence_ids": sorted(
                                {
                                    key
                                    for field in candidate["fields"]
                                    for key in field["evidence_ids"]
                                }
                            ),
                        }
                        for candidate in kwargs["input_payload"]["candidates"]
                    ],
                },
            )

    runner = PolicyStructuringJobRunner(
        queue=PolicyStructuringJobQueue(url),
        evidence_loader=PolicyEvidenceLoader(url),
        provider=Provider(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=PolicyRangeRepository(url),
        request_budget=PolicyRequestBudget(url),
    )
    runner._run_range(job, WORKER)
    with psycopg.connect(_psycopg_url(url)) as connection:
        receipt = connection.execute(
            "SELECT to_jsonb(r) FROM policy_range_replay_sources r WHERE job_id=%s", (job.id,)
        ).fetchone()[0]
        assert connection.execute(
            "SELECT count(*) FROM analysis_candidate_versions WHERE structuring_job_id=%s",
            (job.id,),
        ).fetchone() == (0,)
        connection.execute(
            "UPDATE policy_structuring_jobs SET available_at=clock_timestamp() WHERE id=%s",
            (job.id,),
        )
    resumed = runner.queue.claim_next_job(WORKER)
    assert resumed is not None and resumed.id == job.id
    runner._run_range(resumed, WORKER)
    assert calls == [
        "policy_range_structurer_v3",
        "policy_candidate_batch_verifier_v2",
        "policy_candidate_batch_verifier_v2",
    ]
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(r) FROM policy_range_replay_sources r WHERE job_id=%s", (job.id,)
            ).fetchone()[0]
            == receipt
        )
        assert connection.execute(
            "SELECT count(*) FROM analysis_candidate_versions WHERE structuring_job_id=%s "
            "AND status='AI_VERIFIED'",
            (job.id,),
        ).fetchone() == (2,)


@pytest.mark.parametrize("stage", ["structurer", "verifier"])
def test_source_changed_during_provider_call_cannot_publish(initial_source, stage):
    url, job, work, raw = initial_source
    calls = []

    class Provider:
        def complete(self, **kwargs):
            name = kwargs["schema_name"]
            calls.append(name)
            structurer = name == "policy_range_structurer_v3"
            if structurer == (stage == "structurer"):
                with psycopg.connect(_psycopg_url(url)) as connection:
                    connection.execute(
                        "UPDATE document_structure_generations SET is_current=false WHERE id=%s",
                        (work.generation_id,),
                    )
            if structurer:
                return ProviderResponse(
                    payload=raw.model_dump(mode="json"), request_id="synthetic-racing-raw"
                )
            return ProviderResponse(
                request_id="synthetic-racing-verifier",
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": candidate["candidate_id"],
                            "decision": "approved",
                            "issue_codes": [],
                            "evidence_ids": sorted(
                                {
                                    key
                                    for field in candidate["fields"]
                                    for key in field["evidence_ids"]
                                }
                            ),
                        }
                        for candidate in kwargs["input_payload"]["candidates"]
                    ],
                },
            )

    runner = PolicyStructuringJobRunner(
        queue=PolicyStructuringJobQueue(url),
        evidence_loader=PolicyEvidenceLoader(url),
        provider=Provider(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=PolicyRangeRepository(url),
        request_budget=PolicyRequestBudget(url),
    )
    with pytest.raises(PolicyRangeConflict):
        runner._run_range(job, WORKER)
    assert len(calls) == (1 if stage == "structurer" else 2)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM analysis_candidate_versions WHERE structuring_job_id=%s",
            (job.id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM policy_provider_requests WHERE job_id=%s AND state='SUCCEEDED'",
            (job.id,),
        ).fetchone() == (len(calls),)

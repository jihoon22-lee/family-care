"""Historical v4 draft replay publishes only after fresh budgeted verification."""

from uuid import uuid4

import psycopg
import pytest
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker import retained_policy
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    StructurerCandidate,
)
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_range_enrollment_integration import (
    WORKER,
    _psycopg_url,
    _retain_contract,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_range_enrollment_integration import (
    enrollment_database as enrollment_database,
)
from workers.analyzer.tests.test_policy_range_repository import _no_facts, _one_contract

pytestmark = pytest.mark.integration


def test_reduced_draft_retains_partial_receipt_and_publishes_proven_enrollment(
    enrollment_database,
    monkeypatch,
):
    # v5 deliberately rejects earlier privacy packets; this exercises the
    # historical v4 replay/publication contract without weakening that fence.
    monkeypatch.setattr(
        retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", "retained-policy-association-v4"
    )
    url, original = enrollment_database
    _retain_contract(url, original)
    ranges = PolicyRangeRepository(url)
    automatic = PolicyStructuringJobQueue(url)
    while (remaining := automatic.claim_next_job(WORKER)) is not None:
        assert remaining.id == original.id
        work = ranges.next(remaining, WORKER, sensitive_terms=("Family Member A",))
        ranges.save(
            remaining,
            WORKER,
            work,
            _no_facts(work),
            CandidatePipelineResult(classification="SUCCESS", candidates=()),
        )
    with psycopg.connect(_psycopg_url(url)) as connection:
        generation = connection.execute(
            "SELECT generation_id FROM document_policy_range_plans WHERE job_id=%s",
            (original.id,),
        ).fetchone()[0]
    arguments = dict(
        household_space_id=original.household_space_id,
        source_job_id=original.id,
        expected_generation_id=generation,
    )
    with monkeypatch.context() as historical:
        historical.setattr(
            retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", "retained-policy-association-v2"
        )
        old = retained_policy.RetainedPolicyRepository(url).enqueue(
            **arguments, pipeline_revision="retained-policy-association-v2"
        )
        old_queue = retained_policy.RetainedPolicyJobQueue(
            url,
            household_space_id=old.household_space_id,
            job_id=old.id,
            pipeline_revision="retained-policy-association-v2",
        )
    old = old_queue.claim_next_job(WORKER)
    ranges = PolicyRangeRepository(url)
    member_terms = PolicyEvidenceLoader(url).load_member_terms(
        household_space_id=old.household_space_id,
        family_member_id=old.family_member_id,
    )
    work = ranges.next(old, WORKER, sensitive_terms=member_terms)
    batch, _ = _one_contract(work)
    primary = work.envelope.primary_evidence_ids[0]
    policy = batch.candidates[0].model_copy(
        update={
            "fields": (
                *batch.candidates[0].fields,
                CandidateField(
                    field_id="contract_start", value="2025-01-01", evidence_ids=(primary,)
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
                ("rider_name", "Sample Rider"),
                ("sum_assured", 317),
                ("currency", "KRW"),
            )
        ),
    )
    batch = batch.model_copy(
        update={
            "candidates": (policy, rider),
            "ranges": (
                batch.ranges[0].model_copy(
                    update={"candidate_ids": (policy.candidate_id, rider.candidate_id)}
                ),
                *batch.ranges[1:],
            ),
        }
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        request_id = connection.execute(
            "INSERT INTO policy_provider_requests(job_id,document_id,fingerprint,state,"
            "response_json,request_id) SELECT %s,document_id,%s,'SUCCEEDED',%s,"
            "'synthetic-original-draft' FROM document_versions "
            "WHERE id=%s RETURNING id",
            (old.id, "e" * 64, Jsonb(batch.model_dump(mode="json")), old.document_version_id),
        ).fetchone()[0]
    ranges.reject(old, WORKER, work)
    target = retained_policy.RetainedPolicyRepository(url).enqueue(
        **arguments, pipeline_revision="retained-policy-association-v4"
    )
    calls = []

    class Verifier:
        def complete(self, **kwargs):
            calls.append(kwargs["schema_name"])
            return ProviderResponse(
                request_id="synthetic-fresh-verifier",
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": candidate["candidate_id"],
                            "decision": "approved",
                            "evidence_ids": sorted(
                                {
                                    key
                                    for field in candidate["fields"]
                                    for key in field["evidence_ids"]
                                }
                            ),
                            "issue_codes": [],
                        }
                        for candidate in kwargs["input_payload"]["candidates"]
                    ],
                },
            )

    queue = retained_policy.RetainedPolicyJobQueue(
        url,
        household_space_id=target.household_space_id,
        job_id=target.id,
        pipeline_revision="retained-policy-association-v4",
    )
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=PolicyEvidenceLoader(url),
        provider=Verifier(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=ranges,
        request_budget=PolicyRequestBudget(url),
        replay_repository=PolicyDraftReplayRepository(url, source_provider_request_id=request_id),
    )
    assert runner.run_once(WORKER)
    assert calls == ["policy_candidate_batch_verifier_v2"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        candidates = connection.execute(
            "SELECT status,generator_version FROM analysis_candidate_versions "
            "WHERE structuring_job_id=%s",
            (target.id,),
        ).fetchall()
        assert len(candidates) == 2 and all(row["status"] == "AI_VERIFIED" for row in candidates)
        assert {row["generator_version"] for row in candidates} == {"policy-draft-normalization-v1"}
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM policy_provider_requests WHERE job_id=%s", (target.id,)
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT state FROM document_policy_ranges WHERE job_id=%s AND position=0",
                (target.id,),
            ).fetchone()["state"]
            == "REVIEW"
        )
        assert connection.execute(
            "SELECT response_json FROM policy_provider_requests WHERE id=%s", (request_id,)
        ).fetchone()["response_json"] == batch.model_dump(mode="json")
    assert RangeEnrollmentProjector(url).project_pending() >= 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM riders WHERE household_space_id=%s AND deleted_at IS NULL",
            (target.household_space_id,),
        ).fetchone() == (1,)

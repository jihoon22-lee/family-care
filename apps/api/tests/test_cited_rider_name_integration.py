"""Fresh cited-row name verification recovers three Riders without replaying old approval."""

from copy import deepcopy

import psycopg
import pytest
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository
from familycare_worker.policy_range_repository import PolicyRangeRepository
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.retained_policy import RetainedPolicyRepository
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row

from apps.api.tests.test_source_scoped_policy_identity_integration import (
    WORKER,
    _drain,
    _psycopg_url,
    _queue,
)
from apps.api.tests.test_source_scoped_policy_identity_integration import (
    deferred_parent as deferred_parent,
)
from apps.api.tests.test_source_scoped_policy_identity_integration import (
    enrollment_database as enrollment_database,
)
from apps.api.tests.test_source_scoped_policy_identity_integration import (
    ranges_database as ranges_database,
)
from apps.api.tests.test_source_scoped_policy_identity_integration import (
    seeded_policy_database as seeded_policy_database,
)
from apps.api.tests.test_source_scoped_policy_identity_integration import (
    structure_database as structure_database,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("deferred_parent", ["cited_name_errors"], indirect=True)
def test_v10_replays_only_names_proven_by_the_original_cited_table_rows(deferred_parent):
    url, original, old, parent_job, _, _, _, parent_calls, _ = deferred_parent
    projector = RangeEnrollmentProjector(url)
    assert len(parent_calls) == 1
    assert projector.project_pending() == 4
    loader = PolicyEvidenceLoader(url)
    terms = loader.load_member_terms(
        household_space_id=old.household_space_id, family_member_id=old.family_member_id
    )
    queue = _queue(url, parent_job)
    _drain(url, queue, queue.claim_next_job(WORKER), terms)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        old_riders = connection.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY id",
            (old.household_space_id,),
        ).fetchall()
        assert {r["display_name"] for r in old_riders} == {f"Sample Rider {i}" for i in (3, 4, 5)}
        policy_before = connection.execute(
            "SELECT * FROM policy_contracts WHERE id=%s", (old_riders[0]["policy_contract_id"],)
        ).fetchone()
        raw = connection.execute(
            "SELECT id,response_json FROM policy_provider_requests WHERE job_id=%s "
            "AND request_id='synthetic-normalization-request'",
            (old.id,),
        ).fetchone()
        raw_riders = [
            c for c in raw["response_json"]["candidates"] if c["candidate_kind"] == "rider"
        ]
        wrong = raw_riders[:3]
        assert [
            next(f["value"] for f in c["fields"] if f["field_id"] == "rider_name") for c in wrong
        ] == ["SampleRider 0", "Sample Rider1", "Wrong Rider 2"]
        wrong_ids = {c["candidate_id"] for c in wrong}
        old_jobs = [old.id, parent_job.id]
        history = {
            table: connection.execute(
                f"SELECT to_jsonb(t) AS value FROM {table} t WHERE {column}=ANY(%s) "
                "ORDER BY to_jsonb(t)::text",
                (old_jobs,),
            ).fetchall()
            for table, column in (
                ("policy_structuring_jobs", "id"),
                ("policy_provider_requests", "job_id"),
                ("policy_range_replay_sources", "job_id"),
                ("document_policy_ranges", "job_id"),
                ("analysis_candidate_versions", "structuring_job_id"),
            )
        }
        fields_before = connection.execute(
            "SELECT to_jsonb(f) AS value FROM analysis_candidate_fields f "
            "JOIN analysis_candidate_versions c "
            "ON c.id=f.candidate_version_id WHERE c.structuring_job_id=ANY(%s) "
            "ORDER BY f.candidate_version_id,f.field_id",
            (old_jobs,),
        ).fetchall()
        generation = connection.execute(
            "SELECT generation_id FROM document_policy_range_plans WHERE job_id=%s", (old.id,)
        ).fetchone()["generation_id"]
        publications = connection.execute(
            "SELECT to_jsonb(p) AS value FROM range_enrollment_publications p "
            "WHERE household_space_id=%s ORDER BY candidate_version_id",
            (old.household_space_id,),
        ).fetchall()
    target = RetainedPolicyRepository(url).enqueue(
        household_space_id=old.household_space_id,
        source_job_id=original.id,
        expected_generation_id=generation,
        pipeline_revision="retained-policy-association-v10",
    )
    queue = _queue(url, target)
    job = queue.claim_next_job(WORKER)
    assert job is not None
    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=terms)
    assert work is not None
    replay = PolicyDraftReplayRepository(url, source_provider_request_id=raw["id"])
    prepared = replay.prepare(job, WORKER, work)
    assert prepared is not None and len(prepared.batch.candidates) == 3
    assert {str(c.candidate_id) for c in prepared.batch.candidates} == wrong_ids
    for index, candidate in enumerate(prepared.batch.candidates):
        assert candidate.candidate_kind == "rider"
        fields = {f.field_id: f for f in candidate.fields}
        assert fields["rider_name"].value == f"Sample Rider {index}"
        assert fields["rider_key"].value == fields["rider_name"].value
        assert fields["sum_assured"].value == 100 + index
        assert fields["currency"].value == "KRW"
        original_name = next(f for f in wrong[index]["fields"] if f["field_id"] == "rider_name")
        assert {str(key) for key in fields["rider_name"].evidence_ids} == set(
            original_name["evidence_ids"]
        )
    calls = []

    class Verifier:
        def complete(self, **kwargs):
            calls.append(deepcopy(kwargs))
            candidates = kwargs["input_payload"]["candidates"]
            assert len(candidates) == 3 and {c["candidate_id"] for c in candidates} == wrong_ids
            assert all(c["candidate_kind"] == "rider" for c in candidates)
            return ProviderResponse(
                request_id="synthetic-v10-cited-name-verifier",
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": c["candidate_id"],
                            "decision": "approved",
                            "issue_codes": [],
                            "evidence_ids": sorted(
                                {key for f in c["fields"] for key in f["evidence_ids"]}
                            ),
                        }
                        for c in candidates
                    ],
                },
            )

    PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=loader,
        provider=Verifier(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=ranges,
        request_budget=PolicyRequestBudget(url),
        replay_repository=replay,
    )._run_range(job, WORKER)
    assert len(calls) == 1 and calls[0]["schema_name"] == "policy_candidate_batch_verifier_v2"
    assert projector.project_pending() == 3
    assert projector.project_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        all_riders = connection.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY id",
            (old.household_space_id,),
        ).fetchall()
        assert len(all_riders) == 6 and all(
            r["policy_contract_id"] == policy_before["id"] for r in all_riders
        )
        assert {r["display_name"] for r in all_riders} == {f"Sample Rider {i}" for i in range(6)}
        assert {r["display_name"]: r["insured_amount"] for r in all_riders} == {
            f"Sample Rider {i}": 100 + i for i in range(6)
        }
        assert all(r["currency"] == "KRW" for r in all_riders)
        assert all(r in all_riders for r in old_riders)
        assert (
            connection.execute(
                "SELECT * FROM policy_contracts WHERE id=%s", (policy_before["id"],)
            ).fetchone()
            == policy_before
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE structuring_job_id=%s "
                "AND candidate_kind='rider' AND status='AI_VERIFIED' AND published_at IS NOT NULL",
                (job.id,),
            ).fetchone()["n"]
            == 3
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE structuring_job_id=%s "
                "AND candidate_kind='policy_contract'",
                (job.id,),
            ).fetchone()["n"]
            == 0
        )
        receipt = connection.execute(
            "SELECT normalization_revision,adjustments_json FROM policy_range_replay_sources "
            "WHERE job_id=%s",
            (job.id,),
        ).fetchone()
        assert receipt["normalization_revision"] == "policy-draft-normalization-v5"
        assert {
            a["candidate_id"]
            for a in receipt["adjustments_json"]
            if a["reason"] == "RIDER_NAME_RESTORED_FROM_CITED_ROW"
        } == wrong_ids
        assert (
            sum(
                a["reason"] == "PRIOR_VERIFIED_CANDIDATE_PRESERVED"
                for a in receipt["adjustments_json"]
            )
            == 4
        )
        for table, prior in history.items():
            column = (
                "id"
                if table == "policy_structuring_jobs"
                else "structuring_job_id"
                if table == "analysis_candidate_versions"
                else "job_id"
            )
            assert (
                connection.execute(
                    f"SELECT to_jsonb(t) AS value FROM {table} t WHERE {column}=ANY(%s) "
                    "ORDER BY to_jsonb(t)::text",
                    (old_jobs,),
                ).fetchall()
                == prior
            )
        assert (
            connection.execute(
                "SELECT to_jsonb(f) AS value FROM analysis_candidate_fields f "
                "JOIN analysis_candidate_versions c "
                "ON c.id=f.candidate_version_id WHERE c.structuring_job_id=ANY(%s) "
                "ORDER BY f.candidate_version_id,f.field_id",
                (old_jobs,),
            ).fetchall()
            == fields_before
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM range_enrollment_publications p "
                "WHERE candidate_version_id=ANY(%s::uuid[]) ORDER BY candidate_version_id",
                ([p["value"]["candidate_version_id"] for p in publications],),
            ).fetchall()
            == publications
        )

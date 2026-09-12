"""An explicit scoped recheck preserves an earlier missing-evidence decision."""

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


@pytest.mark.parametrize(
    "target_revision", ["retained-policy-association-v11", "retained-policy-association-v12"]
)
@pytest.mark.parametrize("deferred_parent", ["cited_name_errors"], indirect=True)
def test_scoped_revision_rechecks_only_unapproved_candidate_and_preserves_prior_results(
    deferred_parent,
    target_revision,
):
    url, original, old, parent, *_ = deferred_parent
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending() == 4
    loader = PolicyEvidenceLoader(url)
    terms = loader.load_member_terms(
        household_space_id=old.household_space_id, family_member_id=old.family_member_id
    )
    _drain(url, _queue(url, parent), _queue(url, parent).claim_next_job(WORKER), terms)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        raw = connection.execute(
            "SELECT id FROM policy_provider_requests WHERE job_id=%s "
            "AND request_id='synthetic-normalization-request'",
            (old.id,),
        ).fetchone()["id"]
        generation = connection.execute(
            "SELECT generation_id FROM document_policy_range_plans WHERE job_id=%s", (old.id,)
        ).fetchone()["generation_id"]
    calls = []
    failed_id = None

    class Verifier:
        def __init__(self, scoped):
            self.scoped = scoped

        def complete(self, **kwargs):
            nonlocal failed_id
            calls.append(deepcopy(kwargs))
            candidates = kwargs["input_payload"]["candidates"]
            if self.scoped:
                assert len(candidates) == 1 and candidates[0]["candidate_id"] == failed_id
                assert len(kwargs["input_payload"]["evidence"]) < len(
                    calls[0]["input_payload"]["evidence"]
                )
                assert len(kwargs["input_payload"]["evidence"]) == 2
                assert kwargs["system_instruction"] != calls[0]["system_instruction"]
            else:
                assert len(candidates) == 3
                failed_id = candidates[0]["candidate_id"]
            return ProviderResponse(
                request_id=f"synthetic-scoped-verifier-{self.scoped}",
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": c["candidate_id"],
                            "decision": "needs_review"
                            if not self.scoped and c["candidate_id"] == failed_id
                            else "approved",
                            "issue_codes": ["MISSING_EVIDENCE"]
                            if not self.scoped and c["candidate_id"] == failed_id
                            else [],
                            "evidence_ids": sorted(
                                {key for f in c["fields"] for key in f["evidence_ids"]}
                            ),
                        }
                        for c in candidates
                    ],
                },
            )

    def run(revision, scoped):
        target = RetainedPolicyRepository(url).enqueue(
            household_space_id=old.household_space_id,
            source_job_id=original.id,
            expected_generation_id=generation,
            pipeline_revision=revision,
        )
        queue = _queue(url, target)
        job = queue.claim_next_job(WORKER)
        assert job is not None
        PolicyStructuringJobRunner(
            queue=queue,
            evidence_loader=loader,
            provider=Verifier(scoped),
            publisher=PolicyCandidatePublisher(url),
            range_repository=PolicyRangeRepository(url),
            request_budget=PolicyRequestBudget(url, per_document=8, daily=8),
            replay_repository=PolicyDraftReplayRepository(url, source_provider_request_id=raw),
        )._run_range(job, WORKER)
        _drain(url, queue, queue.claim_next_job(WORKER), terms)
        return target

    previous = run("retained-policy-association-v10", False)
    assert projector.project_pending() == 2
    tables = {
        "document_policy_ranges": "job_id",
        "policy_range_replay_sources": "job_id",
        "analysis_candidate_versions": "structuring_job_id",
        "policy_provider_requests": "job_id",
    }

    def history(connection):
        return {
            t: connection.execute(
                f"SELECT to_jsonb(t) AS value FROM {t} t WHERE {column}=%s "
                "ORDER BY to_jsonb(t)::text",
                (previous.id,),
            ).fetchall()
            for t, column in tables.items()
        }

    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        before = history(connection)
        riders = connection.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY id",
            (old.household_space_id,),
        ).fetchall()
        assert len(riders) == 5
    target = run(target_revision, True)
    assert len(calls) == 2
    assert projector.project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert history(connection) == before
        current = connection.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY id",
            (old.household_space_id,),
        ).fetchall()
        assert len(current) == 6 and all(r in current for r in riders)
        row = connection.execute(
            "SELECT result_json FROM document_policy_ranges WHERE job_id=%s "
            "AND result_json ? 'verification_scope'",
            (target.id,),
        ).fetchone()
        assert row["result_json"]["verification_scope"] == {
            "revision": "cited-fields-v1",
            "evidence_ids": [item["evidence_id"] for item in calls[1]["input_payload"]["evidence"]],
        }
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE structuring_job_id=%s "
                "AND status='NEEDS_REVIEW'",
                (previous.id,),
            ).fetchone()["n"]
            == 1
        )

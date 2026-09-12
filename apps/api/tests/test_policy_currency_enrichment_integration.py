"""Currency recovery appends proof while preserving the same six enrolled Riders."""

from copy import deepcopy

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.amount_source import read_operational_amount_source
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


@pytest.mark.parametrize("deferred_parent", ["missing_currency"], indirect=True)
@pytest.mark.parametrize("ledger_edit", [False, True])
def test_v9_currency_only_recovery_preserves_ledger_and_old_proof(deferred_parent, ledger_edit):
    url, original, old, parent_job, *_ = deferred_parent
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending() == 7
    loader = PolicyEvidenceLoader(url)
    terms = loader.load_member_terms(
        household_space_id=original.household_space_id, family_member_id=original.family_member_id
    )
    _drain(url, _queue(url, parent_job), _queue(url, parent_job).claim_next_job(WORKER), terms)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        before = connection.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY display_name",
            (old.household_space_id,),
        ).fetchall()
        assert len(before) == 6 and all(r["currency"] is None for r in before)
        policy_before = connection.execute(
            "SELECT * FROM policy_contracts WHERE id=%s", (before[0]["policy_contract_id"],)
        ).fetchone()
        history = {
            table: connection.execute(
                f"SELECT to_jsonb(t) AS value FROM {table} t WHERE "
                f"{column}=ANY(%s) ORDER BY to_jsonb(t)::text",
                ([old.id, parent_job.id],),
            ).fetchall()
            for table, column in (
                ("policy_provider_requests", "job_id"),
                ("policy_range_replay_sources", "job_id"),
                ("analysis_candidate_versions", "structuring_job_id"),
            )
        }
        raw_id = connection.execute(
            "SELECT id FROM policy_provider_requests WHERE job_id=%s "
            "AND request_id='synthetic-normalization-request'",
            (old.id,),
        ).fetchone()["id"]
        generation = connection.execute(
            "SELECT generation_id FROM document_policy_range_plans WHERE job_id=%s", (old.id,)
        ).fetchone()["generation_id"]
        publications = connection.execute(
            "SELECT to_jsonb(p) AS value FROM range_enrollment_publications p "
            "WHERE household_space_id=%s ORDER BY candidate_version_id",
            (old.household_space_id,),
        ).fetchall()
        if ledger_edit:
            connection.execute(
                "UPDATE riders SET insured_amount=737,version=version+1 WHERE id=%s",
                (before[0]["id"],),
            )
    target = RetainedPolicyRepository(url).enqueue(
        household_space_id=old.household_space_id,
        source_job_id=original.id,
        expected_generation_id=generation,
        pipeline_revision="retained-policy-association-v9",
    )
    queue = _queue(url, target)
    job = queue.claim_next_job(WORKER)
    assert job is not None
    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=terms)
    assert work is not None
    replay = PolicyDraftReplayRepository(url, source_provider_request_id=raw_id)
    prepared = replay.prepare(job, WORKER, work)
    assert prepared is not None and len(prepared.batch.candidates) == 6
    assert all(c.candidate_kind == "rider" for c in prepared.batch.candidates)
    calls = []

    class Verifier:
        def complete(self, **kwargs):
            calls.append(deepcopy(kwargs))
            candidates = kwargs["input_payload"]["candidates"]
            assert len(candidates) == 6
            return ProviderResponse(
                request_id="synthetic-v9-currency-verifier",
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
    assert len(calls) == 1
    assert projector.project_pending() == (5 if ledger_edit else 6)
    assert projector.project_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        after = connection.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY display_name",
            (old.household_space_id,),
        ).fetchall()
        assert len(after) == 6
        for index, (prior, current) in enumerate(zip(before, after, strict=True)):
            assert current["id"] == prior["id"]
            if ledger_edit and index == 0:
                assert current["currency"] is None and current["insured_amount"] == 737
            else:
                assert current["currency"] == "KRW"
                assert {
                    k: v
                    for k, v in current.items()
                    if k not in {"currency", "version", "updated_at"}
                } == {
                    k: v for k, v in prior.items() if k not in {"currency", "version", "updated_at"}
                }
                amount = read_operational_amount_source(
                    connection, HouseholdScope(old.household_space_id), current["id"]
                )
                assert amount.amount_decision == amount.currency_decision == "MATCH"
                assert amount.amount == prior["insured_amount"] and amount.currency == "KRW"
            assert current["version"] == prior["version"] + 1
        assert (
            connection.execute(
                "SELECT * FROM policy_contracts WHERE id=%s", (policy_before["id"],)
            ).fetchone()
            == policy_before
        )
        for table, prior in history.items():
            column = "structuring_job_id" if table == "analysis_candidate_versions" else "job_id"
            assert (
                connection.execute(
                    f"SELECT to_jsonb(t) AS value FROM {table} t WHERE "
                    f"{column}=ANY(%s) ORDER BY to_jsonb(t)::text",
                    ([old.id, parent_job.id],),
                ).fetchall()
                == prior
            )
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM range_enrollment_publications p "
                "WHERE candidate_version_id=ANY(%s::uuid[]) ORDER BY candidate_version_id",
                ([p["value"]["candidate_version_id"] for p in publications],),
            ).fetchall()
            == publications
        )

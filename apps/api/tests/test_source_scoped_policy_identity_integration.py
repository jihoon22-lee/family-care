"""One new parent verification releases unchanged source-bound v7 Riders."""

from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.insurance_documents.schemas import MemberInsuranceDocumentInventoryResponse
from familycare_api.insurance_reconciliation.repository import InsuranceReconciliationRepository
from familycare_api.insurance_reconciliation.schemas import MemberInsuranceReconciliationResponse
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_api.policies.repository import PolicyLedgerRepository
from familycare_api.policies.schemas import PolicyResponse
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    PolicyCandidate,
    StructurerCandidate,
)
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_draft_replay import (
    PolicyDraftNormalizationRepository,
    PolicyDraftReplayRepository,
)
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.retained_policy import RetainedPolicyJobQueue, RetainedPolicyRepository
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row

from apps.api.tests.test_range_enrollment_integration import (
    WORKER,
    _psycopg_url,
)
from apps.api.tests.test_range_enrollment_integration import (
    enrollment_database as enrollment_database,
)
from workers.analyzer.tests.test_initial_policy_draft_normalization import _raw_request

pytestmark = pytest.mark.integration


def _queue(url, job):
    return RetainedPolicyJobQueue(
        url,
        household_space_id=job.household_space_id,
        job_id=job.id,
        pipeline_revision=job.pipeline_version,
    )


def _drain(url, queue, job, terms):
    ranges = PolicyRangeRepository(url)
    while job is not None:
        work = ranges.next(job, WORKER, sensitive_terms=terms)
        if work is not None:
            ranges.reject(job, WORKER, work)
        job = queue.claim_next_job(WORKER)


@pytest.fixture()
def deferred_parent(enrollment_database):
    url, original = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (original.family_member_id,),
        )
        lines = [
            "보험증권 가입금액",
            "증권번호: synthetic-source-scoped-001",
            "피보험자: Family Member A",
            "Sample Insurer",
            "Sample Plan_보험증권",
        ]
        lines += [f"담보명: Sample Rider {i} | 정액 가입금액: {100 + i}원" for i in range(6)]
        connection.execute(
            "UPDATE extraction_blocks SET text=%s WHERE reading_order=0 AND "
            "page_id IN (SELECT id FROM extraction_pages WHERE "
            "extraction_id=%s)",
            ("\n".join(lines), original.extraction_id),
        )
    loader = PolicyEvidenceLoader(url)
    terms = loader.load_member_terms(
        household_space_id=original.household_space_id, family_member_id=original.family_member_id
    )
    ranges = PolicyRangeRepository(url)
    work = ranges.next(original, WORKER, sensitive_terms=terms)
    assert work is not None
    generation = work.generation_id
    _drain(url, PolicyStructuringJobQueue(url), original, terms)
    arguments = dict(
        household_space_id=original.household_space_id,
        source_job_id=original.id,
        expected_generation_id=generation,
    )
    old = RetainedPolicyRepository(url).enqueue(
        **arguments, pipeline_revision="retained-policy-association-v7"
    )
    queue = _queue(url, old)
    old = queue.claim_next_job(WORKER)
    assert old is not None
    work = ranges.next(old, WORKER, sensitive_terms=terms)
    primary = work.envelope.primary_evidence_ids[0]

    def candidate(kind, fields):
        return StructurerCandidate(
            schema_version="1",
            candidate_id=uuid4(),
            candidate_kind=kind,
            fields=tuple(
                CandidateField(field_id=name, value=value, evidence_ids=(primary,))
                for name, value in fields
            ),
        )

    policy = candidate(
        "policy_contract", [("insurer", "Sample Insurer"), ("product_name", "Sample Plan")]
    )
    riders = tuple(
        candidate(
            "rider",
            [
                ("rider_name", f"Sample Rider {i}"),
                ("rider_key", f"Sample Rider {i}"),
                *([("benefit_type", "fixed")] if i != 5 else []),
                ("sum_assured", 100 + i),
                ("currency", "KRW"),
            ],
        )
        for i in range(6)
    )
    batch = PolicyRangeBatch(
        schema_version="3",
        candidates=(policy, *riders),
        ranges=tuple(
            RangeDisposition(
                chunk_id=key,
                outcome="CANDIDATES" if i == 0 else "NO_ENROLLMENT_FACTS",
                candidate_ids=tuple(c.candidate_id for c in (policy, *riders)) if i == 0 else (),
            )
            for i, key in enumerate(work.envelope.primary_chunk_ids)
        ),
    )
    request = _raw_request(url, old, batch)
    draft = PolicyDraftNormalizationRepository(url).normalize(
        old, WORKER, work, batch, "synthetic-normalization-request"
    )
    assert len(draft.batch.candidates) == 7
    result = CandidatePipelineResult(
        classification="NEEDS_REVIEW",
        candidates=tuple(
            PolicyCandidate(
                candidate_id=c.candidate_id,
                candidate_kind=c.candidate_kind,
                fields=c.fields,
                status="NEEDS_REVIEW" if c.candidate_kind == "policy_contract" else "AI_VERIFIED",
                issue_codes=("MISSING_EVIDENCE",) if c.candidate_kind == "policy_contract" else (),
                provider_request_ids=("synthetic-normalization-request", "synthetic-v7-verifier"),
            )
            for c in draft.batch.candidates
        ),
    )
    ranges.save(old, WORKER, work, draft.batch, result)
    _drain(url, queue, queue.claim_next_job(WORKER), terms)
    assert RangeEnrollmentProjector(url).project_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        history = {
            table: connection.execute(
                f"SELECT to_jsonb(t) FROM {table} t WHERE job_id=%s ORDER BY to_jsonb(t)::text",
                (old.id,),
            ).fetchall()
            for table in (
                "policy_provider_requests",
                "policy_range_replay_sources",
                "document_policy_ranges",
            )
        }
        old_fields = connection.execute(
            "SELECT to_jsonb(f) FROM analysis_candidate_fields f JOIN "
            "analysis_candidate_versions c ON c.id=f.candidate_version_id WHERE "
            "c.structuring_job_id=%s ORDER BY f.candidate_version_id,f.field_id",
            (old.id,),
        ).fetchall()
        old_candidates = connection.execute(
            "SELECT id,status FROM analysis_candidate_versions WHERE "
            "structuring_job_id=%s ORDER BY id",
            (old.id,),
        ).fetchall()
    target = RetainedPolicyRepository(url).enqueue(
        **arguments, pipeline_revision="retained-policy-association-v8"
    )
    current = _queue(url, target).claim_next_job(WORKER)
    assert current is not None
    work = ranges.next(current, WORKER, sensitive_terms=terms)
    replay = PolicyDraftReplayRepository(url, source_provider_request_id=request)
    preview = replay.prepare(current, WORKER, work)
    assert preview is not None and [c.candidate_kind for c in preview.batch.candidates] == [
        "policy_contract"
    ]
    assert [f.field_id for f in preview.batch.candidates[0].fields] == ["product_name"]
    calls = []

    class Verifier:
        def complete(self, **kwargs):
            calls.append(deepcopy(kwargs))
            candidates = kwargs["input_payload"]["candidates"]
            assert len(candidates) == 1 and candidates[0]["candidate_kind"] == "policy_contract"
            return ProviderResponse(
                request_id="synthetic-v8-parent-verifier",
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

    runner = PolicyStructuringJobRunner(
        queue=_queue(url, target),
        evidence_loader=loader,
        provider=Verifier(),
        publisher=PolicyCandidatePublisher(url),
        range_repository=ranges,
        request_budget=PolicyRequestBudget(url),
        replay_repository=replay,
    )
    runner._run_range(current, WORKER)
    return url, original, old, current, history, old_fields, old_candidates, calls


def test_v8_parent_publishes_existing_six_riders_without_copy_or_reverification(deferred_parent):
    url, original, old, current, history, old_fields, old_candidates, calls = deferred_parent
    assert len(calls) == 1 and calls[0]["schema_name"] == "policy_candidate_batch_verifier_v2"
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending() == 7
    assert projector.project_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        policies = connection.execute(
            "SELECT * FROM policy_contracts WHERE household_space_id=%s", (old.household_space_id,)
        ).fetchall()
        assert len(policies) == 1
        policy = policies[0]
        assert policy["insurer_display"] is None and policy["insurer_key"] is None
        assert policy["status"] == "unknown"
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM riders WHERE policy_contract_id=%s", (policy["id"],)
            ).fetchone()["n"]
            == 6
        )
        proof = connection.execute(
            "SELECT source_identity_json FROM range_enrollment_publications "
            "WHERE policy_contract_id=%s AND rider_id IS NULL",
            (policy["id"],),
        ).fetchone()["source_identity_json"]
        assert proof["reason_code"] == "INSURER_SOURCE_UNVERIFIED"
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE structuring_job_id=%s",
                (current.id,),
            ).fetchone()["n"]
            == 1
        )
    with psycopg.connect(_psycopg_url(url)) as connection:
        for table, before in history.items():
            assert (
                connection.execute(
                    f"SELECT to_jsonb(t) FROM {table} t WHERE job_id=%s ORDER BY to_jsonb(t)::text",
                    (old.id,),
                ).fetchall()
                == before
            )
        assert (
            connection.execute(
                "SELECT to_jsonb(f) FROM analysis_candidate_fields f JOIN "
                "analysis_candidate_versions c ON c.id=f.candidate_version_id "
                "WHERE c.structuring_job_id=%s ORDER BY "
                "f.candidate_version_id,f.field_id",
                (old.id,),
            ).fetchall()
            == old_fields
        )
        assert (
            connection.execute(
                "SELECT id,status FROM analysis_candidate_versions WHERE "
                "structuring_job_id=%s ORDER BY id",
                (old.id,),
            ).fetchall()
            == old_candidates
        )
        assert connection.execute("SELECT count(*) FROM terms_editions").fetchone() == (0,)
    scope = HouseholdScope(old.household_space_id)
    response = PolicyResponse.from_domain(
        PolicyLedgerRepository(url).get_policy(scope, policy["id"])
    )
    assert (
        response.insurer_key is None
        and response.insurer_unresolved_reason == "INSURER_SOURCE_UNVERIFIED"
    )
    inventory = InsuranceDocumentRepository(url).get_inventory(scope, original.family_member_id)
    serialized = MemberInsuranceDocumentInventoryResponse.from_domain(inventory)
    assert serialized.registered_policies[0].insurer_display is None
    reconciliation = InsuranceReconciliationRepository(url).get_member(
        scope, original.family_member_id
    )
    serialized_reconciliation = MemberInsuranceReconciliationResponse.from_domain(reconciliation)
    assert serialized_reconciliation.orphan_operational_contracts[0].insurer_display is None
    from apps.api.tests.test_metadata_navigation_publication import _migrate

    refused = _migrate(url, "downgrade", "0076_certificate_title_grounding")
    assert refused.returncode != 0
    assert "source scoped policy history prevents downgrade" in refused.stderr


def test_database_cannot_create_unknown_issuer_without_source_provenance(enrollment_database):
    url, job = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        evidence = connection.execute(
            "SELECT id FROM evidence WHERE document_version_id=%s LIMIT 1",
            (job.document_version_id,),
        ).fetchone()[0]
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO "
                "policy_contracts(household_space_id,source_document_version_id,"
                "source_evidence_id,insurer_display,insurer_key,product_display,"
                "product_key,status) "
                "VALUES (%s,%s,%s,NULL,NULL,'Sample "
                "Plan','sample-plan','unknown')",
                (job.household_space_id, job.document_version_id, evidence),
            )
            connection.execute("SET CONSTRAINTS policy_source_scoped_identity_guard IMMEDIATE")

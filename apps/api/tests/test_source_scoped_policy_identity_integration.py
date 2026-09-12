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
from familycare_api.policies.candidate_models import CandidateCorrectionRequest
from familycare_api.policies.candidate_repository import CandidateRepository
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
from psycopg.types.json import Jsonb

from apps.api.tests.test_range_enrollment_integration import (
    WORKER,
    _psycopg_url,
)
from apps.api.tests.test_range_enrollment_integration import (
    enrollment_database as enrollment_database,
)
from apps.api.tests.test_range_enrollment_integration import (
    ranges_database as ranges_database,
)
from apps.api.tests.test_range_enrollment_integration import (
    seeded_policy_database as seeded_policy_database,
)
from apps.api.tests.test_range_enrollment_integration import (
    structure_database as structure_database,
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
def deferred_parent(enrollment_database, request):
    option = getattr(request, "param", None)
    missing_currency = option == "missing_currency"
    cited_name_errors = option == "cited_name_errors"
    review_action = None if missing_currency or cited_name_errors else option
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
        if not cited_name_errors:
            lines += [f"담보명: Sample Rider {i} | 정액 가입금액: {100 + i}원" for i in range(6)]
        connection.execute(
            "UPDATE extraction_blocks SET text=%s WHERE reading_order=0 AND "
            "page_id IN (SELECT id FROM extraction_pages WHERE "
            "extraction_id=%s)",
            ("\n".join(lines), original.extraction_id),
        )
        if cited_name_errors:
            connection.execute(
                "DELETE FROM extraction_blocks WHERE reading_order>0 AND page_id IN "
                "(SELECT id FROM extraction_pages WHERE extraction_id=%s)",
                (original.extraction_id,),
            )
            page = connection.execute(
                "SELECT id FROM extraction_pages WHERE extraction_id=%s", (original.extraction_id,)
            ).fetchone()[0]
            table = connection.execute(
                "INSERT INTO extraction_tables(page_id,bbox,metadata_json) "
                "VALUES (%s,'[10,100,430,240]',%s) RETURNING id",
                (page, Jsonb({"header_rows": [0]})),
            ).fetchone()[0]
            table_rows = [["담보명", "가입금액(원)", "보장구분"]] + [
                [f"Sample Rider {i}", str(100 + i), "정액"] for i in range(6)
            ]
            for row_index, cells in enumerate(table_rows):
                for column, value in enumerate(cells):
                    connection.execute(
                        "INSERT INTO extraction_cells(table_id,row_index,column_index,text,bbox) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (
                            table,
                            row_index,
                            column,
                            value,
                            Jsonb(
                                [
                                    10 + column * 140,
                                    100 + row_index * 20,
                                    150 + column * 140,
                                    120 + row_index * 20,
                                ]
                            ),
                        ),
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

    def candidate(kind, fields, evidence_id=None):
        return StructurerCandidate(
            schema_version="1",
            candidate_id=uuid4(),
            candidate_kind=kind,
            fields=tuple(
                CandidateField(field_id=name, value=value, evidence_ids=(evidence_id or primary,))
                for name, value in fields
            ),
        )

    policy = candidate(
        "policy_contract", [("insurer", "Sample Insurer"), ("product_name", "Sample Plan")]
    )
    raw_names = ["SampleRider 0", "Sample Rider1", "Wrong Rider 2"] if cited_name_errors else []
    riders = tuple(
        candidate(
            "rider",
            [
                ("rider_name", raw_names[i] if i < len(raw_names) else f"Sample Rider {i}"),
                ("rider_key", raw_names[i] if i < len(raw_names) else f"Sample Rider {i}"),
                *([("benefit_type", "fixed")] if i != 5 else []),
                ("sum_assured", 100 + i),
                *([] if missing_currency else [("currency", "KRW")]),
            ],
            evidence_id=next(
                e.evidence_id
                for e in work.envelope.evidence
                if e.primary and e.source_role == "policy" and f"Sample Rider {i}" in e.text
            )
            if cited_name_errors
            else None,
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
    if cited_name_errors:
        dispositions = []
        for key, evidence_id in zip(
            work.envelope.primary_chunk_ids, work.envelope.primary_evidence_ids, strict=True
        ):
            ids = tuple(
                c.candidate_id
                for c in batch.candidates
                if any(evidence_id in f.evidence_ids for f in c.fields)
            )
            dispositions.append(
                RangeDisposition(
                    chunk_id=key,
                    outcome="CANDIDATES" if ids else "NO_ENROLLMENT_FACTS",
                    candidate_ids=ids,
                )
            )
        batch = batch.model_copy(update={"ranges": tuple(dispositions)})
    request = _raw_request(url, old, batch)
    draft = PolicyDraftNormalizationRepository(url).normalize(
        old, WORKER, work, batch, "synthetic-normalization-request"
    )
    assert len(draft.batch.candidates) == (4 if cited_name_errors else 7)
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
    reviewed = None
    if review_action is not None:
        scope = HouseholdScope(old.household_space_id)
        repository = CandidateRepository(url)
        item = next(
            item
            for item in repository.list_review_items(
                scope, status="NEEDS_REVIEW" if review_action == "parent_reject" else "AI_VERIFIED"
            )
            if (review_action == "parent_reject" and item.candidate_kind == "policy_contract")
            or any(
                field.field_id == "rider_name" and field.value == "Sample Rider 0"
                for field in item.fields
            )
        )
        if review_action in {"reject", "parent_reject"}:
            current_item = repository.transition(
                scope,
                item.review_item_id,
                expected_version=item.expected_version,
                status="rejected",
                actor_id=uuid4(),
                rejection_reason="INVALID_EVIDENCE",
            )
        else:
            assert review_action == "correct"
            current_item = repository.correct_field(
                scope,
                review_item_id=item.review_item_id,
                actor_id=uuid4(),
                request=CandidateCorrectionRequest(
                    expected_version=item.expected_version,
                    field_id="sum_assured",
                    value=737,
                    evidence_id=item.evidence[0].evidence_id,
                ),
            )
        reviewed = {
            "review_item_id": item.review_item_id,
            "current_candidate_id": current_item.candidate_version_id,
            "expected_status": "rejected"
            if review_action in {"reject", "parent_reject"}
            else "NEEDS_REVIEW",
            "history": _review_history(url, item.review_item_id),
        }
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
    assert preview is not None
    if review_action == "parent_reject":
        assert preview.batch.candidates == ()
    else:
        assert [c.candidate_kind for c in preview.batch.candidates] == ["policy_contract"]
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
    return url, original, old, current, history, old_fields, old_candidates, calls, reviewed


def test_v8_parent_publishes_existing_six_riders_without_copy_or_reverification(
    deferred_parent, monkeypatch
):
    url, original, old, current, history, old_fields, old_candidates, calls, _ = deferred_parent
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
    from apps.api.tests import test_insurance_reconciliation_migration_integration as knowledge

    # Reuse the existing synthetic run/subject/contract seed for this fixture's household.
    run_id = uuid4()
    with monkeypatch.context() as seed_scope, psycopg.connect(_psycopg_url(url)) as connection:
        actor = connection.execute(
            "SELECT id FROM app_users WHERE household_space_id=%s LIMIT 1",
            (old.household_space_id,),
        ).fetchone()[0]
        for name, value in {
            "HOUSEHOLD_ID": old.household_space_id,
            "MEMBER_A_ID": original.family_member_id,
            "USER_ID": actor,
            "RUN_ID": run_id,
            "SUBJECT_ID": uuid4(),
            "CONTRACT_ID": uuid4(),
        }.items():
            seed_scope.setattr(knowledge, name, value)
        knowledge._seed_knowledge(connection)
    try:
        reconciliation = InsuranceReconciliationRepository(url).get_member(
            scope, original.family_member_id
        )
        assert reconciliation is not None and reconciliation.knowledge_run_id == run_id
        serialized_reconciliation = MemberInsuranceReconciliationResponse.from_domain(
            reconciliation
        )
        assert len(serialized_reconciliation.contracts) == 1
        assert len(serialized_reconciliation.orphan_operational_contracts) == 1
        orphan = serialized_reconciliation.orphan_operational_contracts[0]
        assert orphan.policy_contract_id == policy["id"]
        assert orphan.insurer_display is None
        assert orphan.insurer_unresolved_reason == "INSURER_SOURCE_UNVERIFIED"
        assert serialized_reconciliation.contracts[0].operational_link.policy_contract_id is None
    finally:
        # Shared fixtures use this dedicated synthetic database; immutable imports use TRUNCATE.
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE private_knowledge_import_runs CASCADE")
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


def _review_history(url, review_item_id):
    with psycopg.connect(_psycopg_url(url)) as connection:
        versions = connection.execute(
            "SELECT to_jsonb(c) FROM analysis_candidate_versions c WHERE review_item_id=%s "
            "ORDER BY c.version,c.id",
            (review_item_id,),
        ).fetchall()
        payload = {
            table: connection.execute(
                f"SELECT to_jsonb(p) FROM {table} p JOIN analysis_candidate_versions c "
                "ON c.id=p.candidate_version_id WHERE c.review_item_id=%s "
                "ORDER BY to_jsonb(p)::text",
                (review_item_id,),
            ).fetchall()
            for table in ("analysis_candidate_fields", "analysis_candidate_evidence")
        }
    return {"versions": versions, **payload}


@pytest.mark.parametrize("deferred_parent", ["reject", "correct"], indirect=True)
def test_v8_parent_recovery_preserves_prior_user_review_without_republishing_old_rider(
    deferred_parent,
):
    url, _, old, current, history, _, _, calls, reviewed = deferred_parent
    assert reviewed is not None and len(calls) == 1
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending() == 6  # One parent and five untouched prior Riders.
    assert projector.project_pending() == 0
    assert _review_history(url, reviewed["review_item_id"]) == reviewed["history"]
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT status,published_at FROM analysis_candidate_versions "
            "WHERE id=%s AND is_current",
            (reviewed["current_candidate_id"],),
        ).fetchone() == (reviewed["expected_status"], None)
        assert connection.execute(
            "SELECT count(*) FROM riders WHERE household_space_id=%s",
            (old.household_space_id,),
        ).fetchone() == (5,)
        assert connection.execute(
            "SELECT count(*) FROM riders WHERE household_space_id=%s AND display_name=%s",
            (old.household_space_id, "Sample Rider 0"),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT candidate_kind FROM analysis_candidate_versions WHERE structuring_job_id=%s",
            (current.id,),
        ).fetchall() == [("policy_contract",)]
        adjustments = connection.execute(
            "SELECT adjustments_json FROM policy_range_replay_sources WHERE job_id=%s",
            (current.id,),
        ).fetchone()[0]
        assert (
            sum(item["reason"] == "PRIOR_CANDIDATE_REVIEW_PRESERVED" for item in adjustments) == 1
        )
        if reviewed["expected_status"] == "NEEDS_REVIEW":
            assert connection.execute(
                "SELECT value FROM analysis_candidate_fields WHERE candidate_version_id=%s "
                "AND field_id='sum_assured'",
                (reviewed["current_candidate_id"],),
            ).fetchone() == (737,)
        for table, before in history.items():
            assert (
                connection.execute(
                    f"SELECT to_jsonb(t) FROM {table} t WHERE job_id=%s ORDER BY to_jsonb(t)::text",
                    (old.id,),
                ).fetchall()
                == before
            )


@pytest.mark.parametrize("deferred_parent", ["parent_reject"], indirect=True)
def test_v8_recovery_does_not_reapprove_previously_rejected_parent(deferred_parent):
    url, _, _, current, _, _, _, calls, reviewed = deferred_parent
    assert calls == []
    assert RangeEnrollmentProjector(url).project_pending() == 0
    assert _review_history(url, reviewed["review_item_id"]) == reviewed["history"]
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM analysis_candidate_versions WHERE structuring_job_id=%s",
                (current.id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM policy_contracts WHERE household_space_id=%s",
                (current.household_space_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM riders WHERE household_space_id=%s",
                (current.household_space_id,),
            ).fetchone()[0]
            == 0
        )

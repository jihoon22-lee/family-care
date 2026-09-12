"""v2 raw → v4 removed money → v12 proof enriches only unchanged empty ledger fields."""

from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.amount_source import read_operational_amount_source
from familycare_api.policies.candidate_models import CandidateCorrectionRequest
from familycare_api.policies.candidate_repository import CandidateRepository
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker import retained_policy
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.ai.schemas import CandidateField, StructurerCandidate
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_source_scoped_policy_identity_integration import (
    WORKER,
    _drain,
    _psycopg_url,
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
from workers.analyzer.tests.test_initial_policy_draft_normalization import _raw_request

pytestmark = pytest.mark.integration


def _historical_job(url, original, generation, revision, monkeypatch):
    with monkeypatch.context() as previous:
        previous.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", revision)
        job = retained_policy.RetainedPolicyRepository(url).enqueue(
            household_space_id=original.household_space_id,
            source_job_id=original.id,
            expected_generation_id=generation,
            pipeline_revision=revision,
        )
        queue = retained_policy.RetainedPolicyJobQueue(
            url,
            household_space_id=job.household_space_id,
            job_id=job.id,
            pipeline_revision=revision,
        )
    return queue, queue.claim_next_job(WORKER)


class Verifier:
    def __init__(self, count, request_id):
        self.count, self.request_id = count, request_id
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        assert kwargs["schema_name"] == "policy_candidate_batch_verifier_v2"
        candidates = kwargs["input_payload"]["candidates"]
        assert len(candidates) == self.count
        return ProviderResponse(
            request_id=self.request_id,
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


def _verify(url, queue, job, request_id, count, provider_request_id):
    replay = PolicyDraftReplayRepository(url, source_provider_request_id=request_id)
    ranges = PolicyRangeRepository(url)
    loader = PolicyEvidenceLoader(url)
    terms = loader.load_member_terms(
        household_space_id=job.household_space_id, family_member_id=job.family_member_id
    )
    work = ranges.next(job, WORKER, sensitive_terms=terms)
    assert work is not None
    draft = replay.prepare(job, WORKER, work)
    assert draft is not None and len(draft.batch.candidates) == count
    provider = Verifier(count, provider_request_id)
    PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=loader,
        provider=provider,
        publisher=PolicyCandidatePublisher(url),
        range_repository=ranges,
        request_budget=PolicyRequestBudget(url),
        replay_repository=replay,
    )._run_range(job, WORKER)
    assert len(provider.calls) == 1
    _drain(url, queue, queue.claim_next_job(WORKER), terms)
    return draft


@pytest.fixture()
def missing_money(enrollment_database, monkeypatch):
    url, original = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as c:
        c.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (original.family_member_id,),
        )
        c.execute(
            "DELETE FROM extraction_blocks WHERE page_id IN (SELECT id "
            "FROM extraction_pages WHERE extraction_id=%s)",
            (original.extraction_id,),
        )
        page = c.execute(
            "SELECT id FROM extraction_pages WHERE extraction_id=%s", (original.extraction_id,)
        ).fetchone()[0]
        c.execute(
            "INSERT INTO "
            "extraction_blocks(page_id,text,bbox,reading_order) VALUES "
            "(%s,%s,'[10,30,430,80]',0)",
            (
                page,
                "보험증권 가입금액\n증권번호: synthetic-missing-money-001\n피보험자: Family "
                "Member A\n보험사: Sample Insurer\n상품명: Sample Plan",
            ),
        )
        table = c.execute(
            "INSERT INTO extraction_tables(page_id,bbox,metadata_json) "
            "VALUES (%s,'[10,100,430,260]',%s) RETURNING id",
            (page, Jsonb({"header_rows": [0]})),
        ).fetchone()[0]
        rows = [["담보명", "가입금액(만원)", "보장구분"]] + [
            [f"Sample Rider {i}", str(20 + i), "정액"] for i in range(7)
        ]
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                c.execute(
                    "INSERT INTO "
                    "extraction_cells(table_id,row_index,column_index,text,bbox) "
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
    queue, raw_job = _historical_job(
        url, original, generation, "retained-policy-association-v2", monkeypatch
    )
    assert raw_job is not None
    work = ranges.next(raw_job, WORKER, sensitive_terms=terms)
    assert work is not None
    primary = work.envelope.primary_evidence_ids[0]
    policy = StructurerCandidate(
        candidate_id=uuid4(),
        candidate_kind="policy_contract",
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(primary,))
            for key, value in (("insurer", "Sample Insurer"), ("product_name", "Sample Plan"))
        ),
    )
    riders = []
    for index in range(7):
        evidence = next(
            e for e in work.envelope.evidence if e.primary and f"Sample Rider {index}" in e.text
        )
        riders.append(
            StructurerCandidate(
                candidate_id=uuid4(),
                candidate_kind="rider",
                fields=tuple(
                    CandidateField(field_id=key, value=value, evidence_ids=(evidence.evidence_id,))
                    for key, value in (
                        ("rider_name", f"Sample Rider {index}"),
                        ("rider_key", f"Sample Rider {index}"),
                        ("benefit_type", "fixed"),
                        ("sum_assured", 20 + index),
                    )
                ),
            )
        )
    candidates = (policy, *riders)
    dispositions = []
    for chunk, evidence_id in zip(
        work.envelope.primary_chunk_ids, work.envelope.primary_evidence_ids, strict=True
    ):
        ids = tuple(
            c.candidate_id
            for c in candidates
            if any(evidence_id in f.evidence_ids for f in c.fields)
        )
        dispositions.append(
            RangeDisposition(
                chunk_id=chunk,
                outcome="CANDIDATES" if ids else "NO_ENROLLMENT_FACTS",
                candidate_ids=ids,
            )
        )
    raw = PolicyRangeBatch(schema_version="3", candidates=candidates, ranges=tuple(dispositions))
    request_id = _raw_request(url, raw_job, raw)
    _drain(url, queue, raw_job, terms)
    queue, previous = _historical_job(
        url, original, generation, "retained-policy-association-v4", monkeypatch
    )
    assert previous is not None
    draft = _verify(url, queue, previous, request_id, 8, "synthetic-old-v4-verifier")
    assert all(
        not {f.field_id for f in c.fields} & {"sum_assured", "currency"}
        for c in draft.batch.candidates
        if c.candidate_kind == "rider"
    )
    assert RangeEnrollmentProjector(url).project_pending() == 8
    return url, original, raw_job, previous, request_id, generation


@pytest.mark.parametrize("late_change", [None, "ledger_version", "correct", "reject"])
def test_v12_fills_seven_empty_money_pairs_and_respects_late_user_changes(
    missing_money, late_change
):
    url, original, raw_job, previous, request_id, generation = missing_money
    old_jobs = [raw_job.id, previous.id]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as c:
        before = c.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY display_name",
            (original.household_space_id,),
        ).fetchall()
        assert len(before) == 7 and all(
            r["insured_amount"] is None and r["currency"] is None for r in before
        )
        policy = c.execute(
            "SELECT * FROM policy_contracts WHERE id=%s", (before[0]["policy_contract_id"],)
        ).fetchone()
        history = {
            table: c.execute(
                f"SELECT to_jsonb(t) AS value FROM {table} t WHERE "
                f"job_id=ANY(%s) ORDER BY to_jsonb(t)::text",
                (old_jobs,),
            ).fetchall()
            for table in (
                "policy_provider_requests",
                "policy_range_replay_sources",
                "document_policy_ranges",
            )
        }
        publications = c.execute(
            "SELECT to_jsonb(p) AS value FROM "
            "range_enrollment_publications p WHERE household_space_id=%s "
            "ORDER BY candidate_version_id",
            (original.household_space_id,),
        ).fetchall()
        old_candidate = c.execute(
            "SELECT c.id FROM analysis_candidate_versions c JOIN "
            "analysis_candidate_fields f ON f.candidate_version_id=c.id "
            "WHERE c.structuring_job_id=%s AND f.field_id='rider_name' AND "
            "f.value=%s",
            (previous.id, Jsonb("Sample Rider 0")),
        ).fetchone()["id"]
    selected = retained_policy.RetainedPolicyRepository(url).enqueue(
        household_space_id=original.household_space_id,
        source_job_id=original.id,
        expected_generation_id=generation,
        pipeline_revision="retained-policy-association-v12",
    )
    queue = retained_policy.RetainedPolicyJobQueue(
        url,
        household_space_id=selected.household_space_id,
        job_id=selected.id,
        pipeline_revision=selected.pipeline_version,
    )
    job = queue.claim_next_job(WORKER)
    assert job is not None
    draft = _verify(url, queue, job, request_id, 7, "synthetic-v12-money-verifier")
    assert all(c.candidate_kind == "rider" for c in draft.batch.candidates)
    scope = HouseholdScope(original.household_space_id)
    review_history = None
    if late_change == "ledger_version":
        with psycopg.connect(_psycopg_url(url)) as c:
            c.execute("UPDATE riders SET version=version+1 WHERE id=%s", (before[0]["id"],))
    elif late_change in {"correct", "reject"}:
        repository = CandidateRepository(url)
        item = next(
            item
            for item in repository.list_review_items(scope, status="AI_VERIFIED")
            if item.candidate_version_id == old_candidate
        )
        if late_change == "correct":
            repository.correct_field(
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
        else:
            repository.transition(
                scope,
                item.review_item_id,
                expected_version=item.expected_version,
                status="rejected",
                actor_id=uuid4(),
                rejection_reason="INVALID_EVIDENCE",
            )
        with psycopg.connect(_psycopg_url(url)) as c:
            review_history = c.execute(
                "SELECT to_jsonb(v) FROM analysis_candidate_versions v WHERE "
                "review_item_id=%s ORDER BY id",
                (item.review_item_id,),
            ).fetchall()
    assert RangeEnrollmentProjector(url).project_pending() == (7 if late_change is None else 6)
    assert RangeEnrollmentProjector(url).project_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as c:
        after = c.execute(
            "SELECT * FROM riders WHERE household_space_id=%s ORDER BY display_name",
            (original.household_space_id,),
        ).fetchall()
        assert len(after) == 7
        for index, (old, current) in enumerate(zip(before, after, strict=True)):
            assert current["id"] == old["id"]
            if index == 0 and late_change:
                assert current["insured_amount"] is None and current["currency"] is None
            else:
                assert (
                    current["insured_amount"] == (20 + index) * 10000
                    and current["currency"] == "KRW"
                )
                assert current["version"] == old["version"] + 1
                witness = read_operational_amount_source(c, scope, current["id"])
                assert witness.amount_decision == witness.currency_decision == "MATCH"
                assert witness.amount == current["insured_amount"] and witness.currency == "KRW"
            assert {
                k: v
                for k, v in current.items()
                if k not in {"insured_amount", "currency", "version", "updated_at"}
            } == {
                k: v
                for k, v in old.items()
                if k not in {"insured_amount", "currency", "version", "updated_at"}
            }
        assert (
            c.execute("SELECT * FROM policy_contracts WHERE id=%s", (policy["id"],)).fetchone()
            == policy
        )
        for table, rows in history.items():
            assert (
                c.execute(
                    f"SELECT to_jsonb(t) AS value FROM {table} t WHERE "
                    f"job_id=ANY(%s) ORDER BY to_jsonb(t)::text",
                    (old_jobs,),
                ).fetchall()
                == rows
            )
        assert (
            c.execute(
                "SELECT to_jsonb(p) AS value FROM "
                "range_enrollment_publications p WHERE "
                "candidate_version_id=ANY(%s::uuid[]) ORDER BY "
                "candidate_version_id",
                ([p["value"]["candidate_version_id"] for p in publications],),
            ).fetchall()
            == publications
        )
    if review_history is not None:
        with psycopg.connect(_psycopg_url(url)) as c:
            assert (
                c.execute(
                    "SELECT to_jsonb(v) FROM analysis_candidate_versions v WHERE "
                    "review_item_id=%s ORDER BY id",
                    (item.review_item_id,),
                ).fetchall()
                == review_history
            )

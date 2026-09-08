"""One wholly synthetic native policy batch for terms-change integration tests.

Call only with an enrollment_database fixture's claimed job and test URL. The
helper stores invented word geometry and retained synthetic pipeline output; it
never invokes a provider. Callers own enrollment and metadata projection.
"""

from collections import defaultdict
from typing import Any
from uuid import UUID, uuid4

from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    PolicyCandidate,
    PolicyCandidateFieldId,
    StructurerCandidate,
)
from familycare_worker.policy_range_repository import PolicyRangeRepository, PolicyRangeWork

from apps.api.tests.test_native_range_enrollment_integration import _store_words
from workers.analyzer.tests.test_document_text_lines import _words
from workers.analyzer.tests.test_policy_range_repository import WORKER, _no_facts

_POLICY_LINES = (
    "보험증권",
    "보험사: Sample Insurer",
    "상품명: Sample Plan",
    "상품코드: SAMPLE-P",
    "적용약관코드: TERMS-A",
    "적용판본코드: EDITION-A",
    "증권번호: synthetic-policy-001",
    "피보험자: Family Member A",
    "가입금액",
    "Sample Rider sum assured: 317 KRW",
    "Another Rider sum assured: 619 KRW",
)


def retain_terms_change_policy(url: str, job: Any) -> PolicyRangeWork:
    """Retain a policy and two separate rider rows in one native range batch."""
    _store_words(url, job, _words(list(_POLICY_LINES)))
    repository = PolicyRangeRepository(url)
    work = repository.next(job, WORKER, sensitive_terms=("Family Member A",))
    assert work is not None
    evidence_by_text = {item.text: item for item in work.envelope.evidence if item.primary}
    policy_fields: tuple[tuple[PolicyCandidateFieldId, str, str], ...] = (
        ("insurer", "Sample Insurer", "보험사: Sample Insurer"),
        ("product_name", "Sample Plan", "상품명: Sample Plan"),
    )
    candidates = [
        StructurerCandidate(
            schema_version="1",
            candidate_id=uuid4(),
            candidate_kind="policy_contract",
            fields=tuple(
                CandidateField(
                    field_id=field,
                    value=value,
                    evidence_ids=(evidence_by_text[line].evidence_id,),
                )
                for field, value, line in policy_fields
            ),
        )
    ]
    for name, amount in (("Sample Rider", 317), ("Another Rider", 619)):
        evidence = evidence_by_text[f"{name} sum assured: {amount} KRW"]
        rider_fields: tuple[tuple[PolicyCandidateFieldId, str | int], ...] = (
            ("rider_name", name),
            ("rider_key", name.casefold().replace(" ", "-")),
            ("benefit_type", "unknown"),
            ("sum_assured", amount),
            ("currency", "KRW"),
        )
        candidates.append(
            StructurerCandidate(
                schema_version="1",
                candidate_id=uuid4(),
                candidate_kind="rider",
                fields=tuple(
                    CandidateField(
                        field_id=field, value=value, evidence_ids=(evidence.evidence_id,)
                    )
                    for field, value in rider_fields
                ),
            )
        )
    assigned: dict[UUID, set[UUID]] = defaultdict(set)
    for candidate in candidates:
        for field in candidate.fields:
            for evidence_id in field.evidence_ids:
                assigned[evidence_id].add(candidate.candidate_id)
    primary = dict(
        zip(work.envelope.primary_chunk_ids, work.envelope.primary_evidence_ids, strict=True)
    )
    batch = PolicyRangeBatch(
        schema_version="3",
        candidates=tuple(candidates),
        ranges=tuple(
            disposition.model_copy(
                update={
                    "outcome": "CANDIDATES",
                    "candidate_ids": tuple(
                        sorted(assigned[primary[disposition.chunk_id]], key=str)
                    ),
                }
            )
            if primary[disposition.chunk_id] in assigned
            else disposition
            for disposition in _no_facts(work).ranges
        ),
    )
    result = CandidatePipelineResult(
        classification="SUCCESS",
        candidates=tuple(
            PolicyCandidate(
                candidate_id=candidate.candidate_id,
                candidate_kind=candidate.candidate_kind,
                fields=candidate.fields,
                status="AI_VERIFIED",
                issue_codes=(),
                provider_request_ids=("synthetic-structure", "synthetic-verify"),
            )
            for candidate in candidates
        ),
    )
    repository.save(job, WORKER, work, batch, result)
    return work

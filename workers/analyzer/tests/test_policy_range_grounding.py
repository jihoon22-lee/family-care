"""Synthetic field grounding prevents verifier agreement from inventing facts."""

from uuid import uuid4

import pytest
from familycare_worker.ai.policy_ranges import RangeEvidenceSlice
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate


def _evidence(text: str, *, primary: bool = True) -> RangeEvidenceSlice:
    return RangeEvidenceSlice(
        evidence_id=uuid4(),
        document_version_id=uuid4(),
        page=1,
        text=text,
        bbox=None,
        node_id="a" * 64,
        start=0,
        end=len(text),
        primary=primary,
        document_kind="policy",
        source_role="policy",
    )


def _candidate(evidence: RangeEvidenceSlice, **values: object) -> PolicyCandidate:
    return PolicyCandidate(
        candidate_id=uuid4(),
        candidate_kind="rider",
        status="AI_VERIFIED",
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(evidence.evidence_id,))
            for key, value in values.items()
        ),
        issue_codes=(),
        provider_request_ids=("synthetic-structure", "synthetic-verify"),
    )


def test_normalized_row_and_explicit_amount_unit_are_grounded() -> None:
    source = _evidence("Sample Rider | 정액 | 가입금액 20만원 | 보장개시일 2025.01.02")
    candidate = _candidate(
        source,
        rider_name="Sample Rider",
        rider_key="sample-rider",
        benefit_type="fixed",
        sum_assured=200000,
        currency="KRW",
        coverage_start="2025-01-02",
    )
    assert ground_range_candidate(candidate, (source,)) == candidate


@pytest.mark.parametrize(
    "values",
    [
        {"rider_name": "Invented Rider"},
        {"sum_assured": 200000},
        {"currency": "USD"},
        {"coverage_start": "2025-01-03"},
        {"renewable": True},
        {"rider_status": "active"},
        {"benefit_type": "indemnity"},
    ],
)
def test_approved_but_ungrounded_field_requires_review(values: dict[str, object]) -> None:
    source = _evidence("Sample Rider | 정액 | 가입금액 20원 | 보장개시일 2025.01.02 | 비갱신")
    result = ground_range_candidate(_candidate(source, **values), (source,))
    assert result.status == "NEEDS_REVIEW"
    assert "INVENTED_FIELD" in result.issue_codes


def test_amount_must_be_from_sum_assured_not_premium_or_benefit_limit() -> None:
    source = _evidence("Sample Rider 가입금액 20원 보험료 200원 지급한도 300원")
    assert (
        ground_range_candidate(_candidate(source, sum_assured=20), (source,)).status
        == "AI_VERIFIED"
    )
    for value in (200, 300):
        assert (
            ground_range_candidate(_candidate(source, sum_assured=value), (source,)).status
            == "NEEDS_REVIEW"
        )


def test_dates_must_match_their_field_label() -> None:
    source = _evidence("보장개시일 2025-01-02 보장종료일 2030-01-02")
    assert (
        ground_range_candidate(_candidate(source, coverage_start="2030-01-02"), (source,)).status
        == "NEEDS_REVIEW"
    )


def test_context_name_alone_does_not_establish_primary_enrollment() -> None:
    source = _evidence("Sample Rider 가입금액 20원", primary=False)
    result = ground_range_candidate(_candidate(source, rider_name="Sample Rider"), (source,))
    assert result.status == "NEEDS_REVIEW"


def test_rejected_candidate_and_unknown_status_are_preserved() -> None:
    source = _evidence("Sample Rider")
    candidate = _candidate(source, rider_status="unknown")
    assert ground_range_candidate(candidate, (source,)) == candidate
    rejected = candidate.model_copy(update={"status": "rejected"})
    assert ground_range_candidate(rejected, ()) == rejected


@pytest.mark.parametrize("separator", ["\n", " | "])
def test_amount_cannot_be_borrowed_from_another_rider_row(separator: str) -> None:
    source = _evidence(
        "Sample Rider fixed 가입금액 317원" + separator + "Another Rider fixed 가입금액 619원"
    )
    wrong = _candidate(source, rider_name="Sample Rider", sum_assured=619)
    assert ground_range_candidate(wrong, (source,)).status == "NEEDS_REVIEW"


@pytest.mark.parametrize("status", ["AI_VERIFIED", "NEEDS_REVIEW"])
def test_explicitly_unenrolled_row_is_not_published_as_enrollment(status: str) -> None:
    source = _evidence("Sample Rider | 미가입 | 정액 | 가입금액 20원")
    candidate = _candidate(source, rider_name="Sample Rider", benefit_type="fixed", sum_assured=20)
    candidate = candidate.model_copy(update={"status": status})
    result = ground_range_candidate(candidate, (source,))
    assert result.status == "NEEDS_REVIEW"
    assert "NOT_ENROLLED" in result.issue_codes


def test_unenrolled_neighbor_does_not_suppress_a_separate_enrolled_line() -> None:
    source = _evidence(
        "Sample Rider 정액 가입금액 20원\nAnother Rider | 미가입 | 정액 가입금액 30원"
    )
    candidate = _candidate(source, rider_name="Sample Rider", benefit_type="fixed", sum_assured=20)
    assert ground_range_candidate(candidate, (source,)).status == "AI_VERIFIED"


def test_unenrolled_marker_on_the_next_line_remains_bound_to_its_rider() -> None:
    source = _evidence("Sample Rider 정액 가입금액 20원\n미가입")
    candidate = _candidate(source, rider_name="Sample Rider", benefit_type="fixed", sum_assured=20)
    assert ground_range_candidate(candidate, (source,)).status == "NEEDS_REVIEW"


def test_unclassified_enrollment_retains_amount_without_inventing_a_type() -> None:
    source = _evidence("Sample Rider 가입금액 20원")
    candidate = _candidate(
        source, rider_name="Sample Rider", rider_key="sample-rider", sum_assured=20
    )
    result = ground_range_candidate(candidate, (source,))
    assert result.status == "AI_VERIFIED"
    assert (
        next(field.value for field in result.fields if field.field_id == "benefit_type")
        == "unknown"
    )


def test_unguessed_classification_does_not_discard_proven_enrollment() -> None:
    source = _evidence("Sample Rider 가입금액 20원")
    candidate = _candidate(
        source,
        rider_name="Sample Rider",
        rider_key="sample-rider",
        sum_assured=20,
        benefit_type="fixed",
    )
    result = ground_range_candidate(candidate, (source,))
    assert result.status == "AI_VERIFIED"
    assert (
        next(field.value for field in result.fields if field.field_id == "benefit_type")
        == "unknown"
    )


def test_unknown_type_cannot_turn_an_incidental_heading_into_a_rider() -> None:
    source = _evidence("보험증권 가입금액\nSample Insurer Sample Plan")
    candidate = _candidate(
        source, rider_name="보험증권", rider_key="보험증권", benefit_type="unknown"
    )
    assert ground_range_candidate(candidate, (source,)).status == "NEEDS_REVIEW"


def test_range_validation_allows_missing_classification_without_relaxing_legacy_batches() -> None:
    from familycare_worker.ai.schemas import StructurerCandidate, VerifierDecision
    from familycare_worker.ai.validator import validate_candidate

    source = _evidence("Sample Rider 가입금액 20원")
    candidate = _candidate(
        source, rider_name="Sample Rider", rider_key="sample-rider", sum_assured=20
    )
    structured = StructurerCandidate(
        schema_version="1",
        candidate_id=candidate.candidate_id,
        candidate_kind="rider",
        fields=candidate.fields,
    )
    verifier = VerifierDecision(
        schema_version="1",
        candidate_id=candidate.candidate_id,
        decision="approved",
        issue_codes=(),
        evidence_ids=(source.evidence_id,),
    )
    assert "UNSUPPORTED_STRUCTURE" in validate_candidate(
        candidate=structured, verifier=verifier, evidence=(source,)
    )
    assert (
        validate_candidate(
            candidate=structured,
            verifier=verifier,
            evidence=(source,),
            allow_unclassified_enrollment=True,
        )
        == ()
    )


@pytest.mark.parametrize(
    "text,values",
    [
        (
            "Sample Rider 보장개시일 2025-01-02 Another Rider 보장개시일 2030-01-02",
            {"coverage_start": "2030-01-02"},
        ),
        ("Sample Rider fixed Another Rider indemnity", {"benefit_type": "indemnity"}),
        ("Sample Rider 비갱신 Another Rider 갱신형", {"renewable": True}),
    ],
)
def test_ambiguous_single_line_conditions_remain_unresolved(
    text: str, values: dict[str, object]
) -> None:
    source = _evidence(text)
    candidate = _candidate(source, rider_name="Sample Rider", **values)
    assert ground_range_candidate(candidate, (source,)).status == "NEEDS_REVIEW"

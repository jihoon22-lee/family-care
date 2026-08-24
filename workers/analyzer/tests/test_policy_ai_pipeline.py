"""Decision-table tests for the provider-neutral policy candidate pipeline."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from familycare_worker.ai.policy_pipeline import run_policy_pipeline
from familycare_worker.runner import PolicyCandidatePipelineRunner

from workers.analyzer.tests.fixtures.policy_ai_responses import (
    INVENTED_EVIDENCE,
    INVENTED_FIELD,
    VALID_STRUCTURED,
    VALID_VERIFIED,
    VERIFIER_NEEDS_REVIEW,
    VERIFIER_REJECTED,
    FakeProvider,
    synthetic_policy_evidence,
)

STRUCTURER_MODEL = "gpt-5.6-luna"
VERIFIER_MODEL = "gpt-5.6-terra"


def _run(provider: FakeProvider) -> Any:
    return run_policy_pipeline(
        evidence=synthetic_policy_evidence(),
        provider=provider,
        structurer_model=STRUCTURER_MODEL,
        verifier_model=VERIFIER_MODEL,
    )


def _candidate(result: Any) -> Any:
    candidates = result.candidates
    assert len(candidates) == 1
    return candidates[0]


def _issue_codes(candidate: Any) -> set[str]:
    return set(candidate.issue_codes)


def test_two_valid_ai_stages_and_validator_publish_ai_verified() -> None:
    """Only ordered structurer, verifier, and validator success is publishable."""

    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "AI_VERIFIED"
    assert _issue_codes(candidate) == set()
    assert [(call.stage, call.model) for call in provider.calls] == [
        ("structurer", STRUCTURER_MODEL),
        ("verifier", VERIFIER_MODEL),
    ]


def test_unimplemented_policy_party_shape_never_becomes_ai_verified() -> None:
    structured = deepcopy(VALID_STRUCTURED)
    structured["candidate_kind"] = "policy_party"
    provider = FakeProvider(structurer=structured, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)


def test_verifier_cannot_invent_a_rider_or_evidence() -> None:
    """An unreferenced verifier Evidence ID blocks publication."""

    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=INVENTED_EVIDENCE)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "INVENTED_EVIDENCE" in _issue_codes(candidate)


def test_verifier_cannot_add_a_field_not_present_in_the_structurer_candidate() -> None:
    """Verifier output is a decision over the candidate, never a second structurer."""

    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=INVENTED_FIELD)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "INVENTED_FIELD" in _issue_codes(candidate)


def test_verifier_disagreement_preserves_candidate_as_needs_review() -> None:
    """A verifier request for review does not erase the bounded candidate."""

    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=VERIFIER_NEEDS_REVIEW)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "LOW_CONFIDENCE" in _issue_codes(candidate)


def test_verifier_rejection_is_not_a_publishable_candidate() -> None:
    """A rejected verifier result remains explicitly rejected."""

    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=VERIFIER_REJECTED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "rejected"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)


def test_provider_timeout_returns_sanitized_retryable_classification() -> None:
    """A transient provider timeout is classified without exposing its exception text."""

    provider = FakeProvider(structurer=TimeoutError("synthetic-timeout-marker"))

    result = _run(provider)

    assert result.classification == "RETRYABLE_PROVIDER_ERROR"
    assert "synthetic-timeout-marker" not in repr(result)
    assert [call.stage for call in provider.calls] == ["structurer"]


def test_verifier_must_account_for_every_candidate_evidence_reference() -> None:
    """An approval that silently omits candidate Evidence is not complete verification."""

    verifier = deepcopy(VALID_VERIFIED)
    verifier["evidence_ids"] = []
    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=verifier)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "MISSING_EVIDENCE" in _issue_codes(candidate)


class _RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...]]] = []

    def publish(self, result: Any, evidence: tuple[Any, ...]) -> None:
        self.calls.append((result, evidence))


def test_worker_pipeline_runner_publishes_only_a_completed_candidate_batch() -> None:
    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=VALID_VERIFIED)
    publisher = _RecordingPublisher()
    runner = PolicyCandidatePipelineRunner(
        provider=provider,
        publisher=publisher,
        structurer_model=STRUCTURER_MODEL,
        verifier_model=VERIFIER_MODEL,
    )

    result = runner.run(evidence=synthetic_policy_evidence())

    assert result.classification == "SUCCESS"
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0] == result
    assert publisher.calls[0][1] == synthetic_policy_evidence()


def test_worker_pipeline_runner_keeps_provider_failures_out_of_persistence() -> None:
    provider = FakeProvider(structurer=TimeoutError("synthetic-timeout-marker"))
    publisher = _RecordingPublisher()
    runner = PolicyCandidatePipelineRunner(
        provider=provider,
        publisher=publisher,
        structurer_model=STRUCTURER_MODEL,
        verifier_model=VERIFIER_MODEL,
    )

    result = runner.run(evidence=synthetic_policy_evidence())

    assert result.classification == "RETRYABLE_PROVIDER_ERROR"
    assert publisher.calls == []


def test_worker_pipeline_runner_uses_the_approved_default_models() -> None:
    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=VALID_VERIFIED)
    publisher = _RecordingPublisher()
    runner = PolicyCandidatePipelineRunner(provider=provider, publisher=publisher)

    runner.run(evidence=synthetic_policy_evidence())

    assert [(call.stage, call.model) for call in provider.calls] == [
        ("structurer", "gpt-5.6-luna"),
        ("verifier", "gpt-5.6-terra"),
    ]

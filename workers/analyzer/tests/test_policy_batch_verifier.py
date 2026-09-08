"""One independent verifier request validates a bounded candidate batch."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest
from familycare_worker.ai.policy_pipeline import run_bounded_policy_batch_pipeline
from familycare_worker.ai.provider import ProviderResponse, ProviderTimeoutError

from workers.analyzer.tests.fixtures.policy_ai_responses import synthetic_policy_evidence
from workers.analyzer.tests.test_policy_candidate_batch import _candidate_batch


class SyntheticBatchVerifier:
    def __init__(self, *, mode: str = "approved") -> None:
        self.mode = mode
        self.calls: list[str] = []

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        del model, system_instruction
        self.calls.append(schema_name)
        if "batch_structurer" in schema_name:
            return ProviderResponse(payload=_candidate_batch(), request_id="synthetic-structure")
        if self.mode == "timeout":
            raise ProviderTimeoutError
        candidates = input_payload["candidates"]
        assert isinstance(candidates, list)
        decisions: list[dict[str, Any]] = [
            {
                "schema_version": "1",
                "candidate_id": item["candidate_id"],
                "decision": "approved",
                "issue_codes": [],
                "evidence_ids": sorted(
                    {value for field in item["fields"] for value in field["evidence_ids"]}
                ),
            }
            for item in candidates
        ]
        if self.mode == "one_conflict":
            decisions[-1]["decision"] = "needs_review"
            decisions[-1]["issue_codes"] = ["CONFLICTING_EVIDENCE"]
        elif self.mode == "missing":
            decisions.pop()
        elif self.mode == "duplicate":
            decisions[-1] = deepcopy(decisions[0])
        elif self.mode == "invented":
            decisions[-1]["candidate_id"] = "00000000-0000-4000-8000-000000000999"
        elif self.mode == "new_field":
            decisions[-1]["fields"] = []
        elif self.mode == "invented_evidence":
            decisions[-1]["evidence_ids"] = ["00000000-0000-4000-8000-000000000999"]
        return ProviderResponse(
            payload={"schema_version": "2", "decisions": decisions},
            request_id="synthetic-verification",
        )


def _run(provider: SyntheticBatchVerifier) -> Any:
    return run_bounded_policy_batch_pipeline(
        evidence=synthetic_policy_evidence(),
        provider=provider,
        structurer_model="synthetic-structurer",
        verifier_model="synthetic-verifier",
    )


def test_batch_requires_only_two_provider_requests_with_individual_validation() -> None:
    provider = SyntheticBatchVerifier()
    result = _run(provider)
    assert len(provider.calls) == 2
    assert result.classification == "SUCCESS"
    assert [item.status for item in result.candidates] == ["AI_VERIFIED", "AI_VERIFIED"]
    assert all(len(item.provider_request_ids) == 2 for item in result.candidates)


def test_one_conflict_does_not_discard_other_verified_candidates() -> None:
    result = _run(SyntheticBatchVerifier(mode="one_conflict"))
    assert result.classification == "NEEDS_REVIEW"
    assert [item.status for item in result.candidates] == ["AI_VERIFIED", "NEEDS_REVIEW"]


@pytest.mark.parametrize("mode", ["missing", "duplicate", "invented", "new_field"])
def test_invalid_batch_cannot_grant_authority_but_retains_structure(mode: str) -> None:
    result = _run(SyntheticBatchVerifier(mode=mode))
    assert len(result.candidates) == 2
    assert all(item.status == "NEEDS_REVIEW" for item in result.candidates)
    assert all(item.fields for item in result.candidates)


def test_invented_evidence_is_rejected_for_only_the_affected_candidate() -> None:
    result = _run(SyntheticBatchVerifier(mode="invented_evidence"))
    assert [item.status for item in result.candidates] == ["AI_VERIFIED", "NEEDS_REVIEW"]
    assert "INVENTED_EVIDENCE" in result.candidates[-1].issue_codes


def test_verifier_timeout_preserves_structured_candidates_for_stage_resume() -> None:
    provider = SyntheticBatchVerifier(mode="timeout")
    result = _run(provider)
    assert result.classification == "RETRYABLE_PROVIDER_ERROR"
    assert len(result.candidates) == 2
    assert all(item.status == "NEEDS_REVIEW" for item in result.candidates)
    assert all(len(item.provider_request_ids) == 1 for item in result.candidates)
    assert len(provider.calls) == 2


def test_retained_structurer_result_resumes_with_only_one_verifier_call() -> None:
    import json

    from familycare_worker.ai.policy_pipeline import verify_structured_policy_batch
    from familycare_worker.ai.schemas import StructurerCandidateBatch

    stored = StructurerCandidateBatch.model_validate_json(json.dumps(_candidate_batch()))
    provider = SyntheticBatchVerifier()
    result = verify_structured_policy_batch(
        candidates=(stored.policy, *stored.riders),
        structurer_request_id="synthetic-retained-request",
        evidence=synthetic_policy_evidence(),
        provider=provider,
        verifier_model="synthetic-verifier",
    )
    assert result.classification == "SUCCESS"
    assert provider.calls == ["policy_candidate_batch_verifier_v2"]
    assert all(
        item.provider_request_ids[0] == "synthetic-retained-request" for item in result.candidates
    )

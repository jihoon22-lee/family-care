"""Bounded one-policy/multiple-rider structurer orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from familycare_worker.ai.policy_pipeline import run_policy_batch_pipeline
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.schemas import openai_schema_registry

from workers.analyzer.tests.fixtures.policy_ai_responses import (
    SYNTHETIC_POLICY_EVIDENCE_ID,
    SYNTHETIC_RIDER_EVIDENCE_ID,
    synthetic_policy_evidence,
)

POLICY_CANDIDATE_ID = "00000000-0000-4000-8000-000000000301"
RIDER_CANDIDATE_ID = "00000000-0000-4000-8000-000000000302"


def _candidate_batch() -> dict[str, object]:
    return {
        "schema_version": "2",
        "policy": {
            "schema_version": "1",
            "candidate_id": POLICY_CANDIDATE_ID,
            "candidate_kind": "policy_contract",
            "fields": [
                {
                    "field_id": "insurer",
                    "value": "Sample Insurer",
                    "evidence_ids": [str(SYNTHETIC_POLICY_EVIDENCE_ID)],
                },
                {
                    "field_id": "product_name",
                    "value": "Sample Product",
                    "evidence_ids": [str(SYNTHETIC_POLICY_EVIDENCE_ID)],
                },
                {
                    "field_id": "policy_status",
                    "value": "active",
                    "evidence_ids": [str(SYNTHETIC_POLICY_EVIDENCE_ID)],
                },
            ],
        },
        "riders": [
            {
                "schema_version": "1",
                "candidate_id": RIDER_CANDIDATE_ID,
                "candidate_kind": "rider",
                "fields": [
                    {
                        "field_id": "rider_name",
                        "value": "Sample Rider",
                        "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
                    },
                    {
                        "field_id": "rider_key",
                        "value": "sample-rider",
                        "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
                    },
                    {
                        "field_id": "benefit_type",
                        "value": "fixed",
                        "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
                    },
                    {
                        "field_id": "rider_status",
                        "value": "active",
                        "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
                    },
                ],
            }
        ],
    }


class BatchProvider:
    def __init__(
        self,
        structurer: Mapping[str, object],
        *,
        rider_decision: str = "approved",
    ) -> None:
        self.structurer = deepcopy(dict(structurer))
        self.rider_decision = rider_decision
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        del model, system_instruction
        self.calls.append((schema_name, deepcopy(dict(input_payload))))
        if "batch_structurer" in schema_name:
            return ProviderResponse(
                payload=self.structurer,
                request_id="synthetic-structurer-request",
            )
        candidate = input_payload.get("candidate")
        assert isinstance(candidate, Mapping)
        candidate_id = str(candidate["candidate_id"])
        rider = candidate_id == RIDER_CANDIDATE_ID
        decision = self.rider_decision if rider else "approved"
        evidence_id = SYNTHETIC_RIDER_EVIDENCE_ID if rider else SYNTHETIC_POLICY_EVIDENCE_ID
        return ProviderResponse(
            payload={
                "schema_version": "1",
                "candidate_id": candidate_id,
                "decision": decision,
                "evidence_ids": [str(evidence_id)],
                "issue_codes": ["LOW_CONFIDENCE"] if decision == "needs_review" else [],
            },
            request_id=f"synthetic-verifier-request-{len(self.calls)}",
        )


def _run(provider: BatchProvider) -> Any:
    return run_policy_batch_pipeline(
        evidence=synthetic_policy_evidence(),
        provider=provider,
        structurer_model="synthetic-structurer",
        verifier_model="synthetic-verifier",
    )


def test_batch_pipeline_structures_one_policy_and_verifies_each_rider() -> None:
    provider = BatchProvider(_candidate_batch())

    result = _run(provider)

    assert result.classification == "SUCCESS"
    assert [candidate.candidate_kind for candidate in result.candidates] == [
        "policy_contract",
        "rider",
    ]
    assert all(candidate.status == "AI_VERIFIED" for candidate in result.candidates)
    assert len(provider.calls) == 3
    assert provider.calls[0][0] == "policy_candidate_batch_structurer_v2"
    assert [call[1]["candidate"]["candidate_id"] for call in provider.calls[1:]] == [
        POLICY_CANDIDATE_ID,
        RIDER_CANDIDATE_ID,
    ]


def test_batch_pipeline_retains_independent_needs_review_rider() -> None:
    provider = BatchProvider(_candidate_batch(), rider_decision="needs_review")

    result = _run(provider)

    assert result.classification == "NEEDS_REVIEW"
    assert [candidate.status for candidate in result.candidates] == [
        "AI_VERIFIED",
        "NEEDS_REVIEW",
    ]
    assert result.candidates[1].issue_codes == ("LOW_CONFIDENCE",)


def test_batch_pipeline_rejects_duplicate_candidate_identity_before_verification() -> None:
    response = _candidate_batch()
    riders = response["riders"]
    assert isinstance(riders, list)
    rider = riders[0]
    assert isinstance(rider, dict)
    rider["candidate_id"] = POLICY_CANDIDATE_ID
    provider = BatchProvider(response)

    result = _run(provider)

    assert result.classification == "VALIDATION_ERROR"
    assert result.candidates == ()
    assert len(provider.calls) == 1


def test_openai_registry_exposes_strict_bounded_batch_schema() -> None:
    schema = openai_schema_registry()["policy_candidate_batch_structurer_v2"]

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "2"
    assert schema["properties"]["riders"]["maxItems"] == 31

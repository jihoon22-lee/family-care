"""Every primary range must receive an explicit disposition, even without a policy header."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from familycare_worker.ai.policy_ranges import build_policy_envelopes
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.range_structurer import RangePayloadInvalid, structure_policy_range
from familycare_worker.document_structure import plan_structure_chunks

from workers.analyzer.tests.test_document_structure import _block, _build, _extraction, _page


def _envelope() -> Any:
    source = _build(_extraction(_page(1, [_block("보험증권 가입금액 Sample Rider 317")])))
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=256
    )
    return build_policy_envelopes(source, plan, sensitive_terms=()).envelopes[0]


def _payload(envelope: Any) -> dict[str, Any]:
    candidate_id = str(uuid4())
    return {
        "schema_version": "3",
        "candidates": [
            {
                "schema_version": "1",
                "candidate_id": candidate_id,
                "candidate_kind": "rider",
                "fields": [
                    {
                        "field_id": "rider_name",
                        "value": "Sample Rider",
                        "evidence_ids": [str(envelope.primary_evidence_ids[0])],
                    }
                ],
            }
        ],
        "ranges": [
            {
                "chunk_id": envelope.primary_chunk_ids[0],
                "outcome": "CANDIDATES",
                "candidate_ids": [candidate_id],
            }
        ],
    }


class Provider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> ProviderResponse:
        self.calls.append(kwargs)
        return ProviderResponse(payload=self.payload, request_id="synthetic-range-request")


def test_later_rider_range_does_not_require_an_invented_policy_header() -> None:
    envelope = _envelope()
    provider = Provider(_payload(envelope))
    batch, request_id = structure_policy_range(
        envelope=envelope, provider=provider, model="synthetic"
    )
    assert len(batch.candidates) == 1
    assert batch.candidates[0].candidate_kind == "rider"
    assert request_id == "synthetic-range-request"
    assert len(provider.calls) == 1
    assert provider.calls[0]["schema_name"] == "policy_range_structurer_v3"
    assert (
        provider.calls[0]["input_payload"]["primary_ranges"][0]["chunk_id"]
        == envelope.primary_chunk_ids[0]
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_range",
        "duplicate_range",
        "invented_range",
        "unreferenced_candidate",
        "invented_evidence",
    ],
)
def test_missing_or_forged_range_coverage_is_rejected(change: str) -> None:
    envelope = _envelope()
    payload = deepcopy(_payload(envelope))
    if change == "missing_range":
        payload["ranges"] = []
    elif change == "duplicate_range":
        payload["ranges"].append(deepcopy(payload["ranges"][0]))
    elif change == "invented_range":
        payload["ranges"][0]["chunk_id"] = "b" * 64
    elif change == "unreferenced_candidate":
        payload["ranges"][0] = {
            "chunk_id": envelope.primary_chunk_ids[0],
            "outcome": "UNRESOLVED",
            "candidate_ids": [],
        }
    else:
        payload["candidates"][0]["fields"][0]["evidence_ids"] = [str(uuid4())]
    with pytest.raises(RangePayloadInvalid):
        structure_policy_range(envelope=envelope, provider=Provider(payload), model="synthetic")


def test_non_enrollment_range_returns_zero_candidates_without_fabrication() -> None:
    envelope = _envelope()
    payload = {
        "schema_version": "3",
        "candidates": [],
        "ranges": [
            {
                "chunk_id": envelope.primary_chunk_ids[0],
                "outcome": "NO_ENROLLMENT_FACTS",
                "candidate_ids": [],
            }
        ],
    }
    batch, _ = structure_policy_range(
        envelope=envelope, provider=Provider(payload), model="synthetic"
    )
    assert batch.candidates == ()
    assert batch.ranges[0].outcome == "NO_ENROLLMENT_FACTS"


@pytest.mark.parametrize(
    "change", ["oversize", "duplicate_field", "duplicate_evidence", "empty_evidence"]
)
def test_unpersistable_provider_fields_are_rejected_before_verification(change: str) -> None:
    envelope = _envelope()
    payload = _payload(envelope)
    field = payload["candidates"][0]["fields"][0]
    if change == "oversize":
        field["value"] = "S" * 241
    elif change == "duplicate_field":
        payload["candidates"][0]["fields"].append(deepcopy(field))
    elif change == "duplicate_evidence":
        field["evidence_ids"] *= 2
    else:
        field["evidence_ids"] = []
    with pytest.raises(RangePayloadInvalid):
        structure_policy_range(envelope=envelope, provider=Provider(payload), model="synthetic")

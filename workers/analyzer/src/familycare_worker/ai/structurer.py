"""Strict Evidence-to-policy-candidate structuring stage."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from familycare_worker.ai.provider import (
    AiProvider,
    EvidenceSlice,
    ProviderValidationError,
    provider_payload,
)
from familycare_worker.ai.schemas import StructurerCandidate

STRUCTURER_SCHEMA_NAME = "policy_candidate_structurer_v1"
_STRUCTURER_INSTRUCTION = (
    "Structure only facts explicitly supported by the supplied Evidence. "
    "Do not infer enrollment from terms, add fields, or make eligibility or payment decisions."
)


class StructurerPayloadInvalid(ProviderValidationError):
    """The structurer returned data outside the strict candidate schema."""


def structure_policy_candidate(
    *,
    evidence: Sequence[EvidenceSlice],
    provider: AiProvider,
    model: str,
) -> tuple[StructurerCandidate, str]:
    """Call the structurer with one bounded Evidence batch."""

    request: Mapping[str, object] = {
        "schema_version": "1",
        "evidence": [item.to_provider_payload() for item in evidence],
    }
    response = provider.complete(
        model=model,
        schema_name=STRUCTURER_SCHEMA_NAME,
        system_instruction=_STRUCTURER_INSTRUCTION,
        input_payload=request,
    )
    payload, request_id = provider_payload(response)
    try:
        candidate = StructurerCandidate.model_validate_json(
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
            strict=True,
        )
    except ValidationError:
        raise StructurerPayloadInvalid from None
    return candidate, request_id


__all__ = ["STRUCTURER_SCHEMA_NAME", "StructurerPayloadInvalid", "structure_policy_candidate"]

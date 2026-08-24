"""Independent policy-candidate Evidence verification stage."""

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
from familycare_worker.ai.schemas import StructurerCandidate, VerifierDecision

VERIFIER_SCHEMA_NAME = "policy_candidate_verifier_v1"
_VERIFIER_INSTRUCTION = (
    "Verify only the supplied candidate against the supplied Evidence. "
    "You may approve, reject, or request review, but may not add fields, facts, or Evidence IDs."
)


class VerifierPayloadInvalid(ProviderValidationError):
    """The verifier returned data outside its decision-only schema."""


class VerifierInventedField(VerifierPayloadInvalid):
    """The verifier attempted to return a candidate field."""


def verify_policy_candidate(
    *,
    candidate: StructurerCandidate,
    evidence: Sequence[EvidenceSlice],
    provider: AiProvider,
    model: str,
) -> tuple[VerifierDecision, str]:
    """Verify without giving the verifier an authority-bearing output shape."""

    request: Mapping[str, object] = {
        "schema_version": "1",
        "candidate": candidate.model_dump(mode="json"),
        "evidence": [item.to_provider_payload() for item in evidence],
    }
    response = provider.complete(
        model=model,
        schema_name=VERIFIER_SCHEMA_NAME,
        system_instruction=_VERIFIER_INSTRUCTION,
        input_payload=request,
    )
    payload, request_id = provider_payload(response)
    if "fields" in payload:
        raise VerifierInventedField
    try:
        decision = VerifierDecision.model_validate_json(
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
            strict=True,
        )
    except ValidationError:
        raise VerifierPayloadInvalid from None
    return decision, request_id


__all__ = [
    "VERIFIER_SCHEMA_NAME",
    "VerifierInventedField",
    "VerifierPayloadInvalid",
    "verify_policy_candidate",
]

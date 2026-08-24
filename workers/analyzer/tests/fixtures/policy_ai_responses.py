"""Wholly synthetic provider responses for the policy-candidate pipeline tests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

SYNTHETIC_POLICY_DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000101")
SYNTHETIC_FOREIGN_DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000102")
SYNTHETIC_POLICY_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000201")
SYNTHETIC_RIDER_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000202")
SYNTHETIC_UNKNOWN_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000299")
SYNTHETIC_CANDIDATE_ID = "00000000-0000-4000-8000-000000000301"

SYNTHETIC_RAW_PROVIDER_MARKER = "synthetic-raw-provider-marker"
SYNTHETIC_PROVIDER_REQUEST_ID = "synthetic-provider-request-001"


VALID_STRUCTURED: dict[str, object] = {
    "schema_version": "1",
    "candidate_id": SYNTHETIC_CANDIDATE_ID,
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
            "value": "fixed_amount",
            "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
        },
        {
            "field_id": "sum_assured",
            "value": 1000,
            "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
        },
        {
            "field_id": "currency",
            "value": "USD",
            "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
        },
        {
            "field_id": "rider_status",
            "value": "enrolled",
            "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
        },
    ],
}

VALID_VERIFIED: dict[str, object] = {
    "schema_version": "1",
    "candidate_id": SYNTHETIC_CANDIDATE_ID,
    "decision": "approved",
    "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
    "issue_codes": [],
}

VERIFIER_NEEDS_REVIEW: dict[str, object] = {
    "schema_version": "1",
    "candidate_id": SYNTHETIC_CANDIDATE_ID,
    "decision": "needs_review",
    "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
    "issue_codes": ["LOW_CONFIDENCE"],
}

VERIFIER_REJECTED: dict[str, object] = {
    "schema_version": "1",
    "candidate_id": SYNTHETIC_CANDIDATE_ID,
    "decision": "rejected",
    "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
    "issue_codes": ["UNSUPPORTED_STRUCTURE"],
}

INVENTED_EVIDENCE: dict[str, object] = {
    "schema_version": "1",
    "candidate_id": SYNTHETIC_CANDIDATE_ID,
    "decision": "approved",
    "evidence_ids": [str(SYNTHETIC_UNKNOWN_EVIDENCE_ID)],
    "issue_codes": [],
}

INVENTED_FIELD: dict[str, object] = {
    "schema_version": "1",
    "candidate_id": SYNTHETIC_CANDIDATE_ID,
    "decision": "approved",
    "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
    "issue_codes": [],
    "fields": [
        {
            "field_id": "insurer",
            "value": "Invented Insurer",
            "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
        }
    ],
}


def synthetic_policy_evidence() -> tuple[Any, ...]:
    """Return bounded EvidenceSlice values with synthetic policy content only."""

    # Import lazily so this fixture remains a response/evidence fixture and does
    # not provide or duplicate the production EvidenceSlice implementation.
    from familycare_worker.ai.provider import EvidenceSlice

    return (
        EvidenceSlice(
            evidence_id=SYNTHETIC_POLICY_EVIDENCE_ID,
            document_version_id=SYNTHETIC_POLICY_DOCUMENT_VERSION_ID,
            page=1,
            text="Sample Policy contract starts 2026-01-01 and ends 2026-12-31.",
            bbox=(10.0, 20.0, 210.0, 80.0),
        ),
        EvidenceSlice(
            evidence_id=SYNTHETIC_RIDER_EVIDENCE_ID,
            document_version_id=SYNTHETIC_POLICY_DOCUMENT_VERSION_ID,
            page=2,
            text="Sample Rider is enrolled with a fixed benefit of 1000 USD.",
            bbox=(10.0, 20.0, 260.0, 80.0),
        ),
    )


@dataclass(frozen=True)
class ProviderCall:
    """The bounded, observable part of one fake provider request."""

    stage: str
    model: str
    schema_name: str
    system_instruction: str
    input_payload: Mapping[str, object]


class SyntheticProviderResponse(dict[str, object]):
    """Mapping plus the fields a provider adapter normally exposes."""

    payload: Mapping[str, object]
    request_id: str

    def __init__(self, payload: Mapping[str, object], *, request_id: str) -> None:
        super().__init__(deepcopy(dict(payload)))
        self.payload = deepcopy(dict(payload))
        self.request_id = request_id


class FakeProvider:
    """Provider-neutral fake; it never opens a network or reads environment state."""

    def __init__(
        self,
        *,
        structurer: Mapping[str, object] | BaseException = VALID_STRUCTURED,
        verifier: Mapping[str, object] | BaseException = VALID_VERIFIED,
    ) -> None:
        self.structurer = structurer
        self.verifier = verifier
        self.calls: list[ProviderCall] = []

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> SyntheticProviderResponse:
        """Return the configured stage response and record only bounded inputs."""

        stage = self._stage_for(schema_name)
        self.calls.append(
            ProviderCall(
                stage=stage,
                model=model,
                schema_name=schema_name,
                system_instruction=system_instruction,
                input_payload=deepcopy(dict(input_payload)),
            )
        )
        response = self.structurer if stage == "structurer" else self.verifier
        if isinstance(response, BaseException):
            raise response
        return SyntheticProviderResponse(
            response,
            request_id=f"{SYNTHETIC_PROVIDER_REQUEST_ID}-{len(self.calls):03d}",
        )

    def _stage_for(self, schema_name: str) -> str:
        lowered = schema_name.lower()
        if "struct" in lowered:
            return "structurer"
        if "verif" in lowered:
            return "verifier"
        return "structurer" if not self.calls else "verifier"


__all__ = [
    "FakeProvider",
    "INVENTED_EVIDENCE",
    "INVENTED_FIELD",
    "ProviderCall",
    "SYNTHETIC_CANDIDATE_ID",
    "SYNTHETIC_FOREIGN_DOCUMENT_VERSION_ID",
    "SYNTHETIC_POLICY_DOCUMENT_VERSION_ID",
    "SYNTHETIC_POLICY_EVIDENCE_ID",
    "SYNTHETIC_RAW_PROVIDER_MARKER",
    "SYNTHETIC_RIDER_EVIDENCE_ID",
    "SYNTHETIC_UNKNOWN_EVIDENCE_ID",
    "SyntheticProviderResponse",
    "VALID_STRUCTURED",
    "VALID_VERIFIED",
    "VERIFIER_NEEDS_REVIEW",
    "VERIFIER_REJECTED",
    "synthetic_policy_evidence",
]

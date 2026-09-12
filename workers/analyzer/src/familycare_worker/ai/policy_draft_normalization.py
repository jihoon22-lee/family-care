"""Reduce a retained draft using existing field proofs, without granting authority.

The caller owns source/lease/provenance validation and preservation of the original
batch. This pure result must still pass the independent verifier, candidate
validator, grounder and publication checks. Field support is not insurance
eligibility or a payout decision. Local nodes have the same trusted, immutable
source contract as ``ground_range_candidate``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from familycare_worker.ai.policy_ranges import RANGE_ENVELOPE_REVISION, PolicyRangeEnvelope
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.ai.schemas import (
    CandidateField,
    PolicyCandidate,
    PolicyCandidateFieldId,
    StructurerCandidate,
)

POLICY_DRAFT_NORMALIZATION_REVISION = "policy-draft-normalization-v1"
CERTIFICATE_TITLE_NORMALIZATION_REVISION = "policy-draft-normalization-v2"

type AdjustmentReason = Literal[
    "REQUIRED_FIELD_MISSING",
    "REQUIRED_FIELD_UNSUPPORTED",
    "OPTIONAL_FIELD_UNSUPPORTED",
    "RIDER_KEY_DERIVED_FROM_NAME",
    "RANGE_PRIMARY_UNSUPPORTED",
    "CANDIDATE_UNREFERENCED",
]


class PolicyDraftInvalid(ValueError):
    """Fixed diagnostic for malformed or inconsistent draft/source identities."""

    def __init__(self) -> None:
        super().__init__("POLICY_DRAFT_INVALID")


@dataclass(frozen=True)
class PolicyDraftAdjustment:
    """Value-free receipt; ids identify the unchanged input, not new facts."""

    reason: AdjustmentReason
    candidate_id: UUID | None = None
    field_id: PolicyCandidateFieldId | None = None
    chunk_id: str | None = None


@dataclass(frozen=True, repr=False)
class PolicyDraftNormalization:
    batch: PolicyRangeBatch
    adjustments: tuple[PolicyDraftAdjustment, ...]
    partial: bool
    revision: str = POLICY_DRAFT_NORMALIZATION_REVISION


def _check_source(batch: PolicyRangeBatch, envelope: PolicyRangeEnvelope) -> None:
    # Revalidate even frozen models: model_copy/model_construct bypass validation.
    PolicyRangeBatch.model_validate(batch.model_dump(), strict=True)
    chunks, primary, evidence = (
        envelope.primary_chunk_ids,
        envelope.primary_evidence_ids,
        envelope.evidence,
    )
    ids = {item.evidence_id for item in evidence}
    if (
        not 1 <= len(chunks) <= 32
        or len(chunks) != len(primary)
        or len(set(chunks)) != len(chunks)
        or len(set(primary)) != len(primary)
        or any(re.fullmatch(r"[0-9a-f]{64}", key) is None for key in chunks)
        or not 1 <= len(evidence) <= 64
        or len(ids) != len(evidence)
        or sum(len(item.text) for item in evidence) > 16384
        or len({item.document_version_id for item in evidence}) != 1
        or set(primary) != {item.evidence_id for item in evidence if item.primary}
        or {item.chunk_id for item in batch.ranges} != set(chunks)
        or any(
            not set(field.evidence_ids) <= ids
            for candidate in batch.candidates
            for field in candidate.fields
        )
    ):
        raise PolicyDraftInvalid
    for item in evidence:
        item.__post_init__()
    identity = json.dumps(
        [RANGE_ENVELOPE_REVISION, chunks, [dict(item.to_provider_payload()) for item in evidence]],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if hashlib.sha256(identity).hexdigest() != envelope.envelope_id:
        raise PolicyDraftInvalid


def _check_nodes(
    envelope: PolicyRangeEnvelope, nodes: Mapping[str, Mapping[str, Any]] | None
) -> None:
    if nodes is None:
        return
    for item in envelope.evidence:
        node = nodes[item.node_id]
        if (
            node["node_id"] != item.node_id
            or node["page_number"] != item.page
            or not isinstance(node["text"], str)
            or item.end > len(node["text"])
        ):
            raise PolicyDraftInvalid


def _supported(
    source: StructurerCandidate,
    field: CandidateField,
    envelope: PolicyRangeEnvelope,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
    *,
    allow_certificate_title: bool = False,
) -> bool:
    # Probe one field with its rider-name anchor through the unchanged program
    # proof. The temporary status only enables that proof; it is never returned.
    name = next((item for item in source.fields if item.field_id == "rider_name"), None)
    fields = (
        (name, field)
        if source.candidate_kind == "rider" and name is not None and field.field_id != "rider_name"
        else (field,)
    )
    candidate = PolicyCandidate(
        candidate_id=source.candidate_id,
        candidate_kind=source.candidate_kind,
        status="AI_VERIFIED",
        fields=fields,
        issue_codes=(),
        provider_request_ids=(),
    )
    proof = ground_range_candidate(
        candidate,
        envelope.evidence,
        local_nodes=local_nodes,
        allow_certificate_title=allow_certificate_title,
    )
    proven = next((item for item in proof.fields if item.field_id == field.field_id), None)
    # The grounder can enrich table citations and demote guessed benefit types.
    # Only prove the original value here; neither alteration enters this draft.
    return (
        proof.status == "AI_VERIFIED"
        and proven is not None
        and type(proven.value) is type(field.value)
        and proven.value == field.value
    )


def _candidate_draft(
    source: StructurerCandidate,
    envelope: PolicyRangeEnvelope,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
    adjustments: list[PolicyDraftAdjustment],
    *,
    allow_certificate_title: bool = False,
) -> StructurerCandidate | None:
    fields = {item.field_id: item for item in source.fields}
    required: tuple[PolicyCandidateFieldId, ...] = (
        ("insurer", "product_name")
        if source.candidate_kind == "policy_contract"
        else ("rider_name",)
    )
    unsupported = False
    for key in required:
        field = fields.get(key)
        if field is None or not _supported(
            source, field, envelope, local_nodes, allow_certificate_title=allow_certificate_title
        ):
            adjustments.append(
                PolicyDraftAdjustment(
                    "REQUIRED_FIELD_MISSING" if field is None else "REQUIRED_FIELD_UNSUPPORTED",
                    source.candidate_id,
                    key,
                )
            )
            unsupported = True
    if unsupported:
        return None

    retained = []
    for field in source.fields:
        if field.field_id in required or _supported(
            source, field, envelope, local_nodes, allow_certificate_title=allow_certificate_title
        ):
            retained.append(field)
        elif source.candidate_kind == "rider" and field.field_id == "rider_key":
            adjustments.append(
                PolicyDraftAdjustment(
                    "REQUIRED_FIELD_UNSUPPORTED", source.candidate_id, field.field_id
                )
            )
            return None
        else:
            adjustments.append(
                PolicyDraftAdjustment(
                    "OPTIONAL_FIELD_UNSUPPORTED", source.candidate_id, field.field_id
                )
            )
    if source.candidate_kind == "rider" and "rider_key" not in fields:
        name = fields["rider_name"]
        retained.append(
            CandidateField(field_id="rider_key", value=name.value, evidence_ids=name.evidence_ids)
        )
        adjustments.append(
            PolicyDraftAdjustment("RIDER_KEY_DERIVED_FROM_NAME", source.candidate_id, "rider_key")
        )
    return StructurerCandidate(
        schema_version=source.schema_version,
        candidate_id=source.candidate_id,
        candidate_kind=source.candidate_kind,
        fields=tuple(retained),
    )


def normalize_policy_draft(
    batch: PolicyRangeBatch,
    envelope: PolicyRangeEnvelope,
    *,
    local_nodes: Mapping[str, Mapping[str, Any]] | None = None,
    revision: str = POLICY_DRAFT_NORMALIZATION_REVISION,
) -> PolicyDraftNormalization:
    """Return a separate draft and complete loss accounting without provider I/O.

    Non-policy/context ranges may remain in the envelope. They cannot supply
    primary policy field proof. A lost candidate or primary pair makes its whole
    range UNRESOLVED, never NO_ENROLLMENT_FACTS or a silently shortened success.
    """
    try:
        if revision not in (
            POLICY_DRAFT_NORMALIZATION_REVISION,
            CERTIFICATE_TITLE_NORMALIZATION_REVISION,
        ):
            raise PolicyDraftInvalid
        _check_source(batch, envelope)
        _check_nodes(envelope, local_nodes)
        adjustments: list[PolicyDraftAdjustment] = []
        drafts = {
            candidate.candidate_id: _candidate_draft(
                candidate,
                envelope,
                local_nodes,
                adjustments,
                allow_certificate_title=revision == CERTIFICATE_TITLE_NORMALIZATION_REVISION,
            )
            for candidate in batch.candidates
        }
        primary = dict(zip(envelope.primary_chunk_ids, envelope.primary_evidence_ids, strict=True))
        ranges = []
        for disposition in batch.ranges:
            unsupported = False
            for candidate_id in disposition.candidate_ids:
                candidate = drafts[candidate_id]
                if candidate is None or not any(
                    primary[disposition.chunk_id] in field.evidence_ids
                    for field in candidate.fields
                ):
                    adjustments.append(
                        PolicyDraftAdjustment(
                            "RANGE_PRIMARY_UNSUPPORTED", candidate_id, chunk_id=disposition.chunk_id
                        )
                    )
                    unsupported = True
            ranges.append(
                RangeDisposition(
                    chunk_id=disposition.chunk_id, outcome="UNRESOLVED", candidate_ids=()
                )
                if unsupported
                else disposition
            )
        referenced = {key for disposition in ranges for key in disposition.candidate_ids}
        candidates = []
        for source in batch.candidates:
            candidate = drafts[source.candidate_id]
            if source.candidate_id not in referenced:
                adjustments.append(
                    PolicyDraftAdjustment("CANDIDATE_UNREFERENCED", source.candidate_id)
                )
            elif candidate is not None:
                candidates.append(candidate)
        normalized = PolicyRangeBatch(
            schema_version=batch.schema_version, candidates=tuple(candidates), ranges=tuple(ranges)
        )
    except KeyError, TypeError, ValueError, ValidationError:
        raise PolicyDraftInvalid from None
    return PolicyDraftNormalization(
        normalized,
        tuple(adjustments),
        any(item.reason != "RIDER_KEY_DERIVED_FROM_NAME" for item in adjustments)
        or any(item.outcome == "UNRESOLVED" for item in ranges),
        revision=revision,
    )

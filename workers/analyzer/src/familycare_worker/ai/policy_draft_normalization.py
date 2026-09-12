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
import unicodedata
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
SOURCE_SCOPED_NORMALIZATION_REVISION = "policy-draft-normalization-v3"
AMOUNT_CURRENCY_NORMALIZATION_REVISION = "policy-draft-normalization-v4"
TABLE_NAME_NORMALIZATION_REVISION = "policy-draft-normalization-v5"

type AdjustmentReason = Literal[
    "REQUIRED_FIELD_MISSING",
    "REQUIRED_FIELD_UNSUPPORTED",
    "OPTIONAL_FIELD_UNSUPPORTED",
    "RIDER_KEY_DERIVED_FROM_NAME",
    "RANGE_PRIMARY_UNSUPPORTED",
    "CANDIDATE_UNREFERENCED",
    "ISSUER_UNCONFIRMED",
    "PRIOR_VERIFIED_CANDIDATE_PRESERVED",
    "PRIOR_CANDIDATE_REVIEW_PRESERVED",
    "CURRENCY_DERIVED_FROM_AMOUNT",
    "CURRENCY_EVIDENCE_REALIGNED",
    "RIDER_NAME_RESTORED_FROM_CITED_ROW",
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
    allow_unconfirmed_insurer: bool = False,
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
        require_issuer_context=allow_unconfirmed_insurer,
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


def _table_name_draft(
    source: StructurerCandidate,
    envelope: PolicyRangeEnvelope,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
    adjustments: list[PolicyDraftAdjustment],
) -> StructurerCandidate:
    """Restore an already-cited single native row's exact name, never search by value."""
    from familycare_worker.ai.table_grounding import _NAME, _cells, _label

    if source.candidate_kind != "rider" or local_nodes is None:
        return source
    fields = {field.field_id: field for field in source.fields}
    if len(fields) != len(source.fields) or "rider_name" not in fields:
        return source
    name = fields["rider_name"]
    if _supported(source, name, envelope, local_nodes):
        return source
    cited = [e for e in envelope.evidence if e.evidence_id in name.evidence_ids]
    rows = [
        e
        for e in cited
        if e.primary
        and e.source_role == "policy"
        and local_nodes[e.node_id].get("kind") == "TABLE_ROW"
        and local_nodes[e.node_id].get("row_role") == "data"
    ]
    if len(rows) != 1:
        return source
    evidence = rows[0]
    row = local_nodes[evidence.node_id]
    if (
        row.get("source_layer") != "native"
        or evidence.start != 0
        or evidence.end != len(row["text"])
    ):
        return source
    cells = _cells(row)
    headers = [
        local_nodes[key]
        for key in row.get("context_node_ids", ())
        if key in local_nodes
        and local_nodes[key].get("kind") == "TABLE_ROW"
        and local_nodes[key].get("row_role") == "header"
    ]
    columns = {
        column
        for header in headers
        for column, cell in (_cells(header) or {}).items()
        if _label(cell["text"]) in _NAME
    }
    if cells is None or len(columns) != 1 or next(iter(columns)) not in cells:
        return source
    value = cells[next(iter(columns))]["text"]
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        return source
    # Local nodes retain unminimized source. Never reintroduce a removed name or
    # identifier into the provider draft: the complete value must already appear
    # in this exact privacy-minimized source slice.
    visible = " ".join(unicodedata.normalize("NFKC", evidence.text).split())
    if " ".join(unicodedata.normalize("NFKC", value).split()) not in visible:
        return source
    restored = name.model_copy(update={"value": value, "evidence_ids": (evidence.evidence_id,)})
    # Existing logical keys must either copy the former name or remain independently
    # proven. They are never silently redirected from another named Rider.
    key = fields.get("rider_key")
    if key is not None and key.value != name.value:
        return source
    proposed = source.model_copy(
        update={
            "fields": tuple(
                restored
                if f.field_id == "rider_name"
                else f.model_copy(update={"value": value, "evidence_ids": restored.evidence_ids})
                if f.field_id == "rider_key"
                else f
                for f in source.fields
            )
        }
    )
    # The unchanged column proof rejects ambiguous headers, examples, multiple rows,
    # missing context and conflicting source. At least the same numeric amount must
    # be proven, so a guessed citation cannot reassign a name-only candidate.
    amount = fields.get("sum_assured")
    if (
        amount is None
        or not _supported(proposed, restored, envelope, local_nodes)
        or not _supported(proposed, amount, envelope, local_nodes)
    ):
        return source
    adjustments.append(
        PolicyDraftAdjustment(
            "RIDER_NAME_RESTORED_FROM_CITED_ROW", source.candidate_id, "rider_name"
        )
    )
    return proposed


def _amount_currency_draft(
    source: StructurerCandidate,
    envelope: PolicyRangeEnvelope,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
    adjustments: list[PolicyDraftAdjustment],
) -> StructurerCandidate:
    """Derive a unique explicit unit from the already-proven amount's same row.

    No default currency, adjacent row or changed numeric value is accepted. A
    conflicting supplied currency remains unresolved rather than being corrected.
    """
    if source.candidate_kind != "rider" or len({f.field_id for f in source.fields}) != len(
        source.fields
    ):
        return source
    fields = {field.field_id: field for field in source.fields}
    amount = fields.get("sum_assured")
    if amount is None or not _supported(
        source,
        amount,
        envelope,
        local_nodes,
        allow_certificate_title=True,
        allow_unconfirmed_insurer=True,
    ):
        return source
    existing = fields.get("currency")
    if existing is not None and _supported(
        source,
        existing,
        envelope,
        local_nodes,
        allow_certificate_title=True,
        allow_unconfirmed_insurer=True,
    ):
        return source
    proven = []
    for currency in ("KRW", "USD", "EUR", "JPY"):
        field = CandidateField(
            field_id="currency", value=currency, evidence_ids=amount.evidence_ids
        )
        if _supported(
            source,
            field,
            envelope,
            local_nodes,
            allow_certificate_title=True,
            allow_unconfirmed_insurer=True,
        ):
            proven.append(field)
    if len(proven) != 1 or (existing is not None and existing.value != proven[0].value):
        return source
    field = proven[0]
    adjusted = tuple(field if f.field_id == "currency" else f for f in source.fields)
    if existing is None:
        adjusted = (*adjusted, field)
    adjustments.append(
        PolicyDraftAdjustment(
            "CURRENCY_DERIVED_FROM_AMOUNT" if existing is None else "CURRENCY_EVIDENCE_REALIGNED",
            source.candidate_id,
            "currency",
        )
    )
    return source.model_copy(update={"fields": adjusted})


def _candidate_draft(
    source: StructurerCandidate,
    envelope: PolicyRangeEnvelope,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
    adjustments: list[PolicyDraftAdjustment],
    *,
    allow_certificate_title: bool = False,
    allow_unconfirmed_insurer: bool = False,
) -> StructurerCandidate | None:
    fields = {item.field_id: item for item in source.fields}
    required: tuple[PolicyCandidateFieldId, ...] = (
        (("product_name",) if allow_unconfirmed_insurer else ("insurer", "product_name"))
        if source.candidate_kind == "policy_contract"
        else ("rider_name",)
    )
    unsupported = False
    for key in required:
        field = fields.get(key)
        if field is None or not _supported(
            source,
            field,
            envelope,
            local_nodes,
            allow_certificate_title=allow_certificate_title,
            allow_unconfirmed_insurer=allow_unconfirmed_insurer,
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
    if (
        source.candidate_kind == "policy_contract"
        and allow_unconfirmed_insurer
        and "insurer" not in fields
    ):
        adjustments.append(
            PolicyDraftAdjustment("ISSUER_UNCONFIRMED", source.candidate_id, "insurer")
        )
    for field in source.fields:
        if field.field_id in required or _supported(
            source,
            field,
            envelope,
            local_nodes,
            allow_certificate_title=allow_certificate_title,
            allow_unconfirmed_insurer=allow_unconfirmed_insurer,
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
                    "ISSUER_UNCONFIRMED"
                    if allow_unconfirmed_insurer and field.field_id == "insurer"
                    else "OPTIONAL_FIELD_UNSUPPORTED",
                    source.candidate_id,
                    field.field_id,
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
            SOURCE_SCOPED_NORMALIZATION_REVISION,
            AMOUNT_CURRENCY_NORMALIZATION_REVISION,
            TABLE_NAME_NORMALIZATION_REVISION,
        ):
            raise PolicyDraftInvalid
        _check_source(batch, envelope)
        _check_nodes(envelope, local_nodes)
        adjustments: list[PolicyDraftAdjustment] = []
        drafts = {
            candidate.candidate_id: _candidate_draft(
                _amount_currency_draft(
                    _table_name_draft(candidate, envelope, local_nodes, adjustments)
                    if revision == TABLE_NAME_NORMALIZATION_REVISION
                    else candidate,
                    envelope,
                    local_nodes,
                    adjustments,
                )
                if revision
                in {AMOUNT_CURRENCY_NORMALIZATION_REVISION, TABLE_NAME_NORMALIZATION_REVISION}
                else candidate,
                envelope,
                local_nodes,
                adjustments,
                allow_certificate_title=revision
                in {
                    CERTIFICATE_TITLE_NORMALIZATION_REVISION,
                    SOURCE_SCOPED_NORMALIZATION_REVISION,
                    AMOUNT_CURRENCY_NORMALIZATION_REVISION,
                    TABLE_NAME_NORMALIZATION_REVISION,
                },
                allow_unconfirmed_insurer=revision
                in {
                    SOURCE_SCOPED_NORMALIZATION_REVISION,
                    AMOUNT_CURRENCY_NORMALIZATION_REVISION,
                    TABLE_NAME_NORMALIZATION_REVISION,
                },
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

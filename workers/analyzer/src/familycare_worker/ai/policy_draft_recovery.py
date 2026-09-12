"""Reconcile a new draft against existing source proof before fresh verification.

Versions v7/v8 accept structurally valid responses with orphan/misassigned candidates.
The raw response remains immutable; no old verifier decision enters this module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from familycare_worker.ai.policy_draft_normalization import (
    EXPLICIT_UNIT_NORMALIZATION_REVISION,
    PolicyDraftAdjustment,
    PolicyDraftInvalid,
    PolicyDraftNormalization,
    _check_nodes,
    _check_source,
    _explicit_unit_draft,
    _supported,
    normalize_policy_draft,
)
from familycare_worker.ai.policy_ranges import PolicyRangeEnvelope, RangeEvidenceSlice
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate, StructurerCandidate
from familycare_worker.ai.scoped_policy_verifier import scoped_policy_evidence

PROVEN_CONTEXT_NORMALIZATION_REVISION = "policy-draft-normalization-v7"
SOURCE_UNIT_CURRENCY_NORMALIZATION_REVISION = "policy-draft-normalization-v8"


class _RawResponse(BaseModel):
    """Same wire fields; only cross-candidate association waits for source proof."""

    model_config = PolicyRangeBatch.model_config

    schema_version: Literal["3"]
    candidates: tuple[StructurerCandidate, ...] = Field(max_length=32)
    ranges: tuple[RangeDisposition, ...] = Field(min_length=1, max_length=32)


def _parse(response: Mapping[str, Any], envelope: PolicyRangeEnvelope) -> _RawResponse:
    raw = _RawResponse.model_validate_json(json.dumps(dict(response), allow_nan=False), strict=True)
    identities = {candidate.candidate_id for candidate in raw.candidates}
    if (
        len(identities) != len(raw.candidates)
        or len(raw.ranges) != len(envelope.primary_chunk_ids)
        or {r.chunk_id for r in raw.ranges} != set(envelope.primary_chunk_ids)
        or any(key not in identities for r in raw.ranges for key in r.candidate_ids)
    ):
        raise PolicyDraftInvalid
    # Reuse the unchanged batch validator for all field, kind and size rules.
    # This internal association only admits the schema, never a source mapping.
    admission = PolicyRangeBatch(
        schema_version=raw.schema_version,
        candidates=raw.candidates,
        ranges=tuple(
            RangeDisposition(
                chunk_id=r.chunk_id,
                outcome="CANDIDATES" if position == 0 and identities else "UNRESOLVED",
                candidate_ids=tuple(c.candidate_id for c in raw.candidates)
                if position == 0
                else (),
            )
            for position, r in enumerate(raw.ranges)
        ),
    )
    _check_source(admission, envelope)
    return raw


def _required(candidate: StructurerCandidate) -> CandidateField | None:
    key = "rider_name" if candidate.candidate_kind == "rider" else "product_name"
    return next((field for field in candidate.fields if field.field_id == key), None)


def _nominees(candidate: StructurerCandidate, primary: Mapping[str, UUID]) -> set[str]:
    field = _required(candidate)
    return {
        chunk for chunk, key in primary.items() if field is not None and key in field.evidence_ids
    }


def _claimed_ranges(candidate: StructurerCandidate, primary: Mapping[str, UUID]) -> set[str]:
    """Loss accounting covers every original primary claim, not inferred mappings."""
    evidence = {identity for field in candidate.fields for identity in field.evidence_ids}
    return {chunk for chunk, identity in primary.items() if identity in evidence}


def _normalize_one(
    candidate: StructurerCandidate,
    raw: _RawResponse,
    envelope: PolicyRangeEnvelope,
    nodes: Mapping[str, Mapping[str, Any]] | None,
    primary: Mapping[str, UUID],
    *,
    allow_source_unit_currency: bool = False,
) -> PolicyDraftNormalization | None:
    nominated = _nominees(candidate, primary)
    if not nominated:
        return None
    adjustments: list[PolicyDraftAdjustment] = []
    if allow_source_unit_currency:
        candidate = _explicit_unit_draft(
            candidate,
            envelope,
            nodes,
            adjustments,
            allow_source_unit_currency=True,
        )
    # These provisional ranges come only from the candidate's original required
    # field citations. They are discarded after v6's name/unit/field reduction;
    # final mappings below must independently prove each required primary.
    isolated = PolicyRangeBatch(
        schema_version="3",
        candidates=(candidate,),
        ranges=tuple(
            RangeDisposition(
                chunk_id=r.chunk_id,
                outcome="CANDIDATES" if r.chunk_id in nominated else "UNRESOLVED",
                candidate_ids=(candidate.candidate_id,) if r.chunk_id in nominated else (),
            )
            for r in raw.ranges
        ),
    )
    reduced = normalize_policy_draft(
        isolated, envelope, local_nodes=nodes, revision=EXPLICIT_UNIT_NORMALIZATION_REVISION
    )
    if not adjustments:
        return reduced
    return PolicyDraftNormalization(
        batch=reduced.batch,
        adjustments=(*adjustments, *reduced.adjustments),
        partial=True,
        revision=reduced.revision,
    )


def _field_primary_proven(
    candidate: StructurerCandidate,
    field: CandidateField,
    identity: UUID,
    envelope: PolicyRangeEnvelope,
    nodes: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    sources = {item.evidence_id: item for item in envelope.evidence}
    source = sources[identity]
    if (
        identity not in field.evidence_ids
        or source.source_role != "policy"
        or nodes is None
        or nodes[source.node_id].get("source_layer") != "native"
        or nodes[source.node_id].get("issue_codes")
    ):
        return False
    # A combined citation cannot lend another primary's matching value to this
    # range. Retain only this primary plus the field's existing context citations.
    isolated = field.model_copy(
        update={
            "evidence_ids": tuple(
                key for key in field.evidence_ids if key == identity or not sources[key].primary
            )
        }
    )
    if _context_nodes(isolated, envelope, nodes) is None:
        return False
    return _supported(
        candidate,
        isolated,
        envelope,
        nodes,
        allow_certificate_title=True,
        allow_unconfirmed_insurer=True,
    )


def _context_nodes(
    field: CandidateField,
    envelope: PolicyRangeEnvelope,
    nodes: Mapping[str, Mapping[str, Any]],
) -> set[str] | None:
    by_id = {item.evidence_id: item for item in envelope.evidence}
    pending = {by_id[key].node_id for key in field.evidence_ids}
    original = set(pending)
    primary_nodes = {by_id[key].node_id for key in field.evidence_ids if by_id[key].primary}
    referenced_context: set[str] = set()
    fully_covered: set[str] = set()
    visited: set[str] = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        if len(visited) > 64 or key not in nodes:
            return None
        node = nodes[key]
        slices = [item for item in envelope.evidence if item.node_id == key]
        if (
            not slices
            or node.get("source_layer") != "native"
            or node.get("issue_codes")
            or node.get("kind") not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
            or any(
                item.source_role != "policy" or item.page != node.get("page_number")
                for item in slices
            )
        ):
            return None
        end = 0
        contiguous = True
        for item in sorted(slices, key=lambda item: (item.start, item.end)):
            if not 0 <= item.start < item.end <= len(node["text"]):
                return None
            contiguous = contiguous and item.start <= end
            end = max(end, item.end)
        if contiguous and end == len(node["text"]):
            fully_covered.add(key)
        context = node.get("context_node_ids", ())
        if (
            not isinstance(context, (tuple, list))
            or len(context) > 64
            or any(not isinstance(k, str) for k in context)
        ):
            return None
        referenced_context.update(context)
        pending.update(context)
    # The original primary's exact span can already prove a field in v6. It
    # need not disclose the rest of a large node. Referenced context still needs
    # complete coverage, even if the same node also supplied a primary slice.
    if not ((visited - primary_nodes) | referenced_context) <= fully_covered:
        return None
    return visited - original


def _proven_context_draft(
    candidate: StructurerCandidate,
    envelope: PolicyRangeEnvelope,
    scope: tuple[RangeEvidenceSlice, ...],
    nodes: Mapping[str, Mapping[str, Any]] | None,
    adjustments: list[PolicyDraftAdjustment],
) -> StructurerCandidate:
    if nodes is None:
        return candidate
    probe = ground_range_candidate(
        PolicyCandidate(
            candidate_id=candidate.candidate_id,
            candidate_kind=candidate.candidate_kind,
            status="AI_VERIFIED",
            fields=candidate.fields,
            issue_codes=(),
            provider_request_ids=(),
        ),
        scope,
        local_nodes=nodes,
        allow_certificate_title=True,
        require_issuer_context=True,
        allow_primary_header_context=True,
    )
    by_id = {item.evidence_id: item for item in scope}
    original = {field.field_id: field for field in candidate.fields}
    if (
        probe.status != "AI_VERIFIED"
        or {field.field_id for field in probe.fields} != original.keys()
    ):
        return candidate
    changed = []
    for field in probe.fields:
        before = original[field.field_id]
        if (
            type(field.value) is not type(before.value)
            or field.value != before.value
            or not set(before.evidence_ids) <= set(field.evidence_ids)
            or len(field.evidence_ids) > 16
        ):
            return candidate
        extras = set(field.evidence_ids) - set(before.evidence_ids)
        if extras:
            context = _context_nodes(before, envelope, nodes)
            if (
                context is None
                or not extras <= by_id.keys()
                or any(
                    by_id[key].node_id not in context
                    or (
                        nodes[by_id[key].node_id].get("kind") == "TABLE_ROW"
                        and nodes[by_id[key].node_id].get("row_role") != "header"
                    )
                    for key in extras
                )
            ):
                return candidate
            changed.append(field.field_id)
    if not changed:
        return candidate
    for field_id in changed:
        adjustments.append(
            PolicyDraftAdjustment("FIELD_CONTEXT_EVIDENCE_PROVEN", candidate.candidate_id, field_id)
        )
    return candidate.model_copy(update={"fields": probe.fields})


def normalize_policy_response(
    raw_response: Mapping[str, Any],
    envelope: PolicyRangeEnvelope,
    *,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
    revision: str = PROVEN_CONTEXT_NORMALIZATION_REVISION,
) -> PolicyDraftNormalization:
    """Reconcile only independently proven mappings and field-specific context.

    Invalid wire identities reject the response. Unproven individual candidates
    retain explicit loss and UNRESOLVED source ranges, while other ranges survive.
    Neither temporary program probes nor previous verifier decisions approve it.
    """
    try:
        if revision not in (
            PROVEN_CONTEXT_NORMALIZATION_REVISION,
            SOURCE_UNIT_CURRENCY_NORMALIZATION_REVISION,
        ):
            raise PolicyDraftInvalid
        raw = _parse(raw_response, envelope)
        _check_nodes(envelope, local_nodes)
        primary = dict(zip(envelope.primary_chunk_ids, envelope.primary_evidence_ids, strict=True))
        adjustments: list[PolicyDraftAdjustment] = []
        drafts: dict[UUID, StructurerCandidate] = {}
        links: dict[UUID, set[str]] = {}
        blocked: set[str] = set()
        for original in raw.candidates:
            old_links = {r.chunk_id for r in raw.ranges if original.candidate_id in r.candidate_ids}
            reduced = _normalize_one(
                original,
                raw,
                envelope,
                local_nodes,
                primary,
                allow_source_unit_currency=revision == SOURCE_UNIT_CURRENCY_NORMALIZATION_REVISION,
            )
            if reduced is not None:
                adjustments.extend(reduced.adjustments)
            candidate = (
                reduced.batch.candidates[0]
                if reduced is not None and reduced.batch.candidates
                else None
            )
            valid = {
                chunk
                for chunk in old_links
                if candidate is not None
                and any(
                    _field_primary_proven(candidate, field, primary[chunk], envelope, local_nodes)
                    for field in candidate.fields
                )
            }
            unsupported = old_links - valid
            for chunk in sorted(unsupported):
                adjustments.append(
                    PolicyDraftAdjustment(
                        "CANDIDATE_RANGE_UNSUPPORTED", original.candidate_id, chunk_id=chunk
                    )
                )
            if candidate is not None and (unsupported or not old_links):
                field = _required(candidate)
                proven = {
                    chunk
                    for chunk, identity in primary.items()
                    if field is not None
                    and _field_primary_proven(candidate, field, identity, envelope, local_nodes)
                }
                if len(proven) == 1:
                    for chunk in sorted(proven - valid):
                        adjustments.append(
                            PolicyDraftAdjustment(
                                "CANDIDATE_RANGE_RECONCILED", candidate.candidate_id, chunk_id=chunk
                            )
                        )
                    valid.update(proven)
            if candidate is None or not valid:
                blocked.update(old_links | _claimed_ranges(original, primary))
                if candidate is not None:
                    blocked.update(_nominees(candidate, primary))
                adjustments.append(
                    PolicyDraftAdjustment("CANDIDATE_UNREFERENCED", original.candidate_id)
                )
                if not old_links:
                    adjustments.append(
                        PolicyDraftAdjustment("CANDIDATE_RANGE_UNSUPPORTED", original.candidate_id)
                    )
                continue
            drafts[candidate.candidate_id] = candidate
            links[candidate.candidate_id] = valid
        for identity in tuple(drafts):
            links[identity].difference_update(blocked)
            if not links[identity]:
                del drafts[identity]
                adjustments.append(PolicyDraftAdjustment("CANDIDATE_UNREFERENCED", identity))
        ranges = []
        for old in raw.ranges:
            eligible = {
                c.candidate_id
                for c in raw.candidates
                if c.candidate_id in drafts and old.chunk_id in links[c.candidate_id]
            }
            identities = tuple(key for key in old.candidate_ids if key in eligible) + tuple(
                c.candidate_id
                for c in raw.candidates
                if c.candidate_id in eligible and c.candidate_id not in old.candidate_ids
            )
            outcome: Literal["CANDIDATES", "UNRESOLVED", "NO_ENROLLMENT_FACTS"] = (
                "CANDIDATES"
                if identities
                else "UNRESOLVED"
                if old.chunk_id in blocked or old.outcome == "CANDIDATES"
                else old.outcome
            )
            ranges.append(
                RangeDisposition(chunk_id=old.chunk_id, outcome=outcome, candidate_ids=identities)
            )
        batch = PolicyRangeBatch(
            schema_version="3",
            candidates=tuple(
                drafts[c.candidate_id] for c in raw.candidates if c.candidate_id in drafts
            ),
            ranges=tuple(ranges),
        )
        scope = scoped_policy_evidence(
            batch, envelope, local_nodes, allow_primary_header_context=True
        )
        batch = batch.model_copy(
            update={
                "candidates": tuple(
                    _proven_context_draft(c, envelope, scope, local_nodes, adjustments)
                    for c in batch.candidates
                )
            }
        )
        batch = PolicyRangeBatch.model_validate_json(batch.model_dump_json(), strict=True)
        _check_source(batch, envelope)
        return PolicyDraftNormalization(
            batch=batch,
            adjustments=tuple(dict.fromkeys(adjustments)),
            partial=any(a.reason != "RIDER_KEY_DERIVED_FROM_NAME" for a in adjustments)
            or any(r.outcome == "UNRESOLVED" for r in batch.ranges),
            revision=revision,
        )
    except KeyError, TypeError, ValueError, AttributeError, OverflowError, ValidationError:
        raise PolicyDraftInvalid from None

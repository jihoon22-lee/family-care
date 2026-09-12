"""Select existing minimized field evidence without dropping declared source context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from familycare_worker.ai.policy_ranges import PolicyRangeEnvelope, RangeEvidenceSlice
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import PolicyCandidate

FIELD_SCOPE_REVISION = "cited-fields-v1"


def scoped_policy_evidence(
    batch: PolicyRangeBatch,
    envelope: PolicyRangeEnvelope,
    local_nodes: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[RangeEvidenceSlice, ...]:
    """Return whole context closure, or the original envelope on any uncertainty.

    A temporary grounding probe discovers existing header/unit citations. Its
    status and fields confer no approval and never replace the draft. Every
    returned object comes from the already minimized envelope; raw node text is
    used only to check source coverage, never to reconstruct provider input.
    """
    full = envelope.evidence
    try:
        if local_nodes is None or not 1 <= len(batch.candidates) <= 32 or not 1 <= len(full) <= 64:
            return full
        by_id = {item.evidence_id: item for item in full}
        if len(by_id) != len(full):
            return full
        selected: set[UUID] = set()
        for candidate in batch.candidates:
            original = {field.field_id: field.value for field in candidate.fields}
            if not original or len(original) != len(candidate.fields):
                return full
            selected.update(key for field in candidate.fields for key in field.evidence_ids)
            if not selected <= by_id.keys():
                return full
            probe = ground_range_candidate(
                PolicyCandidate(
                    candidate_id=candidate.candidate_id,
                    candidate_kind=candidate.candidate_kind,
                    status="AI_VERIFIED",
                    fields=candidate.fields,
                    issue_codes=(),
                    provider_request_ids=(),
                ),
                full,
                local_nodes=local_nodes,
                allow_certificate_title=True,
                require_issuer_context=True,
            )
            grounded = {field.field_id: field.value for field in probe.fields}
            if probe.status != "AI_VERIFIED" or any(
                type(grounded.get(key)) is not type(value) or grounded.get(key) != value
                for key, value in original.items()
            ):
                return full
            selected.update(key for field in probe.fields for key in field.evidence_ids)
        if not selected or not selected <= by_id.keys():
            return full
        by_node: dict[str, list[RangeEvidenceSlice]] = {}
        for item in full:
            by_node.setdefault(item.node_id, []).append(item)
        pending = {by_id[key].node_id for key in selected}
        visited: set[str] = set()
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            if len(visited) > 64 or key not in local_nodes or key not in by_node:
                return full
            node = local_nodes[key]
            slices = by_node[key]
            if (
                node.get("node_id") != key
                or node.get("source_layer") != "native"
                or node.get("kind") not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
                or node.get("issue_codes")
                or (node["kind"] == "TABLE_ROW" and node.get("row_role") not in {"header", "data"})
                or not isinstance(node.get("text"), str)
                or any(
                    item.source_role != "policy" or item.page != node.get("page_number")
                    for item in slices
                )
            ):
                return full
            # Include every existing slice for each cited/context node. A gap or
            # truncated context requires the full original envelope, not a crop.
            end = 0
            for item in sorted(slices, key=lambda item: (item.start, item.end)):
                if item.start > end or not 0 <= item.start < item.end <= len(node["text"]):
                    return full
                end = max(end, item.end)
            if end != len(node["text"]):
                return full
            selected.update(item.evidence_id for item in slices)
            contexts = node.get("context_node_ids", ())
            if (
                not isinstance(contexts, (list, tuple))
                or len(contexts) > 64
                or any(not isinstance(value, str) for value in contexts)
            ):
                return full
            pending.update(contexts)
        return tuple(item for item in full if item.evidence_id in selected)
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return full

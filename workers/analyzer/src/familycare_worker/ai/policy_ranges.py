"""Build minimized provider envelopes while retaining each primary source range."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import ClassVar
from uuid import NAMESPACE_URL, UUID, uuid5

from familycare_worker.ai.minimizer import EvidenceMinimizationError, SourceWindowMinimizer
from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.document_structure import (
    ChunkPlan,
    DocumentStructure,
    DocumentStructureError,
    Role,
    StructureChunk,
    UnprocessedRange,
    node_source_roles,
    plan_structure_chunks,
)

RANGE_ENVELOPE_REVISION = "policy-range-envelope-v3"


@dataclass(frozen=True, repr=False)
class RangeEvidenceSlice(EvidenceSlice):
    maximum_text_characters: ClassVar[int] = 8192
    node_id: str = ""
    start: int = 0
    end: int = 0
    primary: bool = False
    source_role: Role = "unknown"

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            len(self.node_id) != 64
            or type(self.start) is not int
            or type(self.end) is not int
            or not 0 <= self.start < self.end
            or self.end - self.start > 8192
            or type(self.primary) is not bool
            or self.source_role not in {"policy", "terms", "amendment", "unknown", "ambiguous"}
            or (self.document_kind == "policy") != (self.source_role == "policy")
        ):
            raise DocumentStructureError

    def to_provider_payload(self) -> Mapping[str, object]:
        return {
            **super().to_provider_payload(),
            "source_role": self.source_role,
            "primary": self.primary,
            "node_id": self.node_id,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True, repr=False)
class PolicyRangeEnvelope:
    envelope_id: str
    primary_chunk_ids: tuple[str, ...]
    primary_evidence_ids: tuple[UUID, ...]
    evidence: tuple[RangeEvidenceSlice, ...]

    def to_provider_payload(self) -> dict[str, object]:
        return {
            "envelope_id": self.envelope_id,
            "primary_ranges": [
                {"chunk_id": chunk_id, "evidence_id": str(evidence_id)}
                for chunk_id, evidence_id in zip(
                    self.primary_chunk_ids, self.primary_evidence_ids, strict=True
                )
            ],
            "evidence": [item.to_provider_payload() for item in self.evidence],
        }


@dataclass(frozen=True, repr=False)
class PolicyEnvelopePlan:
    envelopes: tuple[PolicyRangeEnvelope, ...]
    unprocessed: tuple[UnprocessedRange, ...]


def build_policy_envelopes(
    structure: DocumentStructure,
    plan: ChunkPlan,
    *,
    sensitive_terms: Sequence[str],
    maximum_envelopes: int = 256,
) -> PolicyEnvelopePlan:
    """Pack up to 32 primary ranges / 64 slices / 16384 characters per request.

    The local source and raw offsets remain unchanged. Repeated headers and
    footnotes are context, not extra enrollment rows. Unknown roles cannot grant
    policy evidence authority. This function plans inputs; it calls no provider.
    """
    if (
        type(maximum_envelopes) is not int
        or not 1 <= maximum_envelopes <= 4096
        or plan.lineage != structure.lineage
        or plan
        != plan_structure_chunks(
            structure,
            max_content_chars=plan.max_content_chars,
            max_context_chars=plan.max_context_chars,
            max_chunks=plan.max_chunks,
        )
    ):
        raise DocumentStructureError
    nodes = {node.node_id: node for node in structure.nodes}
    page_sources: dict[int, SourceWindowMinimizer | None] = {}
    offsets: dict[str, int] = {}
    roles = node_source_roles(structure)
    for page in structure.pages:
        texts: list[str] = []
        offset = 0
        for node_id in page.node_ids:
            node = nodes[node_id]
            offsets[node_id] = offset
            texts.append(node.text)
            offset += len(node.text) + 1
        try:
            page_sources[page.page_number] = SourceWindowMinimizer(
                "\n".join(texts), sensitive_terms=sensitive_terms
            )
        except EvidenceMinimizationError:
            page_sources[page.page_number] = None

    def evidence(node_id: str, start: int, end: int, *, primary: bool) -> RangeEvidenceSlice:
        node = nodes[node_id]
        redactor = page_sources[node.page_number]
        if redactor is None:
            raise EvidenceMinimizationError
        text = redactor.window(offsets[node_id] + start, offsets[node_id] + end)
        return RangeEvidenceSlice(
            evidence_id=uuid5(NAMESPACE_URL, f"{RANGE_ENVELOPE_REVISION}:{node_id}:{start}:{end}"),
            document_version_id=structure.lineage.document_version_id,
            page=node.page_number,
            text=text,
            bbox=node.bbox,
            document_kind="policy" if roles[node.node_id] == "policy" else "terms",
            node_id=node_id,
            start=start,
            end=end,
            primary=primary,
            source_role=roles[node.node_id],
        )

    envelopes: list[PolicyRangeEnvelope] = []
    unprocessed = list(plan.unprocessed)
    chunks: list[StructureChunk] = []
    primary_ids: list[UUID] = []
    selected: dict[UUID, RangeEvidenceSlice] = {}

    def flush() -> None:
        if not chunks:
            return
        chunk_ids = tuple(chunk.chunk_id for chunk in chunks)
        # Include minimized context in identity so a changed privacy policy cannot
        # reuse an envelope with a different provider projection.
        identity = json.dumps(
            [
                RANGE_ENVELOPE_REVISION,
                chunk_ids,
                [dict(item.to_provider_payload()) for item in selected.values()],
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        envelopes.append(
            PolicyRangeEnvelope(
                hashlib.sha256(identity).hexdigest(),
                chunk_ids,
                tuple(primary_ids),
                tuple(selected.values()),
            )
        )
        chunks.clear()
        primary_ids.clear()
        selected.clear()

    for chunk in plan.chunks:
        reason = None
        additions: dict[UUID, RangeEvidenceSlice] = {}
        primary_id: UUID | None = None
        try:
            primary = evidence(chunk.node_id, chunk.start, chunk.end, primary=True)
            primary_id = primary.evidence_id
            additions[primary.evidence_id] = primary
            for node_id in chunk.context_node_ids:
                node = nodes[node_id]
                context = evidence(node_id, 0, len(node.text), primary=False)
                additions.setdefault(context.evidence_id, context)
        except EvidenceMinimizationError:
            reason = "RANGE_MINIMIZATION_UNAVAILABLE"
        if reason is None:
            merged = dict(selected)
            for key, item in additions.items():
                merged[key] = replace(item, primary=item.primary or key in primary_ids)
            if chunks and (
                len(chunks) >= 32
                or len(merged) > 64
                or sum(len(item.text) for item in merged.values()) > 16384
            ):
                flush()
                merged = additions
            if len(envelopes) >= maximum_envelopes:
                reason = "ENVELOPE_BUDGET_EXHAUSTED"
            elif len(merged) > 64 or sum(len(item.text) for item in merged.values()) > 16384:
                reason = "ENVELOPE_CONTEXT_EXCEEDS_BUDGET"
            else:
                assert primary_id is not None
                selected = merged
                chunks.append(chunk)
                primary_ids.append(primary_id)
        if reason is not None:
            unprocessed.append(
                UnprocessedRange(
                    chunk.node_id,
                    chunk.page_number,
                    chunk.start,
                    chunk.end,
                    reason,
                )
            )
    flush()
    return PolicyEnvelopePlan(tuple(envelopes), tuple(unprocessed))

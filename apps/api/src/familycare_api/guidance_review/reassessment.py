"""Verify requested originals and materialize only real review publication rows.

No function here publishes global terms, mutates event/receipt facts, or persists
an answer. The finalizer owns publication insertion and the separate review result.
``source_digest`` always identifies the complete requested ReviewSources snapshot.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any
from uuid import UUID

import psycopg

from familycare_api.clauses.source_repository import read_verified_clause_source
from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.guidance.models import GuidanceSemanticEvidence
from familycare_api.guidance.semantic_binding import BoundSemanticRoot, bind_semantic_root
from familycare_api.guidance_review.sources import (
    ReviewNativePacket,
    ReviewSources,
    read_review_sources,
)
from familycare_api.terms_knowledge.core import parse_knowledge
from familycare_api.terms_knowledge.generated_contracts import SemanticWorkEnvelope
from familycare_api.terms_knowledge.repository import CurrentSemanticRoot, _plan
from familycare_api.terms_knowledge.source_verification import (
    VerifiedCompilation,
    verify_and_compile,
)

MAX_REVIEW_GRAPH_BYTES = 128 * 1024
MAX_REVIEW_COMPILED_BYTES = 1_000_000
MAX_REVIEW_PUBLICATIONS = 8
MAX_REVIEW_ROOTS = 32


class ReviewReassessmentInvalid(ValueError):
    def __init__(self, code: str = "REVIEW_REASSESSMENT_INVALID") -> None:
        if code not in {
            "REVIEW_REASSESSMENT_INVALID",
            "REVIEW_SOURCE_CHANGED",
            "REVIEW_GRAPH_UNVERIFIED",
            "REVIEW_PUBLICATION_INVALID",
            "REVIEW_OVERLAY_INVALID",
        }:
            code = "REVIEW_REASSESSMENT_INVALID"
        self.code = code
        super().__init__(code)


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


@dataclass(frozen=True, slots=True, repr=False)
class PreparedReviewPublication:
    packet_id: str
    source_digest: str
    coverage_ref: CanonicalCoverageRef
    graph_json: str
    proof_sha256: str
    compiled_json: str
    verifier_revision: str
    compiler_revision: str
    verified_root_ids: tuple[str, ...]
    unverified_root_ids: tuple[str, ...]

    def persistence_values(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "source_digest": self.source_digest,
            "graph_json": json.loads(self.graph_json),
            "proof_sha256": self.proof_sha256,
            "compiled_json": json.loads(self.compiled_json),
            "verifier_revision": self.verifier_revision,
            "compiler_revision": self.compiler_revision,
        }


def _supplied_graph(packet: ReviewNativePacket, graph: Mapping[str, Any]) -> dict[str, Any]:
    if len(_json(graph).encode()) > MAX_REVIEW_GRAPH_BYTES:
        raise ReviewReassessmentInvalid
    value = parse_knowledge(graph)
    envelope = SemanticWorkEnvelope.model_validate_json(packet.envelope_json)
    supplied = {
        region.region_id: {citation.citation_id: citation for citation in region.citations}
        for region in envelope.regions
    }
    citations = {key: citation for region in supplied.values() for key, citation in region.items()}
    nodes = {node.node_id: node for node in value.nodes}
    processing = value.processing
    if (
        value.sources != [envelope.source]
        or processing.expected_region_ids != envelope.expected_region_ids
        or not set(processing.consumed_region_ids) <= set(supplied)
        or set(processing.consumed_region_ids) & set(processing.unresolved_region_ids)
        or set(processing.consumed_region_ids) | set(processing.unresolved_region_ids)
        != set(envelope.expected_region_ids)
        or any(
            c.citation_id not in citations or c != citations[c.citation_id] for c in value.citations
        )
        or any(
            len(node.region_ids) != 1
            or node.region_ids[0] not in supplied
            or not set(node.citation_ids) <= set(supplied[node.region_ids[0]])
            for node in value.nodes
        )
        or any(
            root not in nodes or nodes[root].region_ids[0] not in envelope.primary_region_ids
            for root in value.roots
        )
    ):
        raise ReviewReassessmentInvalid
    return value.model_dump(mode="json")


def _prepare(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    sources: ReviewSources,
    packet: ReviewNativePacket,
    graph: Mapping[str, Any],
) -> tuple[PreparedReviewPublication, VerifiedCompilation]:
    admitted = tuple(
        entry
        for entry in sources.index
        if entry.native_ref == packet.coverage_ref
        and entry.enrollment_decision == "MATCH"
        and packet.packet_id in entry.packet_ids
    )
    if not admitted or packet.coverage_ref.kind != "OPERATIONAL_RIDER":
        raise ReviewReassessmentInvalid
    canonical = _supplied_graph(packet, graph)
    envelope = SemanticWorkEnvelope.model_validate_json(packet.envelope_json)
    edition = UUID(envelope.source.terms_edition_id or "")
    plan = _plan(connection, scope, edition)
    if plan.input_digest != envelope.input_digest or plan.snapshot.source != envelope.source:
        raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
    verified = verify_and_compile(canonical, sources={envelope.source.source_id: plan.snapshot})
    roots = verified.compilation.roots
    accepted = tuple(
        root.root_node_id
        for root in roots
        if root.executable
        and not root.diagnostics
        and root.root_node_id in verified.verified_node_ids
    )
    if not accepted or len(roots) > MAX_REVIEW_ROOTS:
        raise ReviewReassessmentInvalid("REVIEW_GRAPH_UNVERIFIED")
    compiled_json = _json(asdict(verified.compilation))
    if len(compiled_json.encode()) > MAX_REVIEW_COMPILED_BYTES:
        raise ReviewReassessmentInvalid("REVIEW_GRAPH_UNVERIFIED")
    prepared = PreparedReviewPublication(
        packet.packet_id,
        sources.digest_sha256,
        packet.coverage_ref,
        verified.canonical_graph_json,
        verified.proof_sha256,
        compiled_json,
        verified.verifier_revision,
        verified.compilation.compiler_revision,
        accepted,
        tuple(root.root_node_id for root in roots if root.root_node_id not in accepted),
    )
    return prepared, verified


def prepare_verified_review(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    packet: ReviewNativePacket | Mapping[str, Any],
    graph: Mapping[str, Any],
    *,
    expected_source_digest: str | None = None,
    decisions: DecisionRepository | None = None,
) -> PreparedReviewPublication:
    """Revalidate the exact current requested packet; return immutable insertion values."""
    try:
        repository = decisions or DecisionRepository(connection.info.dsn)
        sources = read_review_sources(connection, scope, event, repository)
        if expected_source_digest is not None and sources.digest_sha256 != expected_source_digest:
            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
        payload = packet.to_payload() if isinstance(packet, ReviewNativePacket) else dict(packet)
        selected = tuple(
            item for item in sources.packets if item.packet_id == payload.get("packet_id")
        )
        if len(selected) != 1 or _json(selected[0].to_payload()) != _json(payload):
            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
        return _prepare(connection, scope, sources, selected[0], graph)[0]
    except ReviewReassessmentInvalid:
        raise
    except psycopg.Error, ValueError, TypeError, KeyError, RecursionError:
        raise ReviewReassessmentInvalid from None


def _materialize(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    publication_id: UUID,
    review_job_id: UUID,
    sources: ReviewSources,
) -> tuple[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]]:
    row = connection.execute(
        "SELECT p.*,j.source_digest AS requested_source_digest,i.sources_json "
        "FROM guidance_review_publications p JOIN guidance_review_jobs j ON j.id=p.review_job_id "
        "JOIN guidance_review_inputs i ON i.review_job_id=j.id "
        "JOIN decision_runs r ON r.id=j.decision_run_id "
        "AND r.household_space_id=j.household_space_id "
        "AND r.medical_event_id=j.medical_event_id AND r.event_version=j.event_version "
        "WHERE p.id=%s AND p.review_job_id=%s AND j.household_space_id=%s "
        "AND j.medical_event_id=%s AND j.family_member_id=%s AND j.event_version=%s "
        "AND j.state IN ('running','partial','completed','disagreement') "
        "AND (j.state<>'running' OR (j.deadline_at>clock_timestamp() "
        "AND j.lease_expires_at>clock_timestamp())) "
        "AND i.privacy_digest=terms_semantic_privacy_digest(j.household_space_id)",
        (
            publication_id,
            review_job_id,
            scope.household_space_id,
            event.id,
            event.family_member_id,
            event.version,
        ),
    ).fetchone()
    if (
        row is None
        or row["source_digest"] != sources.digest_sha256
        or row["requested_source_digest"] != sources.digest_sha256
    ):
        raise ReviewReassessmentInvalid("REVIEW_PUBLICATION_INVALID")
    selected = tuple(packet for packet in sources.packets if packet.packet_id == row["packet_id"])
    if len(selected) != 1 or not any(
        _json(packet) == _json(selected[0].to_payload())
        for packet in row["sources_json"].get("packets", ())
    ):
        raise ReviewReassessmentInvalid("REVIEW_PUBLICATION_INVALID")
    packet = selected[0]
    prepared, verified = _prepare(connection, scope, sources, packet, row["graph_json"])
    for key, value in prepared.persistence_values().items():
        if _json(row[key]) != _json(value):
            raise ReviewReassessmentInvalid("REVIEW_PUBLICATION_INVALID")
    source = read_verified_clause_source(connection, scope.household_space_id, packet.clause_id)
    if source is None or source.input_digest != packet.source_digest_sha256:
        raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
    envelope = SemanticWorkEnvelope.model_validate_json(packet.envelope_json)
    bound = []
    for root in verified.compilation.roots:
        if root.root_node_id not in prepared.verified_root_ids:
            continue
        item = bind_semantic_root(
            CurrentSemanticRoot(publication_id, root),
            source.region,
            envelope.source.model_dump(mode="json"),
            review_job_id=review_job_id,
        )
        if item is None or not item.complete:
            raise ReviewReassessmentInvalid("REVIEW_GRAPH_UNVERIFIED")
        bound.append(item)
    return packet.coverage_ref, tuple(bound)


def read_review_overlay(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    publication_id: UUID,
    *,
    review_job_id: UUID,
    decisions: DecisionRepository | None = None,
) -> Mapping[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]]:
    """Only an existing publication belonging to this requested snapshot can bind."""
    try:
        repository = decisions or DecisionRepository(connection.info.dsn)
        sources = read_review_sources(connection, scope, event, repository)
        ref, roots = _materialize(connection, scope, event, publication_id, review_job_id, sources)
        return MappingProxyType({ref: roots})
    except ReviewReassessmentInvalid:
        raise
    except psycopg.Error, ValueError, TypeError, KeyError, RecursionError:
        raise ReviewReassessmentInvalid("REVIEW_PUBLICATION_INVALID") from None


def validate_review_overlay(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    overlay: Mapping[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]],
    *,
    decisions: DecisionRepository,
) -> Mapping[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]]:
    """Replay once per publication, rejecting forged or mutated bound input objects."""
    try:
        if len(overlay) > MAX_REVIEW_PUBLICATIONS:
            raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
        requested: dict[tuple[UUID, UUID], set[CanonicalCoverageRef]] = defaultdict(set)
        for ref, roots in overlay.items():
            if (
                ref.kind != "OPERATIONAL_RIDER"
                or not isinstance(roots, tuple)
                or not 1 <= len(roots) <= MAX_REVIEW_ROOTS
            ):
                raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
            for root in roots:
                inputs = (*root.rules, *((root.calculation,) if root.calculation else ()))
                identities = set()
                for item in inputs:
                    for citation in item.citations:
                        evidence = citation.evidence
                        if (
                            not isinstance(evidence, GuidanceSemanticEvidence)
                            or evidence.review_job_id is None
                            or evidence.publication_id != item.publication_id
                        ):
                            raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
                        identities.add((evidence.publication_id, evidence.review_job_id))
                if len(identities) != 1:
                    raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
                requested[next(iter(identities))].add(ref)
        if len(requested) > MAX_REVIEW_PUBLICATIONS or len({job for _, job in requested}) != 1:
            raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
        sources = read_review_sources(connection, scope, event, decisions)
        actual: dict[CanonicalCoverageRef, list[BoundSemanticRoot]] = defaultdict(list)
        for (publication, job), refs in sorted(requested.items()):
            ref, roots = _materialize(connection, scope, event, publication, job, sources)
            if refs != {ref}:
                raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
            actual[ref].extend(roots)
        for ref, roots in overlay.items():
            if len(roots) != len(actual[ref]) or any(root not in actual[ref] for root in roots):
                raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID")
        return MappingProxyType({ref: tuple(roots) for ref, roots in actual.items()})
    except ReviewReassessmentInvalid:
        raise
    except psycopg.Error, ValueError, TypeError, KeyError, RecursionError, AttributeError:
        raise ReviewReassessmentInvalid("REVIEW_OVERLAY_INVALID") from None

"""Revalidate review proposals locally and retain a separate, immutable result."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.repository import DecisionRepository, _medical_event
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.models import GuidanceCandidate, LocalGuidanceResponse
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.repository import combine_guidance_contexts, read_operational_guidance
from familycare_api.guidance.semantic_binding import BoundSemanticRoot
from familycare_api.guidance_review.models import (
    GuidanceReviewCoverageScope,
    GuidanceReviewDifference,
    GuidanceReviewFinding,
    GuidanceReviewResult,
    GuidanceReviewScope,
    GuidanceReviewSourceCitation,
)
from familycare_api.guidance_review.reassessment import (
    ReviewReassessmentInvalid,
    _supplied_graph,
    prepare_verified_review,
    read_review_overlay,
)
from familycare_api.guidance_review.sources import (
    ReviewNativePacket,
    ReviewSources,
    read_review_sources,
)
from familycare_api.terms_knowledge.core import SemanticKnowledgeError
from familycare_api.terms_knowledge.source_meaning import observe_statement


def _codes(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code for group in groups for code in group))[:64]


def _proposal(value: object, sources: ReviewSources) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_revision") != "guidance-review-proposals-v1"
    ):
        raise ValueError("REVIEW_PROPOSAL_INVALID")
    known = {packet.packet_id for packet in sources.packets}
    partitions = []
    for key in ("reviewed_packet_ids", "unreviewed_packet_ids", "omitted_packet_ids"):
        part = value.get(key)
        if (
            not isinstance(part, list)
            or len(part) > 8
            or any(not isinstance(item, str) for item in part)
            or len(set(part)) != len(part)
        ):
            raise ValueError("REVIEW_PROPOSAL_INVALID")
        partitions.append(set(part))
    if set.union(*partitions) != known or sum(map(len, partitions)) != len(known):
        raise ValueError("REVIEW_PROPOSAL_INVALID")
    suggestions = value.get("suggestions")
    if not isinstance(suggestions, list) or len(suggestions) > 8:
        raise ValueError("REVIEW_PROPOSAL_INVALID")
    if (
        any(not isinstance(s, dict) or s.get("packet_id") not in known for s in suggestions)
        or {s["packet_id"] for s in suggestions} != partitions[0]
        or len(suggestions) != len(partitions[0])
    ):
        raise ValueError("REVIEW_PROPOSAL_INVALID")
    for suggestion in suggestions:
        if suggestion.get("kind") not in {
            "AGREEMENT",
            "CORRECTION",
            "ADDITIONAL_CANDIDATE",
            "EXCEPTION",
            "CONFLICT",
        }:
            raise ValueError("REVIEW_PROPOSAL_INVALID")
    return value


def _citations(
    packet: ReviewNativePacket, graph: Mapping[str, Any]
) -> tuple[GuidanceReviewSourceCitation, ...]:
    canonical = _supplied_graph(packet, graph)
    source = canonical["sources"][0]
    return tuple(
        GuidanceReviewSourceCitation(
            packet_id=packet.packet_id,
            citation_id=c["citation_id"],
            document_version_id=source["document_version_id"],
            terms_edition_id=source["terms_edition_id"],
            source_node_id=c["node_id"],
            page_start=c["page_number"],
            page_end=c["page_number"],
            start=c["start"],
            end=c["end"],
            source_layer=c["source_layer"],
            bbox=c["bbox"],
            source_sha256=source["content_sha256"],
            quote=c["text"],
        )
        for c in canonical["citations"][:64]
    )


def _contradicts(graph: Mapping[str, Any]) -> bool:
    return any(
        meaning is not None and meaning != node["payload"]
        for node in graph["nodes"]
        if (meaning := observe_statement(node["statement"])) is not None
    )


def _behavior(candidate: GuidanceCandidate) -> object:
    def estimate(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value.get(key)
            for key in (
                "kind",
                "amount",
                "lower",
                "upper",
                "currency",
                "formula",
                "missing_inputs",
                "assumptions",
                "partial_amount",
                "basis",
            )
        }

    value = candidate.model_dump(mode="json")
    return {
        "group": value["group"],
        "condition_result": value["condition_result"],
        "freshness": value["freshness"],
        "assumptions": value["assumptions"],
        "estimate": estimate(value["estimate"]),
        "conditions": [
            {key: c.get(key) for key in ("result", "required", "reason_code", "field_paths")}
            for c in value["conditions"]
        ],
        "scenarios": [
            {"hypotheses": s["hypotheses"], "estimate": estimate(s["estimate"])}
            for s in value["scenarios"]
        ],
        "cases": [
            {"condition_result": c["condition_result"], "estimate": estimate(c["estimate"])}
            for c in value["cases"]
        ],
    }


def _differences(
    original: LocalGuidanceResponse, reviewed: LocalGuidanceResponse
) -> tuple[GuidanceReviewDifference, ...]:
    before, after = ({c.ref: c for c in value.candidates} for value in (original, reviewed))
    result = []
    for ref in sorted(before.keys() | after.keys(), key=lambda value: str(value.coverage_id)):
        left, right = before.get(ref), after.get(ref)
        if left is not None and right is not None and _behavior(left) == _behavior(right):
            continue
        result.append(
            GuidanceReviewDifference(
                coverage=ref,
                change="ADDED" if left is None else "REMOVED" if right is None else "CHANGED",
                before=left,
                after=right,
            )
        )
    return tuple(result)


class GuidanceReviewProjector:
    def __init__(self, database_url: str) -> None:
        self.decisions = DecisionRepository(database_url)
        self.database_url = self.decisions.database_url

    def project_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] = lambda: False
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("REVIEW_PROJECTION_LIMIT_INVALID")
        count = 0
        for _ in range(limit):
            if stop_requested():
                break
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                connection.execute("SET LOCAL statement_timeout='60s'")
                candidate = connection.execute(
                    "SELECT j.id,j.household_space_id,j.medical_event_id FROM "
                    "guidance_review_jobs j "
                    "JOIN guidance_review_proposals p ON p.review_job_id=j.id WHERE "
                    "j.state='running' "
                    "ORDER BY p.received_at,j.id LIMIT 1",
                ).fetchone()
                if candidate is None:
                    break
                scope = HouseholdScope(candidate["household_space_id"])
                event_row = self.decisions._event_row(
                    connection, scope, candidate["medical_event_id"], for_update=True
                )
                job = connection.execute(
                    "SELECT j.*,p.proposal_json,r.local_guidance_json,"
                    "c.scope_digest=guidance_review_scope_digest(j.id) AS context_current "
                    "FROM guidance_review_jobs j JOIN guidance_review_proposals p ON "
                    "p.review_job_id=j.id "
                    "JOIN decision_runs r ON r.id=j.decision_run_id "
                    "LEFT JOIN guidance_review_contexts c ON c.review_job_id=j.id "
                    "WHERE j.id=%s AND j.state='running' FOR UPDATE OF j SKIP LOCKED",
                    (candidate["id"],),
                ).fetchone()
                if job is None:
                    continue
                try:
                    with connection.transaction():
                        if event_row is None or job["context_current"] is not True:
                            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
                        event = _medical_event(event_row)
                        original = LocalGuidanceResponse.model_validate(job["local_guidance_json"])
                        if (
                            self.decisions._local_guidance_is_stale(
                                connection, scope, event, original
                            )
                            is not False
                        ):
                            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
                        sources = read_review_sources(connection, scope, event, self.decisions)
                        if sources.digest_sha256 != job["source_digest"]:
                            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
                        proposal = _proposal(job["proposal_json"], sources)
                        result, state = self._evaluate(
                            connection, scope, event, job, original, sources, proposal
                        )
                        if (
                            self.decisions._local_guidance_is_stale(
                                connection, scope, event, original
                            )
                            is not False
                        ):
                            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
                        connection.execute(
                            "INSERT INTO "
                            "guidance_review_results(review_job_id,result_json) VALUES(%s,%s)",
                            (job["id"], Jsonb(result.model_dump(mode="json"))),
                        )
                        changed = connection.execute(
                            "UPDATE guidance_review_jobs SET "
                            "state=%s,completed_at=clock_timestamp(),"
                            "lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND "
                            "state='running' "
                            "AND deadline_at>clock_timestamp() AND EXISTS(SELECT 1 FROM "
                            "guidance_review_contexts c WHERE "
                            "c.review_job_id=guidance_review_jobs.id "
                            "AND "
                            "c.scope_digest=guidance_review_scope_digest(c.review_job_id)) "
                            "RETURNING id",
                            (state, job["id"]),
                        ).fetchone()
                        if changed is None:
                            raise ReviewReassessmentInvalid("REVIEW_SOURCE_CHANGED")
                except ReviewReassessmentInvalid:
                    self._fail(connection, job["id"], "REVIEW_INPUT_CHANGED")
                except (
                    ValueError,
                    ArithmeticError,
                    SemanticKnowledgeError,
                    psycopg.errors.CheckViolation,
                ):
                    self._fail(connection, job["id"], "REVIEW_INVALID_RESPONSE")
                count += 1
        return count

    @staticmethod
    def _fail(connection: psycopg.Connection[dict[str, Any]], job_id: UUID, code: str) -> None:
        connection.execute(
            "UPDATE guidance_review_jobs SET state='failed',error_code=CASE WHEN "
            "deadline_at<=clock_timestamp() "
            "THEN 'REVIEW_DEADLINE_EXCEEDED' ELSE %s END,completed_at=clock_timestamp(),"
            "lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND state='running'",
            (code, job_id),
        )

    def _evaluate(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        job: dict[str, Any],
        original: LocalGuidanceResponse,
        sources: ReviewSources,
        proposal: dict[str, Any],
    ) -> tuple[GuidanceReviewResult, str]:
        packets = {packet.packet_id: packet for packet in sources.packets}
        overlays: dict[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]] = defaultdict(tuple)
        findings = []
        address_verified: set[str] = set()
        semantic_incomplete = False
        for suggestion in proposal["suggestions"]:
            packet = packets[suggestion["packet_id"]]
            graph = suggestion.get("graph")
            evidence: tuple[GuidanceReviewSourceCitation, ...] = ()
            publication_id = None
            status: Literal["APPLIED", "AGREEMENT", "OPINION", "REJECTED"] = "REJECTED"
            reasons: tuple[str, ...] = ("REVIEW_INVALID_CITATION",)
            try:
                evidence = _citations(packet, graph)
                address_verified.add(packet.packet_id)
                if _contradicts(graph):
                    reasons = ("REVIEW_SOURCE_CONTRADICTION",)
                else:
                    prepared = prepare_verified_review(
                        connection,
                        scope,
                        event,
                        packet,
                        graph,
                        expected_source_digest=sources.digest_sha256,
                        decisions=self.decisions,
                    )
                    values = prepared.persistence_values()
                    row = connection.execute(
                        "INSERT INTO "
                        "guidance_review_publications(review_job_id,packet_id,source_digest,"
                        "graph_json,proof_sha256,compiled_json,"
                        "verifier_revision,compiler_revision) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                        (
                            job["id"],
                            prepared.packet_id,
                            prepared.source_digest,
                            Jsonb(values["graph_json"]),
                            prepared.proof_sha256,
                            Jsonb(values["compiled_json"]),
                            prepared.verifier_revision,
                            prepared.compiler_revision,
                        ),
                    ).fetchone()
                    assert row is not None
                    publication_id = row["id"]
                    overlay = read_review_overlay(
                        connection,
                        scope,
                        event,
                        publication_id,
                        review_job_id=job["id"],
                        decisions=self.decisions,
                    )
                    for ref, roots in overlay.items():
                        overlays[ref] += roots
                    status = "AGREEMENT" if suggestion["kind"] == "AGREEMENT" else "APPLIED"
                    reasons = ("REVIEW_SOURCE_VERIFIED",)
                    if (
                        prepared.unverified_root_ids
                        or json.loads(prepared.compiled_json).get("processing_complete") is not True
                    ):
                        semantic_incomplete = True
                        reasons += ("REVIEW_PARTIAL_INTERPRETATION",)
            except (
                ReviewReassessmentInvalid,
                SemanticKnowledgeError,
                ValidationError,
                ValueError,
                KeyError,
                TypeError,
            ):
                if evidence:
                    status, reasons = "OPINION", ("REVIEW_INTERPRETATION_UNVERIFIED",)
                    semantic_incomplete = True
            if suggestion.get("proposed_amount") is not None:
                reasons += ("REVIEW_ADVISORY_AMOUNT_IGNORED",)
            findings.append(
                GuidanceReviewFinding(
                    kind=suggestion["kind"],
                    coverage=packet.coverage_ref,
                    status=status,
                    reason_codes=reasons,
                    evidence=evidence,
                    affected_fact_paths=tuple(suggestion.get("affected_fact_paths", ())),
                    publication_id=publication_id,
                )
            )
        reviewed = original
        if overlays:
            private = self.decisions.knowledge_repository.read_context(connection, scope, event)
            context = combine_guidance_contexts(
                read_operational_guidance(
                    connection, scope, event, self.decisions, semantic_overlay=overlays
                ),
                adapt_private_guidance(private.context) if private.context is not None else None,
            )
            reviewed = LocalGuidanceEngine().evaluate(scope, event, context)
        differences = _differences(original, reviewed)
        disagreement = any(
            f.status in ("REJECTED", "OPINION") or f.kind == "CONFLICT" for f in findings
        )
        disagreement = (
            disagreement or "REVIEW_SEMANTIC_DISAGREEMENT" in reviewed.support.failure_codes
        )
        local_incomplete = proposal.get("local_comparison_complete") is not True
        incomplete = (
            semantic_incomplete
            or local_incomplete
            or bool(proposal.get("omitted_coverage_aliases"))
        )
        omitted = len(proposal["omitted_packet_ids"])
        complete = (
            sources.manifest.complete and not incomplete and len(address_verified) == len(packets)
        )
        reasons = _codes(
            sources.manifest.reason_codes,
            () if complete else ("REVIEW_PARTIAL_SCOPE",),
            () if not local_incomplete else ("REVIEW_LOCAL_COMPARISON_PARTIAL",),
            () if not semantic_incomplete else ("REVIEW_PARTIAL_INTERPRETATION",),
        )
        result = GuidanceReviewResult(
            source_digest=sources.digest_sha256,
            scope=GuidanceReviewScope(
                total_coverages=sources.manifest.total_coverage_count,
                indexed_coverages=len(sources.index),
                total_packets=len(packets),
                reviewed_packets=len(address_verified),
                omitted_packets=omitted,
                expected_regions=sum(r.expected_region_count for r in sources.manifest.regions),
                supplied_regions=sum(
                    r.expected_region_count - r.omitted_region_count
                    for r in sources.manifest.regions
                ),
                unsupplied_regions=sum(r.omitted_region_count for r in sources.manifest.regions),
                unreviewed_packets=len(packets) - len(address_verified) - omitted,
                complete=complete,
                reason_codes=reasons,
                coverages=tuple(
                    GuidanceReviewCoverageScope(
                        ref=entry.ref,
                        contract_label=entry.contract_label,
                        coverage_label=entry.coverage_label,
                        source_state=entry.source_state,
                        reviewed_packets=len(set(entry.packet_ids) & address_verified),
                        total_packets=len(entry.packet_ids),
                        reason_codes=entry.reason_codes,
                    )
                    for entry in sources.index
                ),
            ),
            findings=tuple(findings),
            differences=differences,
            guidance=reviewed,
            reason_codes=_codes(("REVIEW_NOT_PAYMENT_CONFIRMATION",), reasons),
        )
        return result, "disagreement" if disagreement else "completed" if complete else "partial"

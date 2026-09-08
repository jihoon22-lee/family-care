"""Replay the enrolled Rider's approved link and event-specific original semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any
from uuid import UUID

import psycopg

from familycare_api.clauses.errors import RiderClauseLinkInvalid, TermsEditionNotFound
from familycare_api.clauses.links import validate_rider_clause_link
from familycare_api.clauses.repository import RiderClauseLinkRepository
from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.clauses.source_repository import read_verified_clause_source
from familycare_api.clauses.terms_change_repository import read_event_terms
from familycare_api.clauses.terms_change_selection import TermsSelectionScope
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.guidance.semantic_binding import BoundSemanticRoot, bind_semantic_root
from familycare_api.terms_knowledge.core import SemanticKnowledgeError
from familycare_api.terms_knowledge.repository import SemanticRootView, read_semantic_root_page


@dataclass(frozen=True, slots=True, repr=False)
class SemanticGuidanceRead:
    roots: tuple[BoundSemanticRoot, ...]
    versions: tuple[dict[str, Any], ...]


def prefer_bound_root(
    previous: BoundSemanticRoot, candidate: BoundSemanticRoot
) -> BoundSemanticRoot:
    if previous.original_anchor != candidate.original_anchor:
        raise ValueError("SEMANTIC_ORIGINAL_IDENTITY_MISMATCH")

    def quality(root: BoundSemanticRoot) -> tuple[bool, bool, int]:
        return root.complete, root.calculation is not None, len(root.rules)

    return candidate if quality(candidate) > quality(previous) else previous


class SemanticGuidanceReader:
    def __init__(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        database_url: str,
    ) -> None:
        self.connection, self.scope, self.event = connection, scope, event
        self.links = RiderClauseLinkRepository(database_url)
        self.cache: dict[
            tuple[UUID, tuple[ClauseSourceSpan, ...]], tuple[SemanticRootView, ...]
        ] = {}

    def _roots(
        self, edition: UUID, spans: tuple[ClauseSourceSpan, ...]
    ) -> tuple[SemanticRootView, ...]:
        key = (edition, spans)
        if key not in self.cache:
            items: list[SemanticRootView] = []
            after = None
            for _ in range(32):
                page = read_semantic_root_page(
                    self.connection, self.scope, edition, after=after, source_spans=spans
                )
                items.extend(page)
                if len(page) < 32:
                    break
                after = page[-1].root_node_id
            else:
                if read_semantic_root_page(
                    self.connection, self.scope, edition, after=after, source_spans=spans, limit=1
                ):
                    raise SemanticKnowledgeError("SEMANTIC_CLAUSE_ROOT_LIMIT_EXCEEDED")
            self.cache[key] = tuple(items)
        return self.cache[key]

    def for_rider(self, policy: UUID, rider: UUID) -> SemanticGuidanceRead:
        connection, scope, event = self.connection, self.scope, self.event
        rows = connection.execute(
            "SELECT l.* FROM rider_clause_links l JOIN riders r ON r.id=l.rider_id "
            "AND r.household_space_id=l.household_space_id JOIN policy_contracts p "
            "ON p.id=r.policy_contract_id AND p.household_space_id=r.household_space_id "
            "JOIN clauses c ON c.id=l.clause_id AND c.terms_edition_id=l.terms_edition_id "
            "AND c.household_space_id=l.household_space_id "
            "WHERE l.household_space_id=%s AND r.id=%s AND p.id=%s "
            "AND l.deleted_at IS NULL AND r.deleted_at IS NULL AND p.deleted_at IS NULL "
            "AND c.deleted_at IS NULL AND l.review_state IN ('AI_VERIFIED','USER_CONFIRMED') "
            "ORDER BY l.id LIMIT 129",
            (scope.household_space_id, rider, policy),
        ).fetchall()
        if len(rows) > 128:
            return SemanticGuidanceRead((), ({"failure": "SEMANTIC_LINK_LIMIT_EXCEEDED"},))
        roots: dict[tuple[object, ...], BoundSemanticRoot] = {}
        versions = []
        for row in rows:
            state: dict[str, Any] = {"link": str(row["id"]), "version": row["version"]}
            versions.append(state)
            selection = read_event_terms(
                connection,
                TermsSelectionScope(
                    scope.household_space_id,
                    policy,
                    event.family_member_id,
                    rider,
                    row["clause_id"],
                ),
                event.event_date,
            )
            state["selection"] = asdict(selection)
            selected = next(
                (e for e in selection.editions if e.edition_id == row["terms_edition_id"]), None
            )
            if selected is None or selected.status != "MATCH":
                state["status"] = "EVENT_TERMS_UNRESOLVED"
                continue
            try:
                context = self.links._validation_context(
                    connection, scope, row, include_change_gate=False, lock_source=False
                )
                # This event-specific result replaces only the contract-date applicability gate.
                validate_rider_clause_link(
                    scope,
                    replace(
                        context,
                        program_applicability_verified=True,
                        program_applicability_blocked=False,
                    ),
                )
                source = read_verified_clause_source(
                    connection, scope.household_space_id, row["clause_id"]
                )
                if source is None:
                    state["status"] = "CLAUSE_SOURCE_UNRESOLVED"
                    continue
                state["source_digest"] = source.input_digest
                state["source_assessment"] = str(source.assessment_id)
                page = self._roots(
                    row["terms_edition_id"], (*source.region.body, *source.region.table_context)
                )
            except SemanticKnowledgeError as error:
                state["status"] = error.code
                continue
            except RiderClauseLinkInvalid, TermsEditionNotFound:
                state["status"] = "SEMANTIC_SOURCE_UNAVAILABLE"
                continue
            expected = {
                "document_version_id": source.document_version_id,
                "terms_edition_id": source.terms_edition_id,
                "generation_id": source.generation_id,
                "content_sha256": source.region.content_sha256,
            }
            state["roots"] = []
            for view in page:
                chosen = view.last_usable or view.latest
                if chosen is None:
                    continue
                bound = bind_semantic_root(chosen, source.region, expected)
                if bound is None:
                    continue
                state["roots"].append(
                    {
                        "used": str(chosen.publication_id),
                        "latest": str(view.latest.publication_id) if view.latest else None,
                        "manifest": bound.manifest_sha256,
                    }
                )
                previous = roots.get(bound.original_anchor)
                roots[bound.original_anchor] = (
                    bound if previous is None else prefer_bound_root(previous, bound)
                )
        return SemanticGuidanceRead(tuple(roots.values()), tuple(versions))

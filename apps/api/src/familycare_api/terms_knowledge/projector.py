"""Resume deterministic source processing outside HTTP and preserve every attempt.

Local recognition needs no provider reservation. A completion records full-manifest
coverage separately from each bounded graph; it never grants execution authority.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from familycare_api.common.scope import HouseholdScope
from familycare_api.terms_knowledge.core import COMPILER_REVISION
from familycare_api.terms_knowledge.repository import (
    SemanticSourceChanged,
    SemanticSourcePlan,
    TermsSemanticRepository,
    _lock_source,
)
from familycare_api.terms_knowledge.source_verification import VERIFIER_REVISION, verify_and_compile
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

LOGGER = logging.getLogger(__name__)
RETRY_SECONDS = 60


@dataclass(frozen=True, slots=True)
class SemanticProcessingStatus:
    run_id: UUID
    outcome: str
    expected_regions: tuple[str, ...]
    consumed_regions: tuple[str, ...]
    unresolved_regions: tuple[str, ...]
    planned_graphs: int
    published_graphs: int
    reason_codes: tuple[str, ...] = ()


def _revision() -> str:
    from familycare_api.terms_knowledge.local_candidates import LOCAL_PLANNER_REVISION
    from familycare_api.terms_knowledge.source_meaning import MEANING_REVISION

    return f"{LOCAL_PLANNER_REVISION}/{VERIFIER_REVISION}/{COMPILER_REVISION}/{MEANING_REVISION}"


def _proposal(plan: SemanticSourcePlan) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    from familycare_api.terms_knowledge.local_candidates import propose_local_candidates

    proposal = propose_local_candidates(plan.snapshot)
    expected = plan.snapshot.layout.expected_region_ids
    consumed = proposal.consumed_region_ids
    unresolved = proposal.unresolved_region_ids
    if (
        len(set(expected)) != len(expected)
        or not set(consumed) <= set(expected)
        or not set(unresolved) <= set(expected)
        or set(consumed) & set(unresolved)
        or set(consumed) | set(unresolved) != set(expected)
    ):
        raise SemanticSourceChanged
    manifest = {
        "expected_regions": list(expected),
        "consumed_regions": list(consumed),
        "unresolved_regions": list(unresolved),
        "source_complete": plan.snapshot.layout.complete,
    }
    return manifest, proposal.graphs


def _assess(
    plan: SemanticSourcePlan, manifest: dict[str, Any], graphs: tuple[dict[str, Any], ...]
) -> str:
    usable = 0
    has_diagnostics = False
    for graph in graphs:
        roots = verify_and_compile(graph, sources={"terms": plan.snapshot}).compilation.roots
        usable += sum(root.executable and not root.diagnostics for root in roots)
        has_diagnostics |= any(root.diagnostics for root in roots)
    if (
        not has_diagnostics
        and manifest["source_complete"]
        and not manifest["unresolved_regions"]
        and set(manifest["expected_regions"]) == set(manifest["consumed_regions"])
    ):
        return "COMPLETE"
    return "PARTIAL" if usable else "UNRESOLVED"


def _hashes(
    connection: psycopg.Connection[dict[str, Any]], graphs: tuple[dict[str, Any], ...]
) -> list[str]:
    return [
        connection.execute(
            "SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS digest",
            (Jsonb(graph),),
        ).fetchone()["digest"]  # type: ignore[index]
        for graph in graphs
    ]


def _receipts_valid(
    connection: psycopg.Connection[dict[str, Any]],
    run_id: UUID,
    plan: SemanticSourcePlan,
    graphs: tuple[dict[str, Any], ...],
    *,
    require_all: bool,
) -> bool:
    receipts = connection.execute(
        "SELECT graph_ordinal,publication_id FROM terms_semantic_processing_outputs "
        "WHERE run_id=%s",
        (run_id,),
    ).fetchall()
    if require_all and len(receipts) != len(graphs):
        return False
    for receipt in receipts:
        ordinal = receipt["graph_ordinal"]
        row = connection.execute(
            "SELECT p.*,c.graph_json,"
            "(SELECT coalesce(jsonb_agg(r.root_json ORDER BY r.root_node_id),'[]'::jsonb) "
            "FROM terms_semantic_root_publications r WHERE r.publication_id=p.id) AS stored_roots "
            "FROM terms_semantic_publications p "
            "JOIN terms_semantic_candidates c ON c.id=p.candidate_id WHERE p.id=%s",
            (receipt["publication_id"],),
        ).fetchone()
        if (
            row is None
            or not 0 <= ordinal < len(graphs)
            or row["graph_json"] != graphs[ordinal]
            or row["verifier_revision"] != VERIFIER_REVISION
            or row["compiler_revision"] != COMPILER_REVISION
        ):
            return False
        verified = verify_and_compile(graphs[ordinal], sources={"terms": plan.snapshot})
        expected = json.loads(json.dumps(asdict(verified.compilation)))
        if (
            row["proof_sha256"] != verified.proof_sha256
            or row["result_json"] != expected
            or row["stored_roots"] != sorted(expected["roots"], key=lambda r: r["root_node_id"])
        ):
            return False
    return True


class TermsSemanticProjector:
    def __init__(self, database_url: str) -> None:
        self.repository = TermsSemanticRepository(database_url)
        self.database_url = self.repository.database_url

    def project_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] = lambda: False
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("TERMS_SEMANTIC_PROCESSING_LIMIT_INVALID")
        if stop_requested():
            return 0
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT e.id,e.household_space_id,input.context FROM terms_editions e "
                "CROSS JOIN LATERAL (SELECT terms_semantic_input_context("
                "e.id,e.household_space_id) "
                "AS context) input WHERE input.context IS NOT NULL AND NOT EXISTS("
                "SELECT 1 FROM terms_semantic_processing_runs r "
                "JOIN terms_semantic_processing_completions c ON c.run_id=r.id "
                "WHERE r.terms_edition_id=e.id AND r.household_space_id=e.household_space_id "
                "AND r.input_context=input.context AND r.planner_revision=%s) "
                "AND NOT EXISTS(SELECT 1 FROM terms_semantic_processing_failures f "
                "WHERE f.terms_edition_id=e.id AND f.household_space_id=e.household_space_id "
                "AND f.input_context=input.context AND f.planner_revision=%s "
                "AND f.retry_after>clock_timestamp()) "
                "ORDER BY e.id LIMIT %s",
                (_revision(), _revision(), limit),
            ).fetchall()
        completed = 0
        for row in rows:
            if stop_requested():
                break
            try:
                completed += self._project(
                    HouseholdScope(row["household_space_id"]), row["id"], stop_requested
                )
            except Exception:
                LOGGER.warning("terms_semantic_projection_unavailable")
                # An unavailable/cancelled source may not accept a failure row.
                # Never log its SQL, identifiers, source text or connection URL.
                with suppress(Exception):
                    self._record_failure(
                        HouseholdScope(row["household_space_id"]), row["id"], row["context"]
                    )
        return completed

    def _record_failure(
        self, scope: HouseholdScope, edition: UUID, expected_context: dict[str, Any]
    ) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_source(connection, scope, edition)
            connection.execute(
                "INSERT INTO terms_semantic_processing_failures(household_space_id,"
                "terms_edition_id,input_context,input_digest,planner_revision,retry_after) "
                "SELECT %s,%s,context,encode(sha256(convert_to(context::text,'UTF8')),'hex'),"
                "%s,clock_timestamp()+%s*interval '1 second' "
                "FROM (SELECT terms_semantic_input_context(%s,%s) AS context) input "
                "WHERE context IS NOT NULL AND context=%s",
                (
                    scope.household_space_id,
                    edition,
                    _revision(),
                    RETRY_SECONDS,
                    edition,
                    scope.household_space_id,
                    Jsonb(expected_context),
                ),
            )

    def _project(
        self, scope: HouseholdScope, edition: UUID, stop_requested: Callable[[], bool]
    ) -> bool:
        # This session lock serializes only local planners for an edition. It has
        # a different key from publication transactions and is released on close.
        with psycopg.connect(
            self.database_url, row_factory=dict_row, autocommit=True
        ) as connection:
            lock = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired",
                (f"{edition}:semantic-processing",),
            ).fetchone()
            if lock is None or not lock["acquired"]:
                return False
            plan = self.repository.source_plan(scope, edition)
            manifest, graphs = _proposal(plan)
            with connection.transaction():
                _lock_source(connection, scope, edition)
                hashes = _hashes(connection, graphs)
                connection.execute(
                    "INSERT INTO terms_semantic_processing_runs("
                    "household_space_id,terms_edition_id,"
                    "input_context,input_digest,planner_revision,manifest_json,graph_hashes) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        scope.household_space_id,
                        edition,
                        Jsonb(plan.input_context),
                        plan.input_digest,
                        _revision(),
                        Jsonb(manifest),
                        hashes,
                    ),
                )
                run = connection.execute(
                    "SELECT r.*,EXISTS(SELECT 1 FROM terms_semantic_processing_completions c "
                    "WHERE c.run_id=r.id) AS finished FROM terms_semantic_processing_runs r "
                    "WHERE terms_edition_id=%s AND input_digest=%s AND planner_revision=%s",
                    (edition, plan.input_digest, _revision()),
                ).fetchone()
                assert run is not None
                if run["manifest_json"] != manifest or run["graph_hashes"] != hashes:
                    raise SemanticSourceChanged
                if run["finished"]:
                    return False
            done = {
                row["graph_ordinal"]
                for row in connection.execute(
                    "SELECT graph_ordinal FROM terms_semantic_processing_outputs WHERE run_id=%s",
                    (run["id"],),
                ).fetchall()
            }
            for ordinal, graph in enumerate(graphs):
                if stop_requested():
                    return False
                if ordinal in done:
                    continue
                publication = self.repository.publish_candidate(
                    scope, edition, graph, expected_input_digest=plan.input_digest
                )
                with connection.transaction():
                    _lock_source(connection, scope, edition)
                    connection.execute(
                        "INSERT INTO terms_semantic_processing_outputs("
                        "run_id,graph_ordinal,publication_id) "
                        "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (run["id"], ordinal, publication.publication_id),
                    )
            if stop_requested():
                return False
            with connection.transaction():
                _lock_source(connection, scope, edition)
                outcome = (
                    _assess(plan, manifest, graphs)
                    if _receipts_valid(connection, run["id"], plan, graphs, require_all=True)
                    else "UNRESOLVED"
                )
                connection.execute(
                    "INSERT INTO terms_semantic_processing_completions(run_id,outcome) "
                    "VALUES(%s,%s)",
                    (run["id"], outcome),
                )
            return True

    def current_status(
        self, scope: HouseholdScope, edition: UUID
    ) -> SemanticProcessingStatus | None:
        plan = self.repository.source_plan(scope, edition)
        manifest, graphs = _proposal(plan)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_source(connection, scope, edition)
            row = connection.execute(
                "SELECT r.*,c.run_id IS NOT NULL AS finished,"
                "(SELECT count(*) FROM terms_semantic_processing_outputs o "
                "WHERE o.run_id=r.id) AS published "
                "FROM terms_semantic_processing_runs r "
                "LEFT JOIN terms_semantic_processing_completions c "
                "ON c.run_id=r.id WHERE r.household_space_id=%s AND r.terms_edition_id=%s "
                "AND r.input_context=terms_semantic_input_context(%s,%s) "
                "AND r.input_digest=%s AND r.planner_revision=%s",
                (
                    scope.household_space_id,
                    edition,
                    edition,
                    scope.household_space_id,
                    plan.input_digest,
                    _revision(),
                ),
            ).fetchone()
            if (
                row is None
                or row["manifest_json"] != manifest
                or row["graph_hashes"] != _hashes(connection, graphs)
            ):
                return None
            outcome = _assess(plan, manifest, graphs) if row["finished"] else "IN_PROGRESS"
            valid = _receipts_valid(
                connection, row["id"], plan, graphs, require_all=row["finished"]
            )
            if not valid:
                outcome = "UNRESOLVED"
            return SemanticProcessingStatus(
                row["id"],
                outcome,
                tuple(manifest["expected_regions"]),
                tuple(manifest["consumed_regions"]),
                tuple(manifest["unresolved_regions"]),
                len(graphs),
                row["published"],
                () if valid else ("SEMANTIC_PROCESSING_OUTPUT_UNVERIFIED",),
            )

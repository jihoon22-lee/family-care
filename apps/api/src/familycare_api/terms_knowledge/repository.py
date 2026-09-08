"""Store immutable semantic attempts; replay current sources before local use."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from familycare_api.clauses.errors import TermsEditionNotFound
from familycare_api.clauses.source_projection import ClauseSourceProjectionReader
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.repository import _database_url
from familycare_api.terms_knowledge.core import (
    COMPILER_REVISION,
    CompilationResult,
    CompiledSemanticRoot,
    SemanticKnowledgeError,
    parse_knowledge,
)
from familycare_api.terms_knowledge.generated_contracts import SemanticSource
from familycare_api.terms_knowledge.source_layout import observe_semantic_regions
from familycare_api.terms_knowledge.source_verification import (
    VERIFIER_REVISION,
    SourceSnapshot,
    verify_and_compile,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class SemanticSourceChanged(ValueError):
    def __init__(self) -> None:
        super().__init__("TERMS_SEMANTIC_SOURCE_CHANGED")


@dataclass(frozen=True, slots=True, repr=False)
class SemanticSourcePlan:
    input_context: dict[str, Any]
    input_digest: str
    snapshot: SourceSnapshot


@dataclass(frozen=True, slots=True, repr=False)
class SemanticPublication:
    publication_id: UUID
    candidate_id: UUID
    terms_edition_id: UUID
    outcome: str
    compilation: CompilationResult


@dataclass(frozen=True, slots=True, repr=False)
class CurrentSemanticRoot:
    publication_id: UUID
    root: CompiledSemanticRoot


def _outcome(result: CompilationResult) -> str:
    usable = sum(root.executable for root in result.roots)
    if (
        usable == len(result.roots)
        and result.processing_complete
        and all(not root.diagnostics for root in result.roots)
    ):
        return "VERIFIED"
    return "PARTIAL" if usable else "UNRESOLVED"


def _lock_source(
    connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope, edition: UUID
) -> None:
    row = connection.execute(
        "SELECT e.id FROM terms_editions e JOIN insurance_document_components c "
        "ON c.id=e.source_component_id AND c.household_space_id=e.household_space_id "
        "JOIN document_metadata_publications mp ON mp.id=c.metadata_publication_id "
        "JOIN document_metadata_proposals proposal ON proposal.id=mp.proposal_id "
        "JOIN document_structure_generations g ON g.id=proposal.generation_id "
        "JOIN document_versions v ON v.id=e.document_version_id "
        "JOIN documents d ON d.id=v.document_id "
        "JOIN family_members m ON m.id=c.family_member_id "
        "AND m.household_space_id=e.household_space_id "
        "WHERE e.id=%s AND e.household_space_id=%s AND e.deleted_at IS NULL "
        "FOR SHARE OF e,c,d,m,g",
        (edition, scope.household_space_id),
    ).fetchone()
    if row is None:
        raise TermsEditionNotFound


def _plan(
    connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope, edition: UUID
) -> SemanticSourcePlan:
    row = connection.execute(
        "SELECT context,encode(sha256(convert_to(context::text,'UTF8')),'hex') AS digest "
        "FROM (SELECT terms_semantic_input_context(%s,%s) AS context) input",
        (edition, scope.household_space_id),
    ).fetchone()
    if row is None or row["context"] is None:
        raise TermsEditionNotFound
    context = row["context"]
    reader = ClauseSourceProjectionReader(connection, scope.household_space_id)
    pages = tuple(range(context["page_start"], context["page_end"] + 1))
    projection: dict[str, Any] | None = None
    nodes: dict[str, Any] = {}
    manifests: list[Any] = []
    unresolved: list[Any] = []
    for start in range(0, len(pages), 25):
        part = reader.read(UUID(context["generation_id"]), pages[start : start + 25])
        if part is None:
            raise SemanticSourceChanged
        if projection is not None and projection["lineage"] != part["lineage"]:
            raise SemanticSourceChanged
        projection = part
        for node in part["nodes"]:
            previous = nodes.get(node["node_id"])
            if previous is not None and previous != node:
                raise SemanticSourceChanged
            nodes[node["node_id"]] = node
        manifests.extend(part["pages"])
        unresolved.extend(part["unresolved"])
    if projection is None:
        raise SemanticSourceChanged
    source = SemanticSource.model_validate(
        {
            "source_id": "terms",
            "document_version_id": context["document_version_id"],
            "terms_edition_id": str(edition),
            "generation_id": context["generation_id"],
            "content_sha256": context["content_sha256"],
            "structure_identity_sha256": context["structure_identity_sha256"],
        }
    )
    layout = observe_semantic_regions(
        {**projection, "nodes": list(nodes.values()), "pages": manifests, "unresolved": unresolved},
        component_page_start=context["page_start"],
        component_page_end=context["page_end"],
    )
    return SemanticSourcePlan(context, row["digest"], SourceSnapshot(source, layout))


class TermsSemanticRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def source_plan(self, scope: HouseholdScope, edition_id: UUID) -> SemanticSourcePlan:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_source(connection, scope, edition_id)
            return _plan(connection, scope, edition_id)

    def publish_candidate(
        self,
        scope: HouseholdScope,
        edition_id: UUID,
        payload: dict[str, Any],
        *,
        expected_input_digest: str,
    ) -> SemanticPublication:
        # Validate before opening the write transaction; immutable source replay is
        # repeated inside the lock and bound to this canonical input, not caller IDs.
        graph = parse_knowledge(payload)
        canonical = graph.model_dump(mode="json")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(edition_id),)
            )
            _lock_source(connection, scope, edition_id)
            plan = _plan(connection, scope, edition_id)
            if plan.input_digest != expected_input_digest:
                raise SemanticSourceChanged
            verified = verify_and_compile(canonical, sources={"terms": plan.snapshot})
            candidate = connection.execute(
                "INSERT INTO terms_semantic_candidates(household_space_id,terms_edition_id,"
                "input_context,input_digest,graph_json,graph_sha256) "
                "VALUES(%s,%s,%s,%s,%s,encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')) "
                "ON CONFLICT(terms_edition_id,input_digest,graph_sha256) DO NOTHING RETURNING id",
                (
                    scope.household_space_id,
                    edition_id,
                    Jsonb(plan.input_context),
                    plan.input_digest,
                    Jsonb(canonical),
                    Jsonb(canonical),
                ),
            ).fetchone()
            if candidate is None:
                candidate = connection.execute(
                    "SELECT id FROM terms_semantic_candidates WHERE terms_edition_id=%s "
                    "AND household_space_id=%s AND input_digest=%s "
                    "AND graph_sha256=encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')",
                    (edition_id, scope.household_space_id, plan.input_digest, Jsonb(canonical)),
                ).fetchone()
            assert candidate is not None
            result = verified.compilation
            outcome = _outcome(result)
            publication = connection.execute(
                "INSERT INTO terms_semantic_publications(candidate_id,household_space_id,"
                "terms_edition_id,"
                "verifier_revision,compiler_revision,proof_sha256,outcome,"
                "processing_complete,result_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(candidate_id,verifier_revision,compiler_revision) "
                "DO NOTHING RETURNING id",
                (
                    candidate["id"],
                    scope.household_space_id,
                    edition_id,
                    VERIFIER_REVISION,
                    COMPILER_REVISION,
                    verified.proof_sha256,
                    outcome,
                    result.processing_complete,
                    Jsonb(asdict(result)),
                ),
            ).fetchone()
            if publication is None:
                publication = connection.execute(
                    "SELECT id,result_json,proof_sha256 FROM terms_semantic_publications "
                    "WHERE candidate_id=%s AND verifier_revision=%s AND compiler_revision=%s",
                    (candidate["id"], VERIFIER_REVISION, COMPILER_REVISION),
                ).fetchone()
                assert publication is not None
                if (
                    publication["result_json"] != json.loads(json.dumps(asdict(result)))
                    or publication["proof_sha256"] != verified.proof_sha256
                ):
                    raise SemanticSourceChanged
            else:
                for root in result.roots:
                    connection.execute(
                        "INSERT INTO terms_semantic_root_publications(publication_id,root_node_id,"
                        "semantic_sha256,manifest_sha256,executable,root_json) "
                        "VALUES(%s,%s,%s,%s,%s,%s)",
                        (
                            publication["id"],
                            root.root_node_id,
                            root.semantic_sha256,
                            root.manifest_sha256,
                            root.executable,
                            Jsonb(asdict(root)),
                        ),
                    )
            return SemanticPublication(
                publication["id"], candidate["id"], edition_id, outcome, result
            )

    def current(self, scope: HouseholdScope, edition_id: UUID) -> tuple[SemanticPublication, ...]:
        """Return replayed attempts, newest first; never trust stored executable flags."""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_source(connection, scope, edition_id)
            plan = _plan(connection, scope, edition_id)
            rows = connection.execute(
                "SELECT p.*,c.graph_json FROM terms_semantic_publications p "
                "JOIN terms_semantic_candidates c ON c.id=p.candidate_id "
                "WHERE p.household_space_id=%s AND p.terms_edition_id=%s "
                "AND c.input_context=%s AND c.input_digest=%s "
                "AND p.verifier_revision=%s AND p.compiler_revision=%s "
                "ORDER BY p.created_at DESC,p.id DESC LIMIT 64",
                (
                    scope.household_space_id,
                    edition_id,
                    Jsonb(plan.input_context),
                    plan.input_digest,
                    VERIFIER_REVISION,
                    COMPILER_REVISION,
                ),
            ).fetchall()
            result = []
            for row in rows:
                try:
                    verified = verify_and_compile(
                        row["graph_json"], sources={"terms": plan.snapshot}
                    )
                except SemanticKnowledgeError:
                    continue
                if row["proof_sha256"] != verified.proof_sha256 or row["result_json"] != json.loads(
                    json.dumps(asdict(verified.compilation))
                ):
                    continue
                result.append(
                    SemanticPublication(
                        row["id"],
                        row["candidate_id"],
                        edition_id,
                        _outcome(verified.compilation),
                        verified.compilation,
                    )
                )
            return tuple(result)

    def last_usable_roots(
        self, scope: HouseholdScope, edition_id: UUID, root_node_ids: tuple[str, ...]
    ) -> tuple[CurrentSemanticRoot, ...]:
        """Select successful roots independently of the bounded recent-attempt history."""
        if (
            not isinstance(root_node_ids, tuple)
            or not 1 <= len(root_node_ids) <= 32
            or len(set(root_node_ids)) != len(root_node_ids)
            or any(
                not isinstance(key, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key) is None
                for key in root_node_ids
            )
        ):
            raise ValueError("TERMS_SEMANTIC_ROOT_REQUEST_INVALID")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_source(connection, scope, edition_id)
            plan = _plan(connection, scope, edition_id)
            result = []
            for root_id in root_node_ids:
                rows = connection.execute(
                    "SELECT p.*,c.graph_json FROM terms_semantic_root_publications r "
                    "JOIN terms_semantic_publications p ON p.id=r.publication_id "
                    "JOIN terms_semantic_candidates c ON c.id=p.candidate_id "
                    "WHERE p.household_space_id=%s AND p.terms_edition_id=%s "
                    "AND c.input_context=%s AND c.input_digest=%s "
                    "AND p.verifier_revision=%s AND p.compiler_revision=%s "
                    "AND r.root_node_id=%s AND r.executable "
                    "AND r.root_json->'diagnostics'='[]'::jsonb "
                    "ORDER BY p.created_at DESC,p.id DESC LIMIT 8",
                    (
                        scope.household_space_id,
                        edition_id,
                        Jsonb(plan.input_context),
                        plan.input_digest,
                        VERIFIER_REVISION,
                        COMPILER_REVISION,
                        root_id,
                    ),
                ).fetchall()
                for row in rows:
                    try:
                        verified = verify_and_compile(
                            row["graph_json"], sources={"terms": plan.snapshot}
                        )
                    except SemanticKnowledgeError:
                        continue
                    if row["proof_sha256"] != verified.proof_sha256 or row[
                        "result_json"
                    ] != json.loads(json.dumps(asdict(verified.compilation))):
                        continue
                    root = next(
                        (
                            root
                            for root in verified.compilation.roots
                            if root.root_node_id == root_id
                        ),
                        None,
                    )
                    if root is not None and root.executable and not root.diagnostics:
                        result.append(CurrentSemanticRoot(row["id"], root))
                        break
            return tuple(result)

"""Resume approved local reconstruction in an isolated, quiesced target database.

No provider runner, provider work preparation, source-binding confirmation, or
activation is performed. The caller owns the writer barrier and protected plan.
Durable preparation/proposal/publication records own per-source progress.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import psycopg
from familycare_worker.document_metadata import REVISION as METADATA_REVISION
from familycare_worker.document_preparation import PREPARATION_REVISION
from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION
from psycopg.rows import dict_row

_MAX_SOURCES = 1000
_MAX_PLAN_BYTES = 1024 * 1024
_STAGES = (
    "enrollment",
    "metadata",
    "editions",
    "applicability",
    "clause_sources",
    "local_semantics",
    "completed_semantic_work",
    "terms_changes",
    "canonical",
)
_CODES = frozenset(
    {
        "PLAN_INVALID",
        "SOURCE_CHANGED",
        "UNAPPROVED_SOURCE",
        "SOURCE_UNAVAILABLE",
        "BOUNDED",
        "STOPPED",
    }
)


class TransitionError(ValueError):
    def __init__(self, code: str = "SOURCE_UNAVAILABLE") -> None:
        self.code = code if code in _CODES else "SOURCE_UNAVAILABLE"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class DatabaseIdentity:
    cluster_id: str
    database_name: str
    database_oid: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(v, str) or not 1 <= len(v) <= 128
            for v in (self.cluster_id, self.database_name, self.database_oid)
        ):
            raise TransitionError("PLAN_INVALID")


@dataclass(frozen=True, repr=False)
class SourceIdentity:
    batch_item_id: UUID
    household_space_id: UUID
    family_member_id: UUID
    document_version_id: UUID
    extraction_id: UUID
    ocr_layer_id: UUID | None
    content_sha256: str
    source_sha256: str

    def __post_init__(self) -> None:
        ids = (
            self.batch_item_id,
            self.household_space_id,
            self.family_member_id,
            self.document_version_id,
            self.extraction_id,
        )
        if (
            any(not isinstance(v, UUID) or not v.int for v in ids)
            or (
                self.ocr_layer_id is not None
                and (not isinstance(self.ocr_layer_id, UUID) or not self.ocr_layer_id.int)
            )
            or any(
                not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None
                for v in (self.content_sha256, self.source_sha256)
            )
        ):
            raise TransitionError("PLAN_INVALID")


@dataclass(frozen=True, repr=False)
class ReconstructionPlan:
    database: DatabaseIdentity
    sources: tuple[SourceIdentity, ...]
    schema_revision: str = SUPPORTED_SCHEMA_REVISION
    preparation_revision: str = PREPARATION_REVISION
    metadata_revision: str = METADATA_REVISION

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.sources) <= _MAX_SOURCES
            or len({s.batch_item_id for s in self.sources}) != len(self.sources)
            or self.schema_revision != SUPPORTED_SCHEMA_REVISION
            or self.preparation_revision != PREPARATION_REVISION
            or self.metadata_revision != METADATA_REVISION
        ):
            raise TransitionError("PLAN_INVALID")
        object.__setattr__(
            self, "sources", tuple(sorted(self.sources, key=lambda s: s.batch_item_id))
        )


@dataclass(frozen=True, repr=False)
class SourceProgress:
    preparation: str
    generation_id: UUID | None = None
    metadata: str = "MISSING"
    components: int = 0
    unresolved_pages: int = 0
    pending_publications: int = 0
    identity_unresolved: int = 0
    semantic_unresolved: int = 0
    unprocessed_ranges: int = 0


@dataclass(frozen=True)
class ReconstructionReport:
    status: str
    sources: int
    steps: int
    prepared: int = 0
    source_unavailable: int = 0
    metadata_components: int = 0
    unresolved_pages: int = 0
    pending_publications: int = 0
    identity_unresolved: int = 0
    semantic_unresolved: int = 0
    partial_sources: int = 0
    unprocessed_ranges: int = 0


class ReconstructionAdapter(Protocol):
    def verify(self, plan: ReconstructionPlan, *, source: SourceIdentity | None = None) -> None: ...
    def observe(self, source: SourceIdentity) -> SourceProgress: ...
    def prepare(self, source: SourceIdentity) -> None: ...
    def metadata(self, generation: UUID) -> None: ...
    def project(self, stage: str) -> int: ...


def reconstruct(
    plan: ReconstructionPlan,
    *,
    adapter: ReconstructionAdapter,
    max_steps: int = 1000,
    deadline_seconds: float = 300,
    stop_requested: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> ReconstructionReport:
    if (
        type(max_steps) is not int
        or not 1 <= max_steps <= 10000
        or not 0 < deadline_seconds <= 3600
    ):
        raise TransitionError("PLAN_INVALID")
    started, steps = clock(), 0
    last_observed: dict[UUID, SourceProgress] = {}

    def observe(source: SourceIdentity) -> SourceProgress:
        progress = adapter.observe(source)
        last_observed[source.batch_item_id] = progress
        return progress

    def budget() -> None:
        if stop_requested():
            raise TransitionError("STOPPED")
        if clock() - started >= deadline_seconds:
            raise TransitionError("BOUNDED")

    def check(*, mutation: bool = False, source: SourceIdentity | None = None) -> None:
        budget()
        if mutation and steps >= max_steps:
            raise TransitionError("BOUNDED")
        adapter.verify(plan, source=source)

    try:
        if isinstance(adapter, PostgresReconstructionAdapter):
            adapter.checkpoint = budget
        check()
        for source in plan.sources:
            check(source=source)
            progress = observe(source)
            if progress.preparation in {"MISSING", "RETRYABLE_FAILED"}:
                check(mutation=True, source=source)
                adapter.prepare(source)
                steps += 1
                check(source=source)
                progress = observe(source)
            if (
                progress.preparation in {"PREPARED", "PARTIAL"}
                and progress.generation_id is not None
                and progress.metadata in {"MISSING", "RETRYABLE_FAILED"}
            ):
                check(mutation=True, source=source)
                adapter.metadata(progress.generation_id)
                steps += 1
                check(source=source)
        # Existing API operations are global. verify proves this isolated DB's
        # active sources are approved before every operation, including re-entry.
        while True:
            changed = 0
            for stage in _STAGES:
                check(mutation=True)
                changed += adapter.project(stage)
                steps += 1
                check()
            if not changed:
                break
        check()
        observed = [observe(source) for source in plan.sources]
        check()
        unavailable = sum(
            p.generation_id is None or p.metadata != "PREPARED" or p.components == 0
            for p in observed
        )
        incomplete = unavailable or any(
            p.preparation == "PARTIAL"
            or p.unprocessed_ranges
            or p.unresolved_pages
            or p.pending_publications
            or p.identity_unresolved
            or p.semantic_unresolved
            for p in observed
        )
        status = "PARTIAL" if incomplete else "LOCAL_RECONSTRUCTION_COMPLETE"
    except TransitionError as error:
        status = error.code
    except Exception:
        status = "SOURCE_UNAVAILABLE"
    # Keep only reads already completed within the budget. Never issue a final
    # database inventory after cancellation or deadline just to fill counters.
    observed = list(last_observed.values())
    return ReconstructionReport(
        status,
        len(plan.sources),
        steps,
        prepared=sum(p.preparation == "PREPARED" for p in observed),
        source_unavailable=sum(
            p.generation_id is None or p.metadata != "PREPARED" or p.components == 0
            for p in observed
        ),
        metadata_components=sum(p.components for p in observed),
        unresolved_pages=sum(p.unresolved_pages for p in observed),
        pending_publications=sum(p.pending_publications for p in observed),
        identity_unresolved=sum(p.identity_unresolved for p in observed),
        semantic_unresolved=sum(p.semantic_unresolved for p in observed),
        partial_sources=sum(p.preparation == "PARTIAL" for p in observed),
        unprocessed_ranges=sum(p.unprocessed_ranges for p in observed),
    )


_SOURCE_SQL = """
SELECT item.id AS batch_item_id,batch.household_space_id,batch.family_member_id,
       version.id AS document_version_id,extraction.id AS extraction_id,
       layer.id AS ocr_layer_id,version.content_sha256
FROM document_batch_items item
JOIN document_batches batch ON batch.id=item.batch_id
JOIN family_members member ON member.id=batch.family_member_id
  AND member.household_space_id=batch.household_space_id AND member.deleted_at IS NULL
JOIN documents document ON document.id=item.document_id AND document.deleted_at IS NULL
JOIN LATERAL (SELECT v.* FROM document_versions v WHERE v.document_id=item.document_id
  AND (item.processed_document_version_id IS NULL OR v.id=item.processed_document_version_id)
  ORDER BY v.version_number DESC,v.id LIMIT 1) version ON true
JOIN LATERAL (SELECT x.* FROM extractions x WHERE x.document_version_id=version.id
  AND x.status='succeeded' ORDER BY x.succeeded_at DESC,x.id LIMIT 1) extraction ON true
LEFT JOIN LATERAL (SELECT o.id FROM ocr_layers o WHERE o.extraction_id=extraction.id
  AND o.status='succeeded' ORDER BY o.created_at DESC,o.id DESC LIMIT 1) layer ON true
WHERE item.state='succeeded' AND item.id=ANY(%s::uuid[])
  AND EXISTS(SELECT 1 FROM evidence e WHERE e.extraction_id=extraction.id
    AND e.document_version_id=version.id AND e.household_space_id=batch.household_space_id)
ORDER BY item.id
"""
# Hash every retained input consumed by load_stored_structure; payloads remain in PG.
_INPUT_QUERIES = (
    "SELECT x.*,v.page_count,d.document_kind FROM extractions x "
    "JOIN document_versions v ON v.id=x.document_version_id "
    "JOIN documents d ON d.id=v.document_id WHERE x.id=%s",
    "SELECT p.* FROM extraction_pages p WHERE p.extraction_id=%s",
    "SELECT b.* FROM extraction_blocks b JOIN extraction_pages p ON p.id=b.page_id "
    "WHERE p.extraction_id=%s",
    "SELECT t.* FROM extraction_tables t JOIN extraction_pages p ON p.id=t.page_id "
    "WHERE p.extraction_id=%s",
    "SELECT c.* FROM extraction_cells c JOIN extraction_tables t ON t.id=c.table_id "
    "JOIN extraction_pages p ON p.id=t.page_id WHERE p.extraction_id=%s",
    "SELECT o.* FROM ocr_layers o WHERE o.id=%s",
    "SELECT p.* FROM ocr_pages p WHERE p.ocr_layer_id=%s",
    "SELECT b.* FROM ocr_blocks b JOIN ocr_pages p ON p.id=b.ocr_page_id WHERE p.ocr_layer_id=%s",
)


def _database_identity(connection: psycopg.Connection[Any]) -> DatabaseIdentity:
    row = connection.execute(
        "SELECT current_database() AS database_name, "
        "(SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database_oid, "
        "system_identifier::text AS cluster_id FROM pg_control_system()"
    ).fetchone()
    revisions = connection.execute(
        "SELECT version_num FROM public.alembic_version LIMIT 2"
    ).fetchall()
    if not row or revisions != [{"version_num": SUPPORTED_SCHEMA_REVISION}]:
        raise TransitionError("SOURCE_CHANGED")
    return DatabaseIdentity(**row)


def _snapshot(
    connection: psycopg.Connection[Any],
    ids: Sequence[UUID],
    checkpoint: Callable[[], None] = lambda: None,
) -> tuple[SourceIdentity, ...]:
    connection.execute("SET LOCAL TIME ZONE 'UTC'")
    rows = connection.execute(_SOURCE_SQL, (list(ids),)).fetchall()
    if len(rows) != len(ids):
        raise TransitionError("SOURCE_UNAVAILABLE")
    result = []
    for row in rows:
        digest = hashlib.sha256()
        for ordinal, query in enumerate(_INPUT_QUERIES):
            checkpoint()
            digest.update(bytes([ordinal]))
            key = row["extraction_id"] if ordinal < 5 else row["ocr_layer_id"]
            query = (
                "SELECT encode(sha256(convert_to(to_jsonb(input)::text,'UTF8')),'hex') AS digest "
                "FROM (" + query + ") input ORDER BY digest LIMIT 1000001"
            )
            # Server-side cursor keeps client memory bounded for large sources.
            with connection.cursor(name=f"input_{ordinal}_{row['batch_item_id'].hex}") as cursor:
                cursor.execute(query, (key,))
                for count, item in enumerate(cursor, 1):
                    if count % 512 == 0:
                        checkpoint()
                    if count > 1000000:
                        raise TransitionError("SOURCE_UNAVAILABLE")
                    digest.update(bytes.fromhex(item["digest"]))
        result.append(SourceIdentity(**row, source_sha256=digest.hexdigest()))
    return tuple(result)


def capture_plan(database_url: str, batch_item_ids: Sequence[UUID]) -> ReconstructionPlan:
    """Read only the explicitly selected source identities; caller protects serialization."""
    ids = tuple(batch_item_ids)
    if not 1 <= len(ids) <= _MAX_SOURCES or len(set(ids)) != len(ids):
        raise TransitionError("PLAN_INVALID")
    try:
        with _connect(database_url) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return ReconstructionPlan(_database_identity(connection), _snapshot(connection, ids))
    except TransitionError:
        raise
    except Exception:
        raise TransitionError() from None


def _connect(database_url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1),
        row_factory=dict_row,
        connect_timeout=5,
        options="-c statement_timeout=30000 -c lock_timeout=5000",
    )


class PostgresReconstructionAdapter:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.checkpoint: Callable[[], None] = lambda: None
        self.plan: ReconstructionPlan | None = None

    def verify(self, plan: ReconstructionPlan, *, source: SourceIdentity | None = None) -> None:
        if source is not None and source not in plan.sources:
            raise TransitionError("UNAPPROVED_SOURCE")
        selected = plan.sources if source is None else (source,)
        with _connect(self.database_url) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if _database_identity(connection) != plan.database:
                raise TransitionError("SOURCE_CHANGED")
            ids = [s.batch_item_id for s in plan.sources]
            selected_ids = [s.batch_item_id for s in selected]
            if _snapshot(connection, selected_ids, self.checkpoint) != selected:
                raise TransitionError("SOURCE_CHANGED")
            outside = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM document_batch_items i JOIN documents d "
                "ON d.id=i.document_id WHERE d.deleted_at IS NULL AND i.state='succeeded' "
                "AND NOT(i.id=ANY(%s::uuid[]))) OR EXISTS(SELECT 1 FROM documents d "
                "WHERE d.deleted_at IS NULL AND EXISTS(SELECT 1 FROM document_versions v "
                "JOIN extractions x ON x.document_version_id=v.id AND x.status='succeeded' "
                "WHERE v.document_id=d.id) AND NOT EXISTS(SELECT 1 FROM document_batch_items i "
                "WHERE i.document_id=d.id AND i.id=ANY(%s::uuid[]))) OR EXISTS("
                "SELECT 1 FROM document_structure_generations g WHERE g.is_current "
                "AND NOT(g.batch_item_id=ANY(%s::uuid[]))) AS outside",
                (ids, ids, ids),
            ).fetchone()
            if outside is None or outside["outside"]:
                raise TransitionError("UNAPPROVED_SOURCE")
        self.plan = plan

    def prepare(self, source: SourceIdentity) -> None:
        from familycare_worker.document_preparation import DocumentPreparationRunner

        DocumentPreparationRunner(self.database_url, batch_item_id=source.batch_item_id).run_once(
            "transition-local"
        )

    def metadata(self, generation: UUID) -> None:
        from familycare_worker.document_metadata_repository import DocumentMetadataRunner

        DocumentMetadataRunner(self.database_url, generation_id=generation).run_once(
            "transition-local"
        )

    def project(self, stage: str) -> int:
        from familycare_api.clauses.component_editions import ComponentTermsProjector
        from familycare_api.clauses.source_repository import ClauseSourceProjector
        from familycare_api.clauses.terms_applicability_repository import (
            TermsApplicabilityProjector,
        )
        from familycare_api.clauses.terms_change_repository import TermsChangeProjector
        from familycare_api.insurance_documents.metadata_publication import (
            DocumentMetadataProjector,
        )
        from familycare_api.insurance_reconciliation.canonical_repository import (
            CanonicalLinkRepository,
        )
        from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
        from familycare_api.terms_knowledge.projector import TermsSemanticProjector
        from familycare_api.terms_knowledge.work_repository import TermsSemanticWorkRepository

        db = self.database_url
        if self.plan is None:
            raise TransitionError("PLAN_INVALID")
        # A previous current generation is outside the approved latest source
        # when local preparation is partial or unavailable. Do not project it.
        with _connect(db) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            current = connection.execute(
                "SELECT id FROM document_structure_generations WHERE is_current LIMIT 1001"
            ).fetchall()
            approved = {
                p.generation_id
                for s in self.plan.sources
                if (p := self.observe(s)).preparation in {"PREPARED", "PARTIAL"}
                and p.generation_id is not None
            }
            if len(current) > _MAX_SOURCES or any(r["id"] not in approved for r in current):
                raise TransitionError("UNAPPROVED_SOURCE")

        def stop() -> bool:
            self.checkpoint()
            return False

        operations = {
            "enrollment": lambda: RangeEnrollmentProjector(db).project_pending(
                limit=5, stop_requested=stop
            ),
            "metadata": lambda: DocumentMetadataProjector(db).project_pending(
                limit=5, stop_requested=stop
            ),
            "editions": lambda: ComponentTermsProjector(db).project_pending(
                limit=5, stop_requested=stop
            ),
            "applicability": lambda: TermsApplicabilityProjector(db).refresh_pending(
                limit=5, stop_requested=stop
            ),
            "clause_sources": lambda: ClauseSourceProjector(db).refresh_pending(
                limit=5, stop_requested=stop
            ),
            "local_semantics": lambda: TermsSemanticProjector(db).project_pending(
                limit=5, stop_requested=stop
            ),
            "completed_semantic_work": lambda: TermsSemanticWorkRepository(db).project_pending(
                limit=5, stop_requested=stop
            ),
            "terms_changes": lambda: TermsChangeProjector(db).refresh_pending(
                limit=5, stop_requested=stop
            ),
            "canonical": lambda: CanonicalLinkRepository(db).refresh_pending(),
        }
        return operations[stage]()

    def observe(self, source: SourceIdentity) -> SourceProgress:
        from familycare_api.terms_knowledge.projector import _revision

        with _connect(self.database_url) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT p.state,p.generation_id,g.is_current,g.cancelled,m.id AS proposal_id,"
                "m.state AS metadata, "
                "jsonb_array_length(g.plan_json->'unprocessed') AS unprocessed_ranges, "
                "jsonb_array_length(m.proposal_json->'components') AS components, "
                "jsonb_array_length(m.proposal_json->'unresolved_pages') AS unresolved_pages "
                "FROM document_structure_preparations p LEFT JOIN document_structure_generations g "
                "ON g.id=p.generation_id LEFT JOIN document_metadata_proposals m "
                "ON m.generation_id=g.id AND m.revision=%s "
                "WHERE p.batch_item_id=%s AND p.extraction_id=%s "
                "AND p.ocr_layer_id IS NOT DISTINCT FROM %s AND p.pipeline_revision=%s",
                (
                    METADATA_REVISION,
                    source.batch_item_id,
                    source.extraction_id,
                    source.ocr_layer_id,
                    PREPARATION_REVISION,
                ),
            ).fetchone()
            if row is None:
                return SourceProgress("MISSING")
            omissions = row["unprocessed_ranges"] or 0
            if (
                row["state"] not in {"PREPARED", "PARTIAL"}
                or not row["is_current"]
                or row["cancelled"]
            ):
                return SourceProgress(
                    row["state"] if row["state"] != "PREPARED" else "PARTIAL",
                    unprocessed_ranges=omissions,
                )
            counts = connection.execute(
                "SELECT count(*) FILTER(WHERE p.outcome='APPLIED') AS applied, "
                "count(*) FILTER(WHERE c.role='terms' AND (t.id IS NULL OR t.outcome<>'APPLIED')) "
                "AS identity_unresolved, count(*) FILTER(WHERE t.terms_edition_id IS NOT NULL "
                "AND NOT EXISTS(SELECT 1 FROM terms_semantic_processing_runs r "
                "JOIN terms_semantic_processing_completions done ON done.run_id=r.id "
                "WHERE r.terms_edition_id=t.terms_edition_id AND r.planner_revision=%s "
                "AND r.input_context=terms_semantic_input_context("
                "r.terms_edition_id,r.household_space_id) "
                "AND done.outcome='COMPLETE')) AS semantic_unresolved "
                "FROM document_metadata_publications p LEFT JOIN insurance_document_components c "
                "ON c.id=p.component_id LEFT JOIN component_terms_publications t "
                "ON t.component_id=c.id "
                "WHERE p.proposal_id=%s AND p.validator_revision=%s",
                (
                    _revision(),
                    row["proposal_id"],
                    METADATA_REVISION.replace("document-metadata-", "document-metadata-api-"),
                ),
            ).fetchone()
            assert counts is not None
            components = row["components"] or 0
            return SourceProgress(
                row["state"],
                row["generation_id"],
                row["metadata"] or "MISSING",
                components,
                row["unresolved_pages"] or 0,
                max(0, components - counts["applied"]),
                counts["identity_unresolved"],
                counts["semantic_unresolved"],
                omissions,
            )


def load_plan(path: Path) -> ReconstructionPlan:
    try:
        root = Path(__file__).resolve().parents[1]
        if not path.is_absolute() or path.resolve().is_relative_to(root):
            raise TransitionError("PLAN_INVALID")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
                raise TransitionError("PLAN_INVALID")
            raw = stream.read(_MAX_PLAN_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                details.st_size,
                details.st_mtime_ns,
                details.st_ctime_ns,
            ):
                raise TransitionError("PLAN_INVALID")
        if len(raw) > _MAX_PLAN_BYTES:
            raise TransitionError("PLAN_INVALID")

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result = dict(pairs)
            if len(result) != len(pairs):
                raise TransitionError("PLAN_INVALID")
            return result

        value = json.loads(raw, object_pairs_hook=unique)
        if set(value) != {
            "database",
            "sources",
            "schema_revision",
            "preparation_revision",
            "metadata_revision",
        }:
            raise TransitionError("PLAN_INVALID")
        value["database"] = DatabaseIdentity(**value["database"])
        if any(
            set(source) != set(SourceIdentity.__dataclass_fields__) for source in value["sources"]
        ):
            raise TransitionError("PLAN_INVALID")
        value["sources"] = tuple(
            SourceIdentity(
                batch_item_id=UUID(source["batch_item_id"]),
                household_space_id=UUID(source["household_space_id"]),
                family_member_id=UUID(source["family_member_id"]),
                document_version_id=UUID(source["document_version_id"]),
                extraction_id=UUID(source["extraction_id"]),
                ocr_layer_id=UUID(source["ocr_layer_id"]) if source["ocr_layer_id"] else None,
                content_sha256=source["content_sha256"],
                source_sha256=source["source_sha256"],
            )
            for source in value["sources"]
        )
        return ReconstructionPlan(**value)
    except Exception:
        raise TransitionError("PLAN_INVALID") from None


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if list(sys.argv[1:] if argv is None else argv):
            raise TransitionError("PLAN_INVALID")
        plan = load_plan(Path(os.environ["FAMILYCARE_TRANSITION_PLAN_PATH"]))
        report = reconstruct(
            plan, adapter=PostgresReconstructionAdapter(os.environ["FAMILYCARE_DATABASE_URL"])
        )
        print(json.dumps(asdict(report), sort_keys=True))
        return 0 if report.status == "LOCAL_RECONSTRUCTION_COMPLETE" else 1
    except Exception:
        print(json.dumps({"status": "PLAN_INVALID"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

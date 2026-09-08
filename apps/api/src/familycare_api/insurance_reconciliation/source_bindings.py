"""Apply exact source declarations outside immutable private knowledge snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.common.scope import HouseholdScope

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class KnowledgeSourceBindingError(ValueError):
    def __init__(self) -> None:
        super().__init__("KNOWLEDGE_SOURCE_BINDING_INVALID")


class SourceBindingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_binding_id: UUID
    source_alias_digest_sha256: Digest
    document_version_id: UUID
    evidence_id: UUID
    content_sha256: Digest
    page_count: int = Field(ge=1, le=500, strict=True)
    document_kind: Literal["policy", "terms", "application", "amendment", "claim", "supporting"]
    expected_current_binding_id: UUID | None


class KnowledgeSourceManifest(BaseModel):
    """A caller-provided exact source declaration, never a filename similarity guess."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["knowledge-source-bindings-v1"]
    import_run_id: UUID
    package_digest_sha256: Digest
    entries: tuple[SourceBindingEntry, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        if len({entry.document_binding_id for entry in self.entries}) != len(self.entries) or len(
            {entry.source_alias_digest_sha256 for entry in self.entries}
        ) != len(self.entries):
            raise ValueError("duplicate source binding")
        return self


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class KnowledgeSourceBindingRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def apply_manifest(
        self, scope: HouseholdScope, manifest: KnowledgeSourceManifest
    ) -> tuple[UUID, ...]:
        """Atomically validate metadata and append history under the household writer lock."""
        manifest_digest = _digest(manifest.model_dump(mode="json"))
        ids = []
        try:
            with psycopg.connect(
                self.database_url, row_factory=dict_row, connect_timeout=5
            ) as connection:
                connection.execute("SET LOCAL statement_timeout='5s'")
                household = connection.execute(
                    "SELECT id FROM household_spaces WHERE id=%s AND deleted_at IS NULL FOR UPDATE",
                    (scope.household_space_id,),
                ).fetchone()
                run = connection.execute(
                    "SELECT id FROM private_knowledge_import_runs WHERE id=%s AND "
                    "household_space_id=%s "
                    "AND package_digest_sha256=%s AND state='APPLIED' AND is_current FOR SHARE",
                    (
                        manifest.import_run_id,
                        scope.household_space_id,
                        manifest.package_digest_sha256,
                    ),
                ).fetchone()
                if household is None or run is None:
                    raise KnowledgeSourceBindingError
                for entry in manifest.entries:
                    binding_digest = _digest(
                        {"manifest": manifest_digest, "entry": entry.model_dump(mode="json")}
                    )
                    current = connection.execute(
                        "SELECT id,binding_sha256 FROM private_knowledge_source_bindings "
                        "WHERE document_binding_id=%s AND import_run_id=%s AND "
                        "household_space_id=%s "
                        "AND is_current FOR UPDATE",
                        (
                            entry.document_binding_id,
                            manifest.import_run_id,
                            scope.household_space_id,
                        ),
                    ).fetchone()
                    if current is not None and current["binding_sha256"] == binding_digest:
                        # Revalidate even an idempotent request after source metadata changes.
                        valid = connection.execute(
                            "SELECT 1 FROM document_versions v JOIN documents d ON "
                            "d.id=v.document_id "
                            "JOIN evidence e ON e.id=%s AND e.document_version_id=v.id "
                            "JOIN extractions x ON x.id=e.extraction_id AND "
                            "x.document_version_id=v.id "
                            "WHERE v.id=%s AND v.content_sha256=%s AND "
                            "e.content_sha256=v.content_sha256 "
                            "AND e.household_space_id=%s AND v.page_count=%s AND "
                            "d.document_kind=%s "
                            "AND d.deleted_at IS NULL AND x.status='succeeded' "
                            "AND e.physical_page BETWEEN 1 AND v.page_count "
                            "AND EXISTS (SELECT 1 FROM private_knowledge_document_bindings b "
                            "WHERE b.id=%s AND b.import_run_id=%s AND b.household_space_id=%s "
                            "AND b.source_alias_digest_sha256=%s)",
                            (
                                entry.evidence_id,
                                entry.document_version_id,
                                entry.content_sha256,
                                scope.household_space_id,
                                entry.page_count,
                                entry.document_kind,
                                entry.document_binding_id,
                                manifest.import_run_id,
                                scope.household_space_id,
                                entry.source_alias_digest_sha256,
                            ),
                        ).fetchone()
                        if valid is None:
                            raise KnowledgeSourceBindingError
                        ids.append(current["id"])
                        continue
                    if (
                        current["id"] if current is not None else None
                    ) != entry.expected_current_binding_id:
                        raise KnowledgeSourceBindingError
                    if current is not None:
                        connection.execute(
                            "UPDATE private_knowledge_source_bindings SET is_current=false, "
                            "superseded_at=clock_timestamp() WHERE id=%s",
                            (current["id"],),
                        )
                    row = connection.execute(
                        "INSERT INTO "
                        "private_knowledge_source_bindings(import_run_id,household_space_id,"
                        "document_binding_id,source_alias_digest_sha256,document_version_id,evide"
                        "nce_id,"
                        "content_sha256,page_count,document_kind,authority,manifest_sha256,bindin"
                        "g_sha256) "
                        "VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROGRAM_VERIFIED_CONTENT_MANIFEST',%s,%s) "
                        "RETURNING id",
                        (
                            manifest.import_run_id,
                            scope.household_space_id,
                            entry.document_binding_id,
                            entry.source_alias_digest_sha256,
                            entry.document_version_id,
                            entry.evidence_id,
                            entry.content_sha256,
                            entry.page_count,
                            entry.document_kind,
                            manifest_digest,
                            binding_digest,
                        ),
                    ).fetchone()
                    assert row is not None
                    ids.append(row["id"])
        except psycopg.Error:
            raise KnowledgeSourceBindingError from None
        return tuple(ids)

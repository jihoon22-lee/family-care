"""Publish independently checked component roles without enrollment or pairing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.document_locks import lock_document_content
from familycare_api.insurance_documents.metadata_publication_store import (
    record_component_publication,
)
from familycare_api.insurance_documents.metadata_supersession import refine_component
from familycare_api.insurance_documents.metadata_validation import (
    MetadataSourceContext,
    validate_component_metadata,
)
from familycare_api.insurance_documents.repository import _database_url

VALIDATOR_REVISION = "document-metadata-api-v4"


class DocumentMetadataProjector:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def project_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] | None = None
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("invalid metadata projection limit")
        completed = 0
        for _ in range(limit):
            if stop_requested and stop_requested():
                break
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL statement_timeout='30s'")
                source = connection.execute(
                    """
                    SELECT proposal.id AS proposal_id,proposal.proposal_json,
                      proposal.revision AS metadata_revision,
                      replace(proposal.revision,'document-metadata-','document-metadata-api-')
                        AS validator_revision,
                      g.id AS generation_id,g.household_space_id,g.family_member_id,
                      g.batch_item_id,g.document_version_id,g.structure_json->'lineage' AS lineage,
                      version.content_sha256
                    FROM document_metadata_proposals proposal
                    JOIN document_structure_generations g ON g.id=proposal.generation_id
                    JOIN document_batch_items item ON item.id=g.batch_item_id
                    JOIN document_batches batch ON batch.id=item.batch_id
                      AND batch.household_space_id=g.household_space_id
                      AND batch.family_member_id=g.family_member_id
                    JOIN family_members member ON member.id=g.family_member_id
                      AND member.household_space_id=g.household_space_id
                    JOIN document_versions version ON version.id=g.document_version_id
                      AND version.document_id=item.document_id
                    JOIN documents document ON document.id=version.document_id
                    WHERE proposal.state='PREPARED' AND g.is_current
                      AND proposal.revision IN (
                        'document-metadata-v1','document-metadata-v2','document-metadata-v3',
                        'document-metadata-v4','document-metadata-v5','document-metadata-v6',
                        'document-metadata-v7','document-metadata-v8','document-metadata-v9')
                      AND item.state='succeeded' AND member.deleted_at IS NULL
                      AND document.deleted_at IS NULL
                      AND (item.processed_document_version_id IS NULL
                        OR item.processed_document_version_id=version.id)
                      AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements(proposal.proposal_json->'components') c
                        WHERE NOT EXISTS (SELECT 1 FROM document_metadata_publications p
                          WHERE p.proposal_id=proposal.id AND p.component_identity=c->>'identity'
                            AND p.validator_revision=replace(proposal.revision,
                              'document-metadata-','document-metadata-api-')))
                    ORDER BY proposal.created_at,proposal.id
                    FOR UPDATE OF proposal SKIP LOCKED LIMIT 1
                    """,
                ).fetchone()
                if source is None:
                    break
                lock_document_content(
                    connection, source["household_space_id"], source["content_sha256"]
                )
                current = connection.execute(
                    """
                    SELECT g.id FROM document_structure_generations g
                    JOIN document_batch_items item ON item.id=g.batch_item_id
                    JOIN document_batches batch ON batch.id=item.batch_id
                      AND batch.household_space_id=g.household_space_id
                      AND batch.family_member_id=g.family_member_id
                    JOIN family_members member ON member.id=g.family_member_id
                      AND member.household_space_id=g.household_space_id
                    JOIN document_versions version ON version.id=g.document_version_id
                      AND version.document_id=item.document_id
                    JOIN documents document ON document.id=version.document_id
                    WHERE g.id=%s AND g.is_current AND item.state='succeeded'
                      AND g.household_space_id=%s AND g.family_member_id=%s
                      AND member.deleted_at IS NULL AND document.deleted_at IS NULL
                      AND (item.processed_document_version_id IS NULL
                        OR item.processed_document_version_id=version.id)
                    FOR UPDATE OF g,item,batch,member,document
                    """,
                    (
                        source["generation_id"],
                        source["household_space_id"],
                        source["family_member_id"],
                    ),
                ).fetchone()
                if current is None:
                    continue
                # The item lock also serializes with manual component creation's SHARE lock.
                self._publish(connection, source, stop_requested)
                completed += 1
        return completed

    @staticmethod
    def _publish(
        connection: psycopg.Connection[dict[str, Any]],
        source: dict[str, Any],
        stop_requested: Callable[[], bool] | None,
    ) -> None:
        validator_revision = source["validator_revision"]

        def load_page(number: int) -> dict[str, Any]:
            projection = connection.execute(
                "SELECT document_structure_projection(%s,%s,%s) AS source",
                (source["generation_id"], source["household_space_id"], [number]),
            ).fetchone()
            if projection is None or projection["source"] is None:
                raise ValueError("metadata page unavailable")
            return dict(projection["source"])

        source_context = (
            MetadataSourceContext(
                source["lineage"], load_page, revision=source["metadata_revision"]
            )
            if source["metadata_revision"]
            in {
                "document-metadata-v3",
                "document-metadata-v4",
                "document-metadata-v5",
                "document-metadata-v6",
                "document-metadata-v7",
                "document-metadata-v8",
                "document-metadata-v9",
            }
            else None
        )
        for component in source["proposal_json"]["components"]:
            if stop_requested and stop_requested():
                break
            if connection.execute(
                "SELECT 1 FROM document_metadata_publications WHERE proposal_id=%s "
                "AND component_identity=%s AND validator_revision=%s",
                (source["proposal_id"], component["identity"], validator_revision),
            ).fetchone():
                continue

            valid = validate_component_metadata(
                component,
                {"lineage": source["lineage"], "nodes": []},
                page_loader=load_page,
                revision=source["metadata_revision"],
                source_context=source_context,
            )
            outcome = "APPLIED" if valid else "INVALID"
            if valid:
                overlaps = connection.execute(
                    "SELECT c.*,publication.proof_json FROM insurance_document_components c "
                    "JOIN document_versions version ON version.id=c.document_version_id "
                    "LEFT JOIN document_metadata_publications publication "
                    "ON publication.id=c.metadata_publication_id "
                    "WHERE c.household_space_id=%s AND c.family_member_id=%s "
                    "AND version.content_sha256=%s "
                    "AND NOT(c.page_end<%s OR c.page_start>%s) ORDER BY c.id FOR UPDATE OF c",
                    (
                        source["household_space_id"],
                        source["family_member_id"],
                        source["content_sha256"],
                        component["page_start"],
                        component["page_end"],
                    ),
                ).fetchall()
                if overlaps:
                    if refine_component(connection, source, component, overlaps):
                        continue
                    outcome = "DEFERRED"
            record_component_publication(connection, source, component, outcome)

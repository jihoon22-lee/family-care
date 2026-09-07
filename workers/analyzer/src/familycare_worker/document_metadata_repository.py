"""Local metadata discovery and page streaming, with independent durable retries."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.document_metadata import (
    REVISION,
    DocumentMetadataError,
    analyze_metadata_pages,
    metadata_proposal,
)
from familycare_worker.document_structure import (
    SourceLineage,
    SourceTextSpan,
    StructureCell,
    StructureNode,
    StructurePage,
)
from familycare_worker.jobs import psycopg_database_url


def _pages(
    connection: psycopg.Connection[dict[str, Any]], row: dict[str, Any]
) -> Iterator[tuple[StructurePage, list[StructureNode]]]:
    pages = connection.execute(
        "SELECT (page->>'page_number')::int AS number,page->>'active_layer' AS layer "
        "FROM document_structure_generations g, "
        "jsonb_array_elements(g.structure_json->'pages') page "
        "WHERE g.id=%s AND g.structure_json->>'storage_layout' IS DISTINCT FROM 'page-v1' "
        "UNION ALL SELECT part_number,payload_json->'page'->>'active_layer' "
        "FROM document_structure_page_payloads WHERE generation_id=%s AND part_number>0 "
        "ORDER BY number",
        (row["id"], row["id"]),
    ).fetchall()
    if not 1 <= len(pages) <= 500:
        raise DocumentMetadataError
    for page in pages:
        projection = connection.execute(
            "SELECT document_structure_projection(%s,%s,%s) AS source",
            (row["id"], row["household_space_id"], [page["number"]]),
        ).fetchone()
        if projection is None or projection["source"] is None:
            raise DocumentMetadataError
        nodes = []
        for raw in projection["source"]["nodes"]:
            if raw["page_number"] != page["number"]:
                continue
            values = dict(raw)
            values["cells"] = tuple(StructureCell(**cell) for cell in raw.get("cells", []))
            values["source_spans"] = tuple(
                SourceTextSpan(**span) for span in raw.get("source_spans", [])
            )
            nodes.append(StructureNode(**values))
        yield StructurePage(page["number"], page["layer"], (), "unknown", ()), nodes


def _store(
    connection: psycopg.Connection[dict[str, Any]],
    generation_id: UUID,
    state: str,
    attempts: int,
    payload: Any,
    error_code: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO document_metadata_proposals
          (generation_id,revision,state,attempts,proposal_json,error_code,available_at)
        VALUES (%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '60 seconds')
        ON CONFLICT (generation_id,revision) DO UPDATE SET
          state=EXCLUDED.state,attempts=EXCLUDED.attempts,proposal_json=EXCLUDED.proposal_json,
          error_code=EXCLUDED.error_code,available_at=EXCLUDED.available_at,
          updated_at=clock_timestamp()
    """,
        (
            generation_id,
            REVISION,
            state,
            attempts,
            None if payload is None else Jsonb(payload),
            error_code,
        ),
    )


class DocumentMetadataRunner:
    """Use generation row locks; metadata failure never mutates an existing IR."""

    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def run_once(self, worker_id: str) -> bool:
        del worker_id
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL statement_timeout='30s'")
                row = connection.execute(
                    """
                    SELECT g.id,g.household_space_id,g.identity_sha256,
                           g.structure_json->'lineage' AS lineage
                    FROM document_structure_generations g
                    JOIN document_batch_items item ON item.id=g.batch_item_id
                    JOIN document_batches batch ON batch.id=item.batch_id
                      AND batch.household_space_id=g.household_space_id
                      AND batch.family_member_id=g.family_member_id
                    JOIN family_members member ON member.id=g.family_member_id
                      AND member.household_space_id=g.household_space_id
                      AND member.deleted_at IS NULL
                    JOIN document_versions version ON version.id=g.document_version_id
                      AND version.document_id=item.document_id
                    JOIN documents document ON document.id=version.document_id
                      AND document.deleted_at IS NULL
                    LEFT JOIN document_metadata_proposals proposal
                      ON proposal.generation_id=g.id AND proposal.revision=%s
                    WHERE g.is_current AND item.state='succeeded'
                      AND (item.processed_document_version_id IS NULL
                        OR item.processed_document_version_id=version.id)
                      AND (proposal.id IS NULL OR (proposal.state='RETRYABLE_FAILED'
                        AND proposal.attempts<3 AND proposal.available_at<=clock_timestamp()))
                    ORDER BY g.created_at,g.id FOR UPDATE OF g SKIP LOCKED LIMIT 1
                """,
                    (REVISION,),
                ).fetchone()
                if row is None:
                    return False
                previous = connection.execute(
                    "SELECT state,attempts,available_at<=clock_timestamp() AS due "
                    "FROM document_metadata_proposals WHERE generation_id=%s AND revision=%s",
                    (row["id"], REVISION),
                ).fetchone()
                if previous and (previous["state"] != "RETRYABLE_FAILED" or not previous["due"]):
                    return False
                attempts = 1 if previous is None else previous["attempts"] + 1
                state, payload, error_code = "PREPARED", None, None
                try:
                    with connection.transaction():
                        lineage = dict(row["lineage"])
                        lineage["document_version_id"] = UUID(lineage["document_version_id"])
                        lineage["extraction_id"] = UUID(lineage["extraction_id"])
                        metadata = analyze_metadata_pages(
                            SourceLineage(**lineage), _pages(connection, row)
                        )
                        payload = metadata_proposal(metadata, row["id"], row["identity_sha256"])
                        _store(connection, row["id"], state, attempts, payload, None)
                    return True
                except DocumentMetadataError, KeyError, TypeError, ValueError:
                    state, error_code = "FAILED", "DOCUMENT_METADATA_SOURCE_INVALID"
                except psycopg.Error:
                    state = "RETRYABLE_FAILED" if attempts < 3 else "FAILED"
                    error_code = "DOCUMENT_METADATA_RETRY"
                _store(connection, row["id"], state, attempts, None, error_code)
                return True
        except psycopg.Error:
            return False

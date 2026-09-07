"""Discover retained sources and prepare one locally without files or AI calls."""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.document_structure import DocumentStructureError, plan_structure_chunks
from familycare_worker.document_structure_repository import (
    DocumentStructureRepository,
    StructureScopeError,
)
from familycare_worker.document_structure_source import load_stored_structure
from familycare_worker.jobs import psycopg_database_url

PREPARATION_REVISION = "stored-structure-lines-v2-ch4096-context4096-max16384"


class DocumentPreparationRunner:
    """Use transaction row locks, so process death rolls back all local preparation.

    A durable record prevents hot loops on invalid source data. Successful local
    preparation does not mark any provider range or business knowledge complete.
    """

    def __init__(self, database_url: str, *, batch_item_id: UUID | None = None) -> None:
        self.database_url = psycopg_database_url(database_url)
        self.batch_item_id = batch_item_id

    def run_once(self, worker_id: str) -> bool:
        del worker_id  # A transaction owns local work; no external call needs a lease.
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL statement_timeout = '30s'")
                row = connection.execute(
                    """
                    SELECT item.id AS batch_item_id, batch.household_space_id,
                           batch.family_member_id, extraction.id AS extraction_id,
                           layer.id AS ocr_layer_id
                    FROM document_batch_items item
                    JOIN document_batches batch ON batch.id = item.batch_id
                    JOIN family_members member ON member.id = batch.family_member_id
                      AND member.household_space_id = batch.household_space_id
                      AND member.deleted_at IS NULL
                    JOIN documents document ON document.id = item.document_id
                      AND document.deleted_at IS NULL
                    JOIN LATERAL (
                      SELECT version.id FROM document_versions version
                      WHERE version.document_id = item.document_id
                        AND (item.processed_document_version_id IS NULL
                             OR version.id = item.processed_document_version_id)
                      ORDER BY version.version_number DESC, version.id LIMIT 1
                    ) version ON TRUE
                    JOIN LATERAL (
                      SELECT extraction.id FROM extractions extraction
                      WHERE extraction.document_version_id = version.id
                        AND extraction.status = 'succeeded'
                      ORDER BY extraction.succeeded_at DESC, extraction.id LIMIT 1
                    ) extraction ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT layer.id FROM ocr_layers layer
                      WHERE layer.extraction_id = extraction.id AND layer.status = 'succeeded'
                      ORDER BY layer.created_at DESC, layer.id DESC LIMIT 1
                    ) layer ON TRUE
                    LEFT JOIN document_structure_preparations preparation
                      ON preparation.batch_item_id = item.id
                     AND preparation.extraction_id = extraction.id
                     AND preparation.ocr_layer_id IS NOT DISTINCT FROM layer.id
                     AND preparation.pipeline_revision = %s
                    WHERE item.state = 'succeeded'
                      AND (%s::uuid IS NULL OR item.id = %s)
                      AND (preparation.id IS NULL OR
                        (preparation.state = 'RETRYABLE_FAILED' AND preparation.attempts < 3
                         AND preparation.available_at <= clock_timestamp()))
                    ORDER BY item.created_at, item.id
                    FOR UPDATE OF item SKIP LOCKED LIMIT 1
                    """,
                    (PREPARATION_REVISION, self.batch_item_id, self.batch_item_id),
                ).fetchone()
                if row is None:
                    return False
                identity = (
                    row["batch_item_id"],
                    row["extraction_id"],
                    row["ocr_layer_id"],
                    PREPARATION_REVISION,
                )
                # Refresh after locking: another transaction may have just prepared it.
                previous = connection.execute(
                    """
                    SELECT state, attempts, available_at <= clock_timestamp() AS due
                    FROM document_structure_preparations
                    WHERE batch_item_id = %s AND extraction_id = %s
                      AND ocr_layer_id IS NOT DISTINCT FROM %s AND pipeline_revision = %s
                    """,
                    identity,
                ).fetchone()
                if previous is not None and (
                    previous["state"] != "RETRYABLE_FAILED"
                    or previous["attempts"] >= 3
                    or not previous["due"]
                ):
                    return False
                attempts = 1 if previous is None else previous["attempts"] + 1
                generation_id = None
                error_code = None
                try:
                    with connection.transaction():
                        structure = load_stored_structure(
                            connection,
                            household_space_id=row["household_space_id"],
                            family_member_id=row["family_member_id"],
                            batch_item_id=row["batch_item_id"],
                            expected_extraction_id=row["extraction_id"],
                            expected_ocr_layer=(row["ocr_layer_id"],),
                        )
                        plan = plan_structure_chunks(
                            structure,
                            max_content_chars=4096,
                            max_context_chars=4096,
                            max_chunks=16384,
                        )
                        generation_id = DocumentStructureRepository.prepare_in_transaction(
                            connection,
                            household_space_id=row["household_space_id"],
                            family_member_id=row["family_member_id"],
                            batch_item_id=row["batch_item_id"],
                            structure=structure,
                            plan=plan,
                        )
                        state = "PREPARED" if plan.complete else "PARTIAL"
                except DocumentStructureError, StructureScopeError:
                    state, error_code = "FAILED", "STRUCTURE_SOURCE_INVALID"
                except psycopg.Error:
                    state = "RETRYABLE_FAILED" if attempts < 3 else "FAILED"
                    error_code = "STRUCTURE_PREPARATION_RETRY"
                connection.execute(
                    """
                    INSERT INTO document_structure_preparations (
                      batch_item_id, extraction_id, ocr_layer_id, pipeline_revision,
                      generation_id, state, attempts, error_code, available_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp() + interval '60 seconds')
                    ON CONFLICT (batch_item_id, extraction_id, ocr_layer_id, pipeline_revision)
                    DO UPDATE SET generation_id = EXCLUDED.generation_id, state = EXCLUDED.state,
                      attempts = EXCLUDED.attempts, error_code = EXCLUDED.error_code,
                      available_at = EXCLUDED.available_at, updated_at = clock_timestamp()
                    """,
                    (*identity, generation_id, state, attempts, error_code),
                )
                return True
        except psycopg.Error:
            # Includes unknown commit outcomes; rediscovery checks durable identity.
            return False

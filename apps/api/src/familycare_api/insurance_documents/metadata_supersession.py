"""Refine only untouched program sources, atomically with their current edition."""

from typing import Any
from uuid import uuid4

import psycopg

from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.insurance_documents.metadata_publication_store import (
    record_component_publication,
)
from familycare_api.insurance_documents.metadata_refinement import metadata_refines


class _RefinementDeferred(Exception):
    """A savepoint must preserve the previous usable edition."""


def refine_component(
    connection: psycopg.Connection[dict[str, Any]],
    source: dict[str, Any],
    proposed: dict[str, Any],
    overlaps: list[dict[str, Any]],
) -> bool:
    active = [row for row in overlaps if row["superseded_by_component_id"] is None]
    if len(active) != 1:
        return False
    previous = active[0]
    if (
        previous["document_version_id"] != source["document_version_id"]
        or previous["review_state"] != "PROGRAM_VERIFIED"
        or previous["version"] != 1
        or previous["deleted_at"] is not None
        or previous["metadata_publication_id"] is None
        or not metadata_refines(previous["proof_json"], proposed)
    ):
        return False
    ancestors = connection.execute(
        "WITH RECURSIVE ancestors(id) AS (SELECT %s::uuid UNION "
        "SELECT receipt.predecessor_component_id FROM document_component_supersessions receipt "
        "JOIN ancestors a ON receipt.successor_component_id=a.id) SELECT id FROM ancestors",
        (previous["id"],),
    ).fetchall()
    if {row["id"] for row in overlaps} - {row["id"] for row in ancestors}:
        return False
    if connection.execute(
        "SELECT 1 FROM insurance_document_set_items "
        "WHERE insurance_document_component_id=%s LIMIT 1",
        (previous["id"],),
    ).fetchone():
        return False
    editions = connection.execute(
        "SELECT e.id,e.version,e.deleted_at,EXISTS(SELECT 1 FROM clauses c "
        "WHERE c.terms_edition_id=e.id) AS has_clauses FROM terms_editions e "
        "WHERE e.source_component_id=%s FOR UPDATE OF e",
        (previous["id"],),
    ).fetchall()
    if any(
        row["version"] != 1 or row["deleted_at"] is not None or row["has_clauses"]
        for row in editions
    ):
        return False
    try:
        with connection.transaction():
            successor_id = uuid4()
            connection.execute(
                "UPDATE insurance_document_components "
                "SET superseded_by_component_id=%s WHERE id=%s",
                (successor_id, previous["id"]),
            )
            publication_id = record_component_publication(
                connection, source, proposed, "APPLIED", component_id=successor_id
            )
            edition_id = None
            if proposed["role"] == "terms":
                ComponentTermsProjector._publish(
                    connection,
                    {
                        "id": successor_id,
                        "household_space_id": source["household_space_id"],
                        "document_version_id": source["document_version_id"],
                        "page_start": proposed["page_start"],
                        "page_end": proposed["page_end"],
                        "proof_json": proposed,
                        "content_sha256": source["content_sha256"],
                    },
                )
                registration = connection.execute(
                    "SELECT terms_edition_id FROM component_terms_publications "
                    "WHERE component_id=%s",
                    (successor_id,),
                ).fetchone()
                edition_id = registration["terms_edition_id"] if registration else None
            if editions and edition_id is None:
                raise _RefinementDeferred
            connection.execute(
                "INSERT INTO document_component_supersessions(predecessor_component_id,"
                "successor_component_id,predecessor_publication_id,successor_publication_id,"
                "successor_terms_edition_id,revision) "
                "VALUES(%s,%s,%s,%s,%s,'metadata-refinement-v1')",
                (
                    previous["id"],
                    successor_id,
                    previous["metadata_publication_id"],
                    publication_id,
                    edition_id,
                ),
            )
        return True
    except _RefinementDeferred:
        return False

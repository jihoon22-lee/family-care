"""Store one metadata decision with reciprocal immutable component provenance."""

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb


def record_component_publication(
    connection: psycopg.Connection[dict[str, Any]],
    source: dict[str, Any],
    component: dict[str, Any],
    outcome: str,
    *,
    component_id: UUID | None = None,
) -> UUID:
    publication_id = uuid4()
    if outcome == "APPLIED":
        component_id = component_id or uuid4()
    elif component_id is not None:
        raise ValueError("non-applied component publication")
    connection.execute(
        "INSERT INTO document_metadata_publications(id,proposal_id,component_identity,"
        "validator_revision,outcome,component_id,proof_json) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            publication_id,
            source["proposal_id"],
            component["identity"],
            source["validator_revision"],
            outcome,
            component_id,
            Jsonb(component),
        ),
    )
    if component_id is not None:
        connection.execute(
            "INSERT INTO insurance_document_components(id,household_space_id,"
            "family_member_id,document_batch_item_id,document_version_id,role,page_start,"
            "page_end,review_state,metadata_publication_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'PROGRAM_VERIFIED',%s)",
            (
                component_id,
                source["household_space_id"],
                source["family_member_id"],
                source["batch_item_id"],
                source["document_version_id"],
                component["role"],
                component["page_start"],
                component["page_end"],
                publication_id,
            ),
        )
    return publication_id

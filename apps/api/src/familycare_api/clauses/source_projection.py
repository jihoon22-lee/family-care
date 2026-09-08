"""Attach retained page completeness to a scoped Clause source projection."""

from typing import Any
from uuid import UUID

import psycopg

from familycare_api.policies.source_projection import StructureProjectionReader


class ClauseSourceProjectionReader:
    """Read immutable manifests; component ownership/currentness remains with the caller."""

    def __init__(self, connection: psycopg.Connection[dict[str, Any]], household_id: UUID) -> None:
        self.connection = connection
        self.household_id = household_id
        self.nodes = StructureProjectionReader(connection, household_id)

    def read(self, generation_id: UUID, pages: tuple[int, ...]) -> dict[str, Any] | None:
        if (
            not isinstance(pages, tuple)
            or not 1 <= len(pages) <= 25
            or any(type(page) is not int or not 1 <= page <= 500 for page in pages)
        ):
            raise ValueError("CLAUSE_SOURCE_PAGE_REQUEST_INVALID")
        requested = tuple(sorted(set(pages)))
        source = self.nodes.read(generation_id, requested)
        if source is None:
            return None
        row = self.connection.execute(
            """
            SELECT CASE WHEN g.structure_json->>'storage_layout'='page-v1' THEN (
                SELECT jsonb_agg(p.payload_json->'page' ORDER BY p.part_number)
                FROM document_structure_page_payloads p WHERE p.generation_id=g.id
                  AND p.part_number=ANY(%(pages)s)
              ) ELSE (
                SELECT jsonb_agg(page ORDER BY (page->>'page_number')::integer)
                FROM jsonb_array_elements(g.structure_json->'pages') page
                WHERE (page->>'page_number')::integer=ANY(%(pages)s)
              ) END AS pages,
              coalesce((SELECT jsonb_agg(item) FROM
                jsonb_array_elements(g.structure_json->'unresolved') item
                WHERE (item->>'page_number')::integer=ANY(%(pages)s)), '[]'::jsonb) AS unresolved
            FROM document_structure_generations g WHERE g.id=%(generation)s
              AND g.household_space_id=%(household)s
              AND jsonb_typeof(g.structure_json->'unresolved')='array'
            """,
            {"generation": generation_id, "household": self.household_id, "pages": list(requested)},
        ).fetchone()
        if (
            row is None
            or not isinstance(row["pages"], list)
            or tuple(page.get("page_number") for page in row["pages"]) != requested
            or not isinstance(row["unresolved"], list)
        ):
            return None
        return {**source, "pages": row["pages"], "unresolved": row["unresolved"]}

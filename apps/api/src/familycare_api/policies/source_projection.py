"""Read one scoped page projection at a time from either retained storage layout."""

from typing import Any
from uuid import UUID

import psycopg


class StructureProjectionReader:
    def __init__(self, connection: psycopg.Connection[dict[str, Any]], household_id: UUID) -> None:
        self.connection = connection
        self.household_id = household_id
        self.key: tuple[UUID, tuple[int, ...]] | None = None
        self.value: dict[str, Any] | None = None

    def read(self, generation_id: UUID, pages: tuple[int, ...]) -> dict[str, Any] | None:
        key = generation_id, tuple(sorted(set(pages)))
        if key != self.key:
            row = self.connection.execute(
                "SELECT document_structure_projection(%s,%s,%s) AS structure",
                (generation_id, self.household_id, list(key[1])),
            ).fetchone()
            self.key = key
            self.value = None if row is None else row["structure"]
        return self.value

"""Serialize source changes across imports of the same document bytes."""

from typing import Any
from uuid import UUID

import psycopg


def lock_document_content(
    connection: psycopg.Connection[dict[str, Any]],
    household_id: UUID,
    content_sha256: str,
) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s || ':' || %s,0))",
        (str(household_id), content_sha256),
    )

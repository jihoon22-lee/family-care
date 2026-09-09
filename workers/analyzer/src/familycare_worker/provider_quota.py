"""All paid reservations count, including requests with an unknown outcome."""

from typing import Any
from uuid import UUID

import psycopg


def request_counts(connection: psycopg.Connection[Any], document_id: UUID) -> tuple[int, int]:
    """Caller holds the shared reservation lock (523019,28).

    A review request is one HTTP reservation regardless of its document count.
    Every included document consumes its own document quota exactly once.
    """
    row = connection.execute(
        "SELECT sum(document_requests)::bigint AS document_requests, "
        "sum(daily_requests)::bigint AS daily_requests FROM ("
        "SELECT count(*) FILTER(WHERE document_id=%s) AS document_requests, "
        "count(*) FILTER(WHERE reserved_at >= date_trunc('day',clock_timestamp() "
        "AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS daily_requests "
        "FROM policy_provider_requests UNION ALL "
        "SELECT count(*) FILTER(WHERE %s=ANY(document_ids)), "
        "count(*) FILTER(WHERE reserved_at >= date_trunc('day',clock_timestamp() "
        "AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') FROM guidance_review_requests) counted",
        (document_id, document_id),
    ).fetchone()
    assert row is not None
    if isinstance(row, dict):
        return int(row["document_requests"]), int(row["daily_requests"])
    return int(row[0]), int(row[1])

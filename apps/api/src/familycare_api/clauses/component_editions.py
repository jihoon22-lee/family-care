"""Register bounded terms sources; registration does not establish applicability."""

import unicodedata
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.clauses.normalization import NORMALIZATION_VERSION
from familycare_api.clauses.repository import _database_url, _lock_terms_content

REVISION = "component-terms-v1"


def _scalar(proof: dict[str, Any], name: str) -> str | None:
    if name in proof["conflicting_fields"] or name in proof["unresolved_fields"]:
        return None
    values = [fact["value"] for fact in proof["facts"] if fact["field"] == name]
    return values[0] if len({_key(value) for value in values}) == 1 else None


def _key(value: str) -> str:
    # Same display-key revision as the program enrollment projector.
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _date(proof: dict[str, Any], name: str) -> date | None:
    value = _scalar(proof, name)
    return date.fromisoformat(value) if value else None


class ComponentTermsProjector:
    """Backfill already published components and retain every publication decision."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def project_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] | None = None
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("invalid component edition projection limit")
        completed = 0
        for _ in range(limit):
            if stop_requested and stop_requested():
                break
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL statement_timeout='30s'")
                source = self._source(connection)
                if source is None:
                    break
                _lock_terms_content(
                    connection, source["household_space_id"], source["content_sha256"]
                )
                source = self._source(connection, component_id=source["id"])
                if source is None:
                    continue
                self._publish(connection, source)
                completed += 1
        return completed

    @staticmethod
    def _source(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        component_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        lock = "FOR UPDATE OF c,d,m SKIP LOCKED" if component_id is not None else ""
        return connection.execute(
            """
            SELECT c.id,c.household_space_id,c.document_version_id,c.page_start,c.page_end,
              p.proof_json,v.content_sha256
            FROM insurance_document_components c
            JOIN document_metadata_publications p ON p.id=c.metadata_publication_id
              AND p.component_id=c.id AND p.outcome='APPLIED'
            JOIN document_versions v ON v.id=c.document_version_id
            JOIN documents d ON d.id=v.document_id
            JOIN family_members m ON m.id=c.family_member_id
              AND m.household_space_id=c.household_space_id
            WHERE c.role='terms' AND c.review_state='PROGRAM_VERIFIED'
              AND c.deleted_at IS NULL AND c.superseded_by_component_id IS NULL
              AND d.deleted_at IS NULL AND m.deleted_at IS NULL
              AND (%s::uuid IS NULL OR c.id=%s)
              AND NOT EXISTS (SELECT 1 FROM component_terms_publications published
                WHERE published.component_id=c.id AND published.revision=%s)
            ORDER BY c.created_at,c.id
            """
            + lock
            + " LIMIT 1",
            (component_id, component_id, REVISION),
        ).fetchone()

    @staticmethod
    def _publish(connection: psycopg.Connection[dict[str, Any]], source: dict[str, Any]) -> None:
        _lock_terms_content(connection, source["household_space_id"], source["content_sha256"])
        proof = source["proof_json"]
        insurer = _scalar(proof, "insurer")
        product = _scalar(proof, "product_name") or _scalar(proof, "product_code")
        reason = "IDENTITY_METADATA_INCOMPLETE"
        edition_id = None
        # Legacy manual editions may cover the whole file. Preserve their decisions,
        # including tombstones, instead of silently installing competing editions.
        if connection.execute(
            "SELECT 1 FROM terms_editions WHERE household_space_id=%s "
            "AND content_sha256=%s AND source_component_id IS NULL LIMIT 1",
            (source["household_space_id"], source["content_sha256"]),
        ).fetchone():
            reason = "LEGACY_EDITION_EXISTS"
        elif (
            insurer
            and product
            and _key(insurer)
            and _key(product)
            and len(insurer) <= 160
            and len(product) <= 200
        ):
            row = connection.execute(
                """
                INSERT INTO terms_editions(household_space_id,document_version_id,
                  insurer_display,insurer_key,product_display,product_key,
                  applicability_start,applicability_end,content_sha256,normalization_version,
                  source_component_id,source_page_start,source_page_end,edition_date,source_metadata_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
                """,
                (
                    source["household_space_id"],
                    source["document_version_id"],
                    insurer,
                    _key(insurer)[:160],
                    product,
                    _key(product)[:200],
                    _date(proof, "applicability_start"),
                    _date(proof, "applicability_end"),
                    source["content_sha256"],
                    NORMALIZATION_VERSION,
                    source["id"],
                    source["page_start"],
                    source["page_end"],
                    _date(proof, "edition_date"),
                    Jsonb(proof),
                ),
            ).fetchone()
            assert row is not None
            edition_id = row["id"]
            reason = "SOURCE_REGISTERED"
        connection.execute(
            "INSERT INTO component_terms_publications("
            "component_id,revision,outcome,reason_code,terms_edition_id) "
            "VALUES(%s,%s,%s,%s,%s)",
            (source["id"], REVISION, "APPLIED" if edition_id else "DEFERRED", reason, edition_id),
        )

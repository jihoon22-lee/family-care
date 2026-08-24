"""Direct-psycopg persistence and PostgreSQL Clause search."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.clauses.domain import (
    Clause,
    ClauseSearchFilters,
    ClauseSearchHit,
    ClauseType,
    TermsEdition,
)
from familycare_api.clauses.errors import (
    ClauseEvidenceInvalid,
    ClauseRepositoryUnavailable,
    ClauseStateConflict,
    ClauseVersionConflict,
    TermsEditionNotFound,
)
from familycare_api.clauses.normalization import NORMALIZATION_VERSION
from familycare_api.common.evidence import EvidenceBbox, EvidenceRef, EvidenceReviewState
from familycare_api.common.scope import HouseholdScope
from familycare_api.common.versions import require_expected_version


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ClauseRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _terms_edition(row: dict[str, Any]) -> TermsEdition:
    return TermsEdition(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        document_version_id=cast(UUID, row["document_version_id"]),
        insurer_display=cast(str, row["insurer_display"]),
        insurer_key=cast(str, row["insurer_key"]),
        product_display=cast(str, row["product_display"]),
        product_key=cast(str, row["product_key"]),
        applicability_start=cast(date | None, row.get("applicability_start")),
        applicability_end=cast(date | None, row.get("applicability_end")),
        content_sha256=cast(str, row["content_sha256"]),
        normalization_version=cast(str, row["normalization_version"]),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row.get("deleted_at"),
    )


def _evidence(row: dict[str, Any], prefix: str = "evidence") -> EvidenceRef | None:
    evidence_id = row.get(f"{prefix}_id")
    if evidence_id is None:
        return None
    coordinates = tuple(row.get(f"{prefix}_{name}") for name in ("x0", "y0", "x1", "y1"))
    bbox = None if coordinates == (None, None, None, None) else cast(EvidenceBbox, coordinates)
    return EvidenceRef(
        evidence_id=cast(UUID, evidence_id),
        document_version_id=cast(UUID, row[f"{prefix}_document_version_id"]),
        extraction_id=cast(UUID, row[f"{prefix}_extraction_id"]),
        content_sha256=cast(str, row[f"{prefix}_content_sha256"]),
        physical_page=int(row[f"{prefix}_physical_page"]),
        bbox=bbox,
        review_state=cast(EvidenceReviewState, row[f"{prefix}_review_state"]),
    )


_TERMS_COLUMNS = """
    id, household_space_id, document_version_id,
    insurer_display, insurer_key, product_display, product_key,
    applicability_start, applicability_end, content_sha256,
    normalization_version, version, created_at, updated_at, deleted_at
"""


class TermsEditionRepository:
    """Persist Terms editions only after validated terms-document Evidence."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def create(
        self,
        scope: HouseholdScope,
        *,
        source_evidence_id: UUID,
        document_version_id: UUID,
        insurer_display: str,
        insurer_key: str,
        product_display: str,
        product_key: str,
        applicability_start: date | None,
        applicability_end: date | None,
        content_sha256: str,
        normalization_version: str = NORMALIZATION_VERSION,
    ) -> TermsEdition:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    f"""
                    INSERT INTO terms_editions (
                      household_space_id, document_version_id,
                      insurer_display, insurer_key, product_display, product_key,
                      applicability_start, applicability_end, content_sha256,
                      normalization_version
                    )
                    SELECT
                      %(household_space_id)s, version.id,
                      %(insurer_display)s, %(insurer_key)s,
                      %(product_display)s, %(product_key)s,
                      %(applicability_start)s, %(applicability_end)s,
                      CAST(%(content_sha256)s AS varchar(64)),
                      CAST(%(normalization_version)s AS varchar(32))
                    FROM evidence AS source
                    JOIN document_versions AS version
                      ON version.id = source.document_version_id
                    JOIN documents AS document ON document.id = version.document_id
                    JOIN extractions AS extraction
                      ON extraction.id = source.extraction_id
                     AND extraction.document_version_id = version.id
                    JOIN extraction_pages AS page
                      ON page.extraction_id = extraction.id
                     AND page.page_number = source.physical_page
                    WHERE source.id = %(source_evidence_id)s
                      AND source.household_space_id = %(household_space_id)s
                      AND source.document_version_id = %(document_version_id)s
                      AND source.content_sha256 = CAST(%(content_sha256)s AS varchar(64))
                      AND version.content_sha256 = CAST(%(content_sha256)s AS varchar(64))
                      AND document.document_kind = 'terms'
                      AND document.deleted_at IS NULL
                      AND extraction.status = 'succeeded'
                      AND source.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
                    RETURNING {_TERMS_COLUMNS}
                    """,
                    {
                        "household_space_id": scope.household_space_id,
                        "source_evidence_id": source_evidence_id,
                        "document_version_id": document_version_id,
                        "insurer_display": insurer_display,
                        "insurer_key": insurer_key,
                        "product_display": product_display,
                        "product_key": product_key,
                        "applicability_start": applicability_start,
                        "applicability_end": applicability_end,
                        "content_sha256": content_sha256,
                        "normalization_version": normalization_version,
                    },
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ClauseStateConflict from None
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        if row is None:
            raise ClauseEvidenceInvalid
        return _terms_edition(row)

    def list(
        self,
        scope: HouseholdScope,
        *,
        deleted_only: bool = False,
    ) -> tuple[TermsEdition, ...]:
        predicate = "deleted_at IS NOT NULL" if deleted_only else "deleted_at IS NULL"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    f"""
                    SELECT {_TERMS_COLUMNS}
                    FROM terms_editions
                    WHERE household_space_id = %s AND {predicate}
                    ORDER BY insurer_key, product_key,
                             applicability_start NULLS FIRST, id
                    """,
                    (scope.household_space_id,),
                ).fetchall()
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        return tuple(_terms_edition(row) for row in rows)

    def get(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> TermsEdition | None:
        predicate = "deleted_at IS NOT NULL" if deleted_only else "deleted_at IS NULL"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    f"""
                    SELECT {_TERMS_COLUMNS}
                    FROM terms_editions
                    WHERE id = %s AND household_space_id = %s AND {predicate}
                    """,
                    (terms_edition_id, scope.household_space_id),
                ).fetchone()
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        return _terms_edition(row) if row is not None else None

    def soft_delete(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
        *,
        expected_version: int,
    ) -> TermsEdition:
        return self._set_deleted(
            scope,
            terms_edition_id,
            expected_version=expected_version,
            restore=False,
        )

    def restore(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
        *,
        expected_version: int,
    ) -> TermsEdition:
        return self._set_deleted(
            scope,
            terms_edition_id,
            expected_version=expected_version,
            restore=True,
        )

    def _set_deleted(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
        *,
        expected_version: int,
        restore: bool,
    ) -> TermsEdition:
        version = require_expected_version(expected_version)
        source_predicate = "deleted_at IS NOT NULL" if restore else "deleted_at IS NULL"
        target = "NULL" if restore else "clock_timestamp()"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    f"""
                    UPDATE terms_editions
                    SET deleted_at = {target}, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                      AND {source_predicate}
                    RETURNING {_TERMS_COLUMNS}
                    """,
                    (terms_edition_id, scope.household_space_id, version),
                ).fetchone()
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        if row is None:
            raise ClauseVersionConflict
        return _terms_edition(row)


_CLAUSE_COLUMNS = """
    clause.id, clause.household_space_id, clause.terms_edition_id,
    clause.parent_clause_id, clause.clause_type, clause.label,
    clause.normalized_title, clause.normalized_text,
    clause.physical_page_start, clause.physical_page_end,
    clause.normalization_version, clause.version,
    clause.created_at, clause.updated_at, clause.deleted_at
"""

_EVIDENCE_COLUMNS = """
    evidence.id AS evidence_id,
    evidence.document_version_id AS evidence_document_version_id,
    evidence.extraction_id AS evidence_extraction_id,
    evidence.content_sha256 AS evidence_content_sha256,
    evidence.physical_page AS evidence_physical_page,
    evidence.x0 AS evidence_x0, evidence.y0 AS evidence_y0,
    evidence.x1 AS evidence_x1, evidence.y1 AS evidence_y1,
    evidence.review_state AS evidence_review_state
"""


def _clauses_from_rows(rows: Sequence[dict[str, Any]]) -> tuple[Clause, ...]:
    grouped: dict[UUID, tuple[dict[str, Any], list[EvidenceRef]]] = {}
    for row in rows:
        clause_id = cast(UUID, row["id"])
        if clause_id not in grouped:
            grouped[clause_id] = (row, [])
        evidence = _evidence(row)
        if evidence is not None:
            grouped[clause_id][1].append(evidence)
    return tuple(
        Clause(
            id=cast(UUID, row["id"]),
            household_space_id=cast(UUID, row["household_space_id"]),
            terms_edition_id=cast(UUID, row["terms_edition_id"]),
            parent_clause_id=cast(UUID | None, row.get("parent_clause_id")),
            clause_type=cast(ClauseType, row["clause_type"]),
            label=cast(str, row["label"]),
            normalized_title=cast(str, row["normalized_title"]),
            normalized_text=cast(str, row["normalized_text"]),
            physical_page_start=int(row["physical_page_start"]),
            physical_page_end=int(row["physical_page_end"]),
            normalization_version=cast(str, row["normalization_version"]),
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row.get("deleted_at"),
            evidence=tuple(evidence),
        )
        for row, evidence in grouped.values()
    )


class ClauseRepository:
    """Own Clause hierarchy writes, Evidence linkage, and optimistic deletion."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def create(
        self,
        scope: HouseholdScope,
        *,
        terms_edition_id: UUID,
        parent_clause_id: UUID | None,
        clause_type: ClauseType,
        label: str,
        normalized_title: str,
        normalized_text: str,
        physical_page_start: int,
        physical_page_end: int,
        evidence_ids: tuple[UUID, ...],
        normalization_version: str = NORMALIZATION_VERSION,
    ) -> Clause:
        if not evidence_ids:
            raise ClauseEvidenceInvalid
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO clauses (
                      household_space_id, terms_edition_id, parent_clause_id,
                      clause_type, label, normalized_title, normalized_text,
                      physical_page_start, physical_page_end, normalization_version
                    )
                    SELECT
                      edition.household_space_id, edition.id,
                      CAST(%(parent_clause_id)s AS uuid),
                      CAST(%(clause_type)s AS varchar(32)),
                      CAST(%(label)s AS varchar(160)),
                      CAST(%(normalized_title)s AS text),
                      CAST(%(normalized_text)s AS text),
                      %(physical_page_start)s, %(physical_page_end)s,
                      CAST(%(normalization_version)s AS varchar(32))
                    FROM terms_editions AS edition
                    WHERE edition.id = %(terms_edition_id)s
                      AND edition.household_space_id = %(household_space_id)s
                      AND edition.deleted_at IS NULL
                      AND (
                        CAST(%(parent_clause_id)s AS uuid) IS NULL OR EXISTS (
                          SELECT 1 FROM clauses AS parent
                          WHERE parent.id = CAST(%(parent_clause_id)s AS uuid)
                            AND parent.household_space_id = %(household_space_id)s
                            AND parent.terms_edition_id = edition.id
                            AND parent.deleted_at IS NULL
                        )
                      )
                    RETURNING id
                    """,
                    {
                        "household_space_id": scope.household_space_id,
                        "terms_edition_id": terms_edition_id,
                        "parent_clause_id": parent_clause_id,
                        "clause_type": clause_type,
                        "label": label,
                        "normalized_title": normalized_title,
                        "normalized_text": normalized_text,
                        "physical_page_start": physical_page_start,
                        "physical_page_end": physical_page_end,
                        "normalization_version": normalization_version,
                    },
                ).fetchone()
                if row is None:
                    raise TermsEditionNotFound
                clause_id = cast(UUID, row["id"])
                for evidence_id in evidence_ids:
                    linked = connection.execute(
                        """
                        INSERT INTO clause_evidence (clause_id, evidence_id)
                        SELECT %(clause_id)s, evidence.id
                        FROM evidence
                        JOIN terms_editions AS edition
                          ON edition.id = %(terms_edition_id)s
                         AND edition.household_space_id = %(household_space_id)s
                         AND edition.deleted_at IS NULL
                        JOIN extractions AS extraction
                          ON extraction.id = evidence.extraction_id
                         AND extraction.document_version_id = evidence.document_version_id
                        JOIN extraction_pages AS page
                          ON page.extraction_id = extraction.id
                         AND page.page_number = evidence.physical_page
                        WHERE evidence.id = %(evidence_id)s
                          AND evidence.household_space_id = %(household_space_id)s
                          AND evidence.document_version_id = edition.document_version_id
                          AND evidence.content_sha256 = edition.content_sha256
                          AND evidence.physical_page BETWEEN %(page_start)s AND %(page_end)s
                          AND evidence.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
                          AND extraction.status = 'succeeded'
                        RETURNING evidence_id
                        """,
                        {
                            "clause_id": clause_id,
                            "terms_edition_id": terms_edition_id,
                            "household_space_id": scope.household_space_id,
                            "evidence_id": evidence_id,
                            "page_start": physical_page_start,
                            "page_end": physical_page_end,
                        },
                    ).fetchone()
                    if linked is None:
                        raise ClauseEvidenceInvalid
                clauses = self._hierarchy_with_connection(
                    connection,
                    scope,
                    terms_edition_id,
                    clause_id=clause_id,
                )
        except ClauseEvidenceInvalid, TermsEditionNotFound:
            raise
        except psycopg.errors.UniqueViolation:
            raise ClauseStateConflict from None
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        if len(clauses) != 1:
            raise ClauseRepositoryUnavailable
        return clauses[0]

    def get_hierarchy(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
    ) -> tuple[Clause, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                editions = connection.execute(
                    """
                    SELECT 1 FROM terms_editions
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    """,
                    (terms_edition_id, scope.household_space_id),
                ).fetchone()
                if editions is None:
                    raise TermsEditionNotFound
                return self._hierarchy_with_connection(connection, scope, terms_edition_id)
        except TermsEditionNotFound:
            raise
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None

    def _hierarchy_with_connection(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        terms_edition_id: UUID,
        *,
        clause_id: UUID | None = None,
    ) -> tuple[Clause, ...]:
        rows = connection.execute(
            f"""
            SELECT {_CLAUSE_COLUMNS}, {_EVIDENCE_COLUMNS}
            FROM clauses AS clause
            JOIN terms_editions AS edition
              ON edition.id = clause.terms_edition_id
             AND edition.household_space_id = clause.household_space_id
             AND edition.deleted_at IS NULL
            LEFT JOIN clause_evidence AS link ON link.clause_id = clause.id
            LEFT JOIN evidence
              ON evidence.id = link.evidence_id
             AND evidence.household_space_id = clause.household_space_id
             AND evidence.document_version_id = edition.document_version_id
             AND evidence.content_sha256 = edition.content_sha256
             AND evidence.physical_page BETWEEN clause.physical_page_start
                                             AND clause.physical_page_end
             AND evidence.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            WHERE clause.household_space_id = %(household_space_id)s
              AND clause.terms_edition_id = %(terms_edition_id)s
              AND clause.deleted_at IS NULL
              AND (
                CAST(%(clause_id)s AS uuid) IS NULL
                OR clause.id = CAST(%(clause_id)s AS uuid)
              )
            ORDER BY clause.physical_page_start, clause.physical_page_end,
                     clause.created_at, clause.id,
                     evidence.physical_page NULLS LAST, evidence.id NULLS LAST
            """,
            {
                "household_space_id": scope.household_space_id,
                "terms_edition_id": terms_edition_id,
                "clause_id": clause_id,
            },
        ).fetchall()
        return _clauses_from_rows(rows)

    def soft_delete(
        self,
        scope: HouseholdScope,
        clause_id: UUID,
        *,
        expected_version: int,
    ) -> None:
        self._set_deleted(
            scope,
            clause_id,
            expected_version=expected_version,
            restore=False,
        )

    def restore(
        self,
        scope: HouseholdScope,
        clause_id: UUID,
        *,
        expected_version: int,
    ) -> Clause:
        clause = self._set_deleted(
            scope,
            clause_id,
            expected_version=expected_version,
            restore=True,
        )
        if clause is None:
            raise ClauseRepositoryUnavailable
        return clause

    def _set_deleted(
        self,
        scope: HouseholdScope,
        clause_id: UUID,
        *,
        expected_version: int,
        restore: bool,
    ) -> Clause | None:
        version = require_expected_version(expected_version)
        source_predicate = "deleted_at IS NOT NULL" if restore else "deleted_at IS NULL"
        target = "NULL" if restore else "clock_timestamp()"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    f"""
                    UPDATE clauses
                    SET deleted_at = {target}, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                      AND {source_predicate}
                    RETURNING terms_edition_id
                    """,
                    (clause_id, scope.household_space_id, version),
                ).fetchone()
                if row is None:
                    raise ClauseVersionConflict
                clauses = (
                    self._hierarchy_with_connection(
                        connection,
                        scope,
                        cast(UUID, row["terms_edition_id"]),
                        clause_id=clause_id,
                    )
                    if restore
                    else ()
                )
        except ClauseVersionConflict:
            raise
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        if restore:
            if len(clauses) != 1:
                raise ClauseRepositoryUnavailable
            return clauses[0]
        return None


SEARCH_SQL = """
WITH ranked AS (
    SELECT
        c.id AS clause_id,
        c.household_space_id,
        c.label,
        left(c.normalized_text, 320) AS excerpt,
        c.terms_edition_id,
        c.physical_page_start,
        c.physical_page_end,
        c.normalization_version,
        ts_rank_cd(
          c.search_vector,
          plainto_tsquery('simple', %(normalized_query)s)
        ) AS relevance,
        similarity(c.normalized_title, %(normalized_query)s) AS title_similarity,
        t.document_version_id,
        t.content_sha256
    FROM clauses AS c
    JOIN terms_editions AS t
      ON t.id = c.terms_edition_id
     AND t.household_space_id = c.household_space_id
    WHERE c.household_space_id = %(household_space_id)s
      AND c.deleted_at IS NULL
      AND t.deleted_at IS NULL
      AND plainto_tsquery('simple', %(normalized_query)s) @@ c.search_vector
      AND (
        CAST(%(terms_edition_id)s AS uuid) IS NULL
        OR c.terms_edition_id = CAST(%(terms_edition_id)s AS uuid)
      )
      AND (
        CAST(%(effective_on)s AS date) IS NULL OR (
          (t.applicability_start IS NULL
           OR t.applicability_start <= CAST(%(effective_on)s AS date))
          AND (t.applicability_end IS NULL
               OR t.applicability_end >= CAST(%(effective_on)s AS date))
        )
      )
      AND (
        CAST(%(insurer_key)s AS varchar(160)) IS NULL
        OR t.insurer_key = CAST(%(insurer_key)s AS varchar(160))
      )
      AND (
        CAST(%(product_key)s AS varchar(200)) IS NULL
        OR t.product_key = CAST(%(product_key)s AS varchar(200))
      )
    ORDER BY relevance DESC, title_similarity DESC,
             c.physical_page_start, c.id
    LIMIT %(limit)s
)
SELECT
    ranked.*,
    evidence.id AS evidence_id,
    evidence.document_version_id AS evidence_document_version_id,
    evidence.extraction_id AS evidence_extraction_id,
    evidence.content_sha256 AS evidence_content_sha256,
    evidence.physical_page AS evidence_physical_page,
    evidence.x0 AS evidence_x0, evidence.y0 AS evidence_y0,
    evidence.x1 AS evidence_x1, evidence.y1 AS evidence_y1,
    evidence.review_state AS evidence_review_state
FROM ranked
LEFT JOIN clause_evidence AS link ON link.clause_id = ranked.clause_id
LEFT JOIN evidence
  ON evidence.id = link.evidence_id
 AND evidence.household_space_id = ranked.household_space_id
 AND evidence.document_version_id = ranked.document_version_id
 AND evidence.content_sha256 = ranked.content_sha256
 AND evidence.physical_page BETWEEN ranked.physical_page_start
                                 AND ranked.physical_page_end
 AND evidence.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
 AND EXISTS (
   SELECT 1
   FROM extractions AS extraction
   JOIN extraction_pages AS page
     ON page.extraction_id = extraction.id
    AND page.page_number = evidence.physical_page
   WHERE extraction.id = evidence.extraction_id
     AND extraction.document_version_id = evidence.document_version_id
     AND extraction.status = 'succeeded'
 )
ORDER BY relevance DESC, title_similarity DESC,
         physical_page_start, clause_id,
         evidence.physical_page NULLS LAST, evidence.id NULLS LAST
"""


class ClauseSearchRepository:
    """Execute one bound-parameter PostgreSQL FTS/trigram query."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def search(
        self,
        scope: HouseholdScope,
        normalized_query: str,
        filters: ClauseSearchFilters,
        *,
        limit: int,
    ) -> tuple[ClauseSearchHit, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    SEARCH_SQL,
                    {
                        "household_space_id": scope.household_space_id,
                        "normalized_query": normalized_query,
                        "terms_edition_id": filters.terms_edition_id,
                        "effective_on": filters.effective_on,
                        "insurer_key": filters.insurer_key,
                        "product_key": filters.product_key,
                        "limit": limit,
                    },
                ).fetchall()
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None

        grouped: dict[UUID, tuple[dict[str, Any], list[EvidenceRef]]] = {}
        for row in rows:
            clause_id = cast(UUID, row["clause_id"])
            if clause_id not in grouped:
                grouped[clause_id] = (row, [])
            evidence = _evidence(row)
            if evidence is not None:
                grouped[clause_id][1].append(evidence)
        return tuple(
            ClauseSearchHit(
                clause_id=cast(UUID, row["clause_id"]),
                label=cast(str, row["label"]),
                excerpt=cast(str, row["excerpt"]),
                terms_edition_id=cast(UUID, row["terms_edition_id"]),
                physical_page_start=int(row["physical_page_start"]),
                physical_page_end=int(row["physical_page_end"]),
                evidence=tuple(evidence),
                relevance=Decimal(str(row["relevance"])),
                normalization_version=cast(str, row["normalization_version"]),
            )
            for row, evidence in grouped.values()
        )


__all__ = [
    "ClauseRepository",
    "ClauseSearchRepository",
    "SEARCH_SQL",
    "TermsEditionRepository",
]

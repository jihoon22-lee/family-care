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
    CoverageRuleInvalid,
    RiderClauseLinkInvalid,
    TermsEditionNotFound,
)
from familycare_api.clauses.links import (
    CandidateReviewState,
    LinkReviewState,
    RiderClauseLink,
    RiderClauseLinkValidationContext,
    validate_rider_clause_link,
)
from familycare_api.clauses.normalization import NORMALIZATION_VERSION
from familycare_api.clauses.rules import (
    CandidateRuleReviewState,
    CoverageRule,
    CoverageRuleVersion,
    LinkPublicationState,
    RulePublicationContext,
    RuleReviewState,
    RuleStatus,
    validate_publishable_rule,
)
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
                          AND (
                            evidence.x0 IS NULL OR (
                              evidence.x1 <= page.width_points
                              AND evidence.y1 <= page.height_points
                            )
                          )
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
             AND EXISTS (
               SELECT 1
               FROM extractions AS extraction
               JOIN extraction_pages AS page
                 ON page.extraction_id = extraction.id
                AND page.page_number = evidence.physical_page
               WHERE extraction.id = evidence.extraction_id
                 AND extraction.document_version_id = evidence.document_version_id
                 AND extraction.status = 'succeeded'
                 AND (
                   evidence.x0 IS NULL OR (
                     evidence.x1 <= page.width_points
                     AND evidence.y1 <= page.height_points
                   )
                 )
             )
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
        edition_predicate = (
            """
                      AND EXISTS (
                        SELECT 1 FROM terms_editions AS edition
                        WHERE edition.id = clauses.terms_edition_id
                          AND edition.household_space_id = clauses.household_space_id
                          AND edition.deleted_at IS NULL
                      )
            """
            if restore
            else ""
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    f"""
                    UPDATE clauses
                    SET deleted_at = {target}, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                      AND {source_predicate}
                      {edition_predicate}
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
      AND (
        plainto_tsquery('simple', %(normalized_query)s) @@ c.search_vector
        OR (
          c.normalized_title %% %(normalized_query)s
          AND similarity(c.normalized_title, %(normalized_query)s) >= 0.4
        )
      )
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


_LINK_COLUMNS = """
    link.id, link.household_space_id, link.rider_id,
    link.terms_edition_id, link.clause_id, link.candidate_version_id,
    link.review_state, link.applicability_reason_code, link.version,
    link.created_at, link.updated_at, link.deleted_at
"""


def _rider_clause_link(row: dict[str, Any], evidence: Sequence[EvidenceRef]) -> RiderClauseLink:
    return RiderClauseLink(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        rider_id=cast(UUID, row["rider_id"]),
        terms_edition_id=cast(UUID, row["terms_edition_id"]),
        clause_id=cast(UUID, row["clause_id"]),
        candidate_version_id=cast(UUID, row["candidate_version_id"]),
        review_state=cast(LinkReviewState, row["review_state"]),
        applicability_reason_code=cast(str, row["applicability_reason_code"]),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row.get("deleted_at"),
        evidence=tuple(evidence),
    )


class RiderClauseLinkRepository:
    """Persist link transitions after one transaction-local validation snapshot."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def list_for_rider(
        self,
        scope: HouseholdScope,
        rider_id: UUID,
    ) -> tuple[RiderClauseLink, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    f"""
                    SELECT {_LINK_COLUMNS}, {_EVIDENCE_COLUMNS}
                    FROM rider_clause_links AS link
                    JOIN riders AS rider
                      ON rider.id = link.rider_id
                     AND rider.household_space_id = link.household_space_id
                     AND rider.deleted_at IS NULL
                    JOIN policy_contracts AS policy
                      ON policy.id = rider.policy_contract_id
                     AND policy.household_space_id = link.household_space_id
                     AND policy.deleted_at IS NULL
                    LEFT JOIN rider_clause_link_evidence AS linked
                      ON linked.rider_clause_link_id = link.id
                    LEFT JOIN evidence ON evidence.id = linked.evidence_id
                    WHERE link.household_space_id = %s
                      AND link.rider_id = %s
                      AND link.deleted_at IS NULL
                    ORDER BY link.created_at, link.id,
                             evidence.physical_page NULLS LAST,
                             evidence.id NULLS LAST
                    """,
                    (scope.household_space_id, rider_id),
                ).fetchall()
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None

        grouped: dict[UUID, tuple[dict[str, Any], list[EvidenceRef]]] = {}
        for row in rows:
            link_id = cast(UUID, row["id"])
            if link_id not in grouped:
                grouped[link_id] = (row, [])
            evidence = _evidence(row)
            if evidence is not None:
                grouped[link_id][1].append(evidence)
        return tuple(_rider_clause_link(row, evidence) for row, evidence in grouped.values())

    def confirm(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
    ) -> RiderClauseLink:
        version = require_expected_version(expected_version)
        invalid: RiderClauseLinkInvalid | None = None
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                link_row = self._locked_link(
                    connection,
                    scope,
                    link_id,
                    expected_version=version,
                )
                if link_row is None:
                    raise ClauseVersionConflict
                try:
                    context = self._validation_context(connection, scope, link_row)
                    validate_rider_clause_link(scope, context)
                    updated = self._transition(
                        connection,
                        scope,
                        link_id,
                        expected_version=version,
                        review_state="USER_CONFIRMED",
                        reason_code="APPLICABLE",
                    )
                except RiderClauseLinkInvalid as error:
                    invalid = error
                    updated = self._transition(
                        connection,
                        scope,
                        link_id,
                        expected_version=version,
                        review_state="NEEDS_REVIEW",
                        reason_code=error.reason_code,
                    )
        except ClauseVersionConflict:
            raise
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        if invalid is not None:
            raise invalid
        return updated

    def reject(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
        reason_code: str,
    ) -> RiderClauseLink:
        if reason_code not in {
            "USER_REJECTED",
            "WRONG_CLAUSE",
            "WRONG_EDITION",
            "NOT_APPLICABLE",
        }:
            raise RiderClauseLinkInvalid("INVALID_REJECTION_REASON")
        version = require_expected_version(expected_version)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                if (
                    self._locked_link(
                        connection,
                        scope,
                        link_id,
                        expected_version=version,
                    )
                    is None
                ):
                    raise ClauseVersionConflict
                return self._transition(
                    connection,
                    scope,
                    link_id,
                    expected_version=version,
                    review_state="rejected",
                    reason_code=reason_code,
                )
        except ClauseVersionConflict:
            raise
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None

    @staticmethod
    def _locked_link(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
    ) -> dict[str, Any] | None:
        return connection.execute(
            f"""
            SELECT {_LINK_COLUMNS}
            FROM rider_clause_links AS link
            WHERE link.id = %s
              AND link.household_space_id = %s
              AND link.version = %s
              AND link.deleted_at IS NULL
            FOR UPDATE
            """,
            (link_id, scope.household_space_id, expected_version),
        ).fetchone()

    def _validation_context(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        link_row: dict[str, Any],
    ) -> RiderClauseLinkValidationContext:
        policy_row = connection.execute(
            """
            SELECT
              policy.id AS policy_contract_id,
              policy.household_space_id AS policy_household_space_id,
              policy.contract_date, policy.insurer_key AS policy_insurer_key,
              policy.product_key AS policy_product_key,
              policy.source_document_version_id AS policy_document_version_id,
              rider.policy_contract_id AS rider_policy_contract_id,
              document.document_kind AS rider_document_kind,
              source.id AS source_id,
              source.document_version_id AS source_document_version_id,
              source.extraction_id AS source_extraction_id,
              source.content_sha256 AS source_content_sha256,
              source.physical_page AS source_physical_page,
              source.x0 AS source_x0, source.y0 AS source_y0,
              source.x1 AS source_x1, source.y1 AS source_y1,
              source.review_state AS source_review_state,
              version.content_sha256 AS policy_content_sha256,
              version.page_count AS policy_page_count,
              extraction.status AS source_extraction_status,
              page.width_points AS source_page_width,
              page.height_points AS source_page_height
            FROM riders AS rider
            JOIN policy_contracts AS policy ON policy.id = rider.policy_contract_id
            JOIN document_versions AS version
              ON version.id = policy.source_document_version_id
            JOIN documents AS document ON document.id = version.document_id
            JOIN evidence AS source ON source.id = rider.source_evidence_id
            JOIN extractions AS extraction ON extraction.id = source.extraction_id
            JOIN extraction_pages AS page
              ON page.extraction_id = extraction.id
             AND page.page_number = source.physical_page
            WHERE rider.id = %s
              AND rider.household_space_id = %s
              AND policy.household_space_id = %s
              AND source.household_space_id = %s
              AND rider.deleted_at IS NULL
              AND policy.deleted_at IS NULL
              AND document.deleted_at IS NULL
            """,
            (
                link_row["rider_id"],
                scope.household_space_id,
                scope.household_space_id,
                scope.household_space_id,
            ),
        ).fetchone()
        if policy_row is None:
            raise RiderClauseLinkInvalid("LINK_EVIDENCE_INVALID")
        rider_evidence = _evidence(policy_row, "source")
        if rider_evidence is None:
            raise RiderClauseLinkInvalid("LINK_EVIDENCE_INVALID")

        edition_row = connection.execute(
            f"""
            SELECT {_TERMS_COLUMNS}
            FROM terms_editions
            WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
            """,
            (link_row["terms_edition_id"], scope.household_space_id),
        ).fetchone()
        if edition_row is None:
            raise RiderClauseLinkInvalid("TERMS_EDITION_MISMATCH")
        edition = _terms_edition(edition_row)
        clauses = ClauseRepository._hierarchy_with_connection(
            ClauseRepository(self.database_url),
            connection,
            scope,
            edition.id,
            clause_id=cast(UUID, link_row["clause_id"]),
        )
        if len(clauses) != 1:
            raise RiderClauseLinkInvalid("CLAUSE_DOCUMENT_MISMATCH")

        candidate = connection.execute(
            """
            SELECT candidate_kind, aggregate_id, status, issues
            FROM analysis_candidate_versions
            WHERE id = %s AND household_space_id = %s
              AND is_current AND deleted_at IS NULL
            """,
            (link_row["candidate_version_id"], scope.household_space_id),
        ).fetchone()
        if candidate is None:
            raise RiderClauseLinkInvalid("CANDIDATE_DOMAIN_MISMATCH")

        link_evidence, link_evidence_valid = self._link_evidence(
            connection,
            scope,
            cast(UUID, link_row["id"]),
        )
        link = _rider_clause_link(link_row, link_evidence)
        evidence_integrity_valid = link_evidence_valid and self._policy_evidence_valid(
            policy_row, rider_evidence
        )
        issues = candidate.get("issues")
        common_special_conflict = isinstance(issues, list) and any(
            item == "COMMON_SPECIAL_TERMS_CONFLICT"
            or (isinstance(item, dict) and item.get("code") == "COMMON_SPECIAL_TERMS_CONFLICT")
            for item in issues
        )
        return RiderClauseLinkValidationContext(
            link=link,
            policy_contract_id=cast(UUID, policy_row["policy_contract_id"]),
            policy_household_space_id=cast(UUID, policy_row["policy_household_space_id"]),
            contract_date=cast(date | None, policy_row.get("contract_date")),
            policy_insurer_key=cast(str, policy_row["policy_insurer_key"]),
            policy_product_key=cast(str, policy_row["policy_product_key"]),
            policy_document_version_id=cast(UUID, policy_row["policy_document_version_id"]),
            rider_policy_contract_id=cast(UUID, policy_row["rider_policy_contract_id"]),
            rider_document_kind=cast(str, policy_row["rider_document_kind"]),
            rider_source_evidence=rider_evidence,
            terms_edition=edition,
            clause=clauses[0],
            candidate_kind=cast(str, candidate["candidate_kind"]),
            candidate_aggregate_id=cast(UUID | None, candidate.get("aggregate_id")),
            candidate_review_state=cast(CandidateReviewState, candidate["status"]),
            evidence_integrity_valid=evidence_integrity_valid,
            common_special_terms_conflict=common_special_conflict,
        )

    @staticmethod
    def _policy_evidence_valid(row: dict[str, Any], evidence: EvidenceRef) -> bool:
        width = row.get("source_page_width")
        height = row.get("source_page_height")
        return bool(
            row.get("rider_document_kind") == "policy"
            and evidence.document_version_id == row.get("policy_document_version_id")
            and evidence.content_sha256 == row.get("policy_content_sha256")
            and evidence.review_state in {"AI_VERIFIED", "USER_CONFIRMED"}
            and row.get("source_extraction_status") == "succeeded"
            and evidence.physical_page <= int(row.get("policy_page_count") or 0)
            and (
                evidence.bbox is None
                or (
                    isinstance(width, Decimal)
                    and isinstance(height, Decimal)
                    and evidence.bbox[2] <= width
                    and evidence.bbox[3] <= height
                )
            )
        )

    @staticmethod
    def _link_evidence(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        link_id: UUID,
    ) -> tuple[tuple[EvidenceRef, ...], bool]:
        rows = connection.execute(
            """
            SELECT
              evidence.id AS evidence_id,
              evidence.document_version_id AS evidence_document_version_id,
              evidence.extraction_id AS evidence_extraction_id,
              evidence.content_sha256 AS evidence_content_sha256,
              evidence.physical_page AS evidence_physical_page,
              evidence.x0 AS evidence_x0, evidence.y0 AS evidence_y0,
              evidence.x1 AS evidence_x1, evidence.y1 AS evidence_y1,
              evidence.review_state AS evidence_review_state,
              version.content_sha256 AS document_content_sha256,
              version.page_count AS document_page_count,
              extraction.status AS extraction_status,
              extraction.document_version_id AS extraction_document_version_id,
              page.width_points, page.height_points
            FROM rider_clause_link_evidence AS linked
            JOIN evidence ON evidence.id = linked.evidence_id
            JOIN document_versions AS version
              ON version.id = evidence.document_version_id
            JOIN extractions AS extraction ON extraction.id = evidence.extraction_id
            JOIN extraction_pages AS page
              ON page.extraction_id = extraction.id
             AND page.page_number = evidence.physical_page
            WHERE linked.rider_clause_link_id = %s
              AND evidence.household_space_id = %s
            ORDER BY evidence.physical_page, evidence.id
            """,
            (link_id, scope.household_space_id),
        ).fetchall()
        evidence: list[EvidenceRef] = []
        valid = bool(rows)
        for row in rows:
            item = _evidence(row)
            if item is None:
                valid = False
                continue
            width = row.get("width_points")
            height = row.get("height_points")
            valid = valid and bool(
                item.content_sha256 == row.get("document_content_sha256")
                and item.document_version_id == row.get("extraction_document_version_id")
                and row.get("extraction_status") == "succeeded"
                and item.physical_page <= int(row.get("document_page_count") or 0)
                and item.review_state in {"AI_VERIFIED", "USER_CONFIRMED"}
                and (
                    item.bbox is None
                    or (
                        isinstance(width, Decimal)
                        and isinstance(height, Decimal)
                        and item.bbox[2] <= width
                        and item.bbox[3] <= height
                    )
                )
            )
            evidence.append(item)
        return tuple(evidence), valid

    @staticmethod
    def _transition(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
        review_state: LinkReviewState,
        reason_code: str,
    ) -> RiderClauseLink:
        row = connection.execute(
            f"""
            UPDATE rider_clause_links AS link
            SET review_state = %s,
                applicability_reason_code = %s,
                version = version + 1,
                updated_at = clock_timestamp()
            WHERE link.id = %s
              AND link.household_space_id = %s
              AND link.version = %s
              AND link.deleted_at IS NULL
            RETURNING {_LINK_COLUMNS}
            """,
            (
                review_state,
                reason_code,
                link_id,
                scope.household_space_id,
                expected_version,
            ),
        ).fetchone()
        if row is None:
            raise ClauseVersionConflict
        evidence, _ = RiderClauseLinkRepository._link_evidence(connection, scope, link_id)
        return _rider_clause_link(row, evidence)


_RULE_COLUMNS = """
    rule.id, rule.household_space_id, rule.rider_clause_link_id,
    rule.rule_key, rule.current_status, rule.version,
    rule.created_at, rule.updated_at, rule.deleted_at
"""

_RULE_VERSION_COLUMNS = """
    version.id, version.coverage_rule_id, version.candidate_version_id,
    version.version_number, version.schema_version, version.rule_kind,
    version.required, version.input_field_paths, version.expression_json,
    version.result_reason_code, version.review_state, version.executable,
    version.generator_version, version.verifier_version,
    version.created_at, version.published_at
"""


def _coverage_rule(row: dict[str, Any]) -> CoverageRule:
    return CoverageRule(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        rider_clause_link_id=cast(UUID, row["rider_clause_link_id"]),
        rule_key=cast(str, row["rule_key"]),
        current_status=cast(RuleStatus, row["current_status"]),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row.get("deleted_at"),
    )


def _coverage_rule_version(
    row: dict[str, Any], evidence: Sequence[EvidenceRef]
) -> CoverageRuleVersion:
    input_fields = row.get("input_field_paths")
    rule_document = row.get("expression_json")
    if not isinstance(input_fields, list) or not isinstance(rule_document, dict):
        raise ClauseRepositoryUnavailable
    return CoverageRuleVersion(
        id=cast(UUID, row["id"]),
        coverage_rule_id=cast(UUID, row["coverage_rule_id"]),
        candidate_version_id=cast(UUID, row["candidate_version_id"]),
        version_number=int(row["version_number"]),
        schema_version=cast(str, row["schema_version"]),
        rule_kind=cast(Any, row["rule_kind"]),
        required=bool(row["required"]),
        input_field_paths=tuple(cast(list[str], input_fields)),
        rule_document=cast(dict[str, object], rule_document),
        result_reason_code=cast(str, row["result_reason_code"]),
        review_state=cast(RuleReviewState, row["review_state"]),
        executable=bool(row["executable"]),
        generator_version=cast(str, row["generator_version"]),
        verifier_version=cast(str, row["verifier_version"]),
        created_at=row["created_at"],
        published_at=row.get("published_at"),
        evidence=tuple(evidence),
    )


class CoverageRuleRepository:
    """Publish a revalidated stored rule candidate in one SQL transaction."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def list_versions(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
    ) -> tuple[CoverageRuleVersion, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    f"""
                    SELECT {_RULE_VERSION_COLUMNS}, {_EVIDENCE_COLUMNS}
                    FROM coverage_rule_versions AS version
                    JOIN coverage_rules AS rule
                      ON rule.id = version.coverage_rule_id
                     AND rule.household_space_id = %s
                     AND rule.deleted_at IS NULL
                    LEFT JOIN coverage_rule_evidence AS linked
                      ON linked.coverage_rule_version_id = version.id
                    LEFT JOIN evidence ON evidence.id = linked.evidence_id
                    WHERE version.coverage_rule_id = %s
                    ORDER BY version.version_number,
                             evidence.physical_page NULLS LAST,
                             evidence.id NULLS LAST
                    """,
                    (scope.household_space_id, rule_id),
                ).fetchall()
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None
        return self._versions_from_rows(rows)

    def publish(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
    ) -> CoverageRuleVersion:
        aggregate_version = require_expected_version(expected_version)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rule_row = connection.execute(
                    f"""
                    SELECT {_RULE_COLUMNS}
                    FROM coverage_rules AS rule
                    WHERE rule.id = %s
                      AND rule.household_space_id = %s
                      AND rule.version = %s
                      AND rule.deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (rule_id, scope.household_space_id, aggregate_version),
                ).fetchone()
                if rule_row is None:
                    raise ClauseVersionConflict
                rule = _coverage_rule(rule_row)

                version_row = connection.execute(
                    f"""
                    SELECT {_RULE_VERSION_COLUMNS}
                    FROM coverage_rule_versions AS version
                    WHERE version.id = %s AND version.coverage_rule_id = %s
                    FOR UPDATE
                    """,
                    (version_id, rule.id),
                ).fetchone()
                if version_row is None:
                    raise ClauseVersionConflict
                version_evidence, version_evidence_valid = self._rule_evidence(
                    connection, scope, version_id
                )
                candidate_version = _coverage_rule_version(version_row, version_evidence)

                link_row = connection.execute(
                    f"""
                    SELECT {_LINK_COLUMNS}
                    FROM rider_clause_links AS link
                    WHERE link.id = %s
                      AND link.household_space_id = %s
                      AND link.deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (rule.rider_clause_link_id, scope.household_space_id),
                ).fetchone()
                if link_row is None:
                    raise CoverageRuleInvalid("RIDER_CLAUSE_LINK_NOT_APPROVED")
                link_repository = RiderClauseLinkRepository(self.database_url)
                link_context = link_repository._validation_context(connection, scope, link_row)
                try:
                    validate_rider_clause_link(scope, link_context)
                except RiderClauseLinkInvalid:
                    raise CoverageRuleInvalid("RIDER_CLAUSE_LINK_NOT_APPROVED") from None

                candidate = connection.execute(
                    """
                    SELECT candidate_kind, aggregate_id, status, is_current
                    FROM analysis_candidate_versions
                    WHERE id = %s AND household_space_id = %s
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (candidate_version.candidate_version_id, scope.household_space_id),
                ).fetchone()
                if candidate is None:
                    raise CoverageRuleInvalid("RULE_CANDIDATE_MISMATCH")
                candidate_evidence_ids, candidate_evidence_valid = self._candidate_evidence_ids(
                    connection,
                    scope,
                    candidate_version.candidate_version_id,
                )
                latest_version = connection.execute(
                    """
                    SELECT max(version_number) AS version_number
                    FROM coverage_rule_versions
                    WHERE coverage_rule_id = %s
                    """,
                    (rule.id,),
                ).fetchone()
                is_latest = bool(
                    latest_version
                    and int(latest_version["version_number"]) == candidate_version.version_number
                )
                context = RulePublicationContext(
                    rule=rule,
                    candidate_version=candidate_version,
                    link_id=cast(UUID, link_row["id"]),
                    link_review_state=cast(LinkPublicationState, link_row["review_state"]),
                    link_evidence_ids=frozenset(
                        item.evidence_id for item in link_context.link.evidence
                    ),
                    candidate_kind=cast(str, candidate["candidate_kind"]),
                    candidate_aggregate_id=cast(UUID | None, candidate.get("aggregate_id")),
                    candidate_review_state=cast(CandidateRuleReviewState, candidate["status"]),
                    candidate_is_current=bool(candidate["is_current"]) and is_latest,
                    candidate_evidence_ids=candidate_evidence_ids,
                    evidence_integrity_valid=(version_evidence_valid and candidate_evidence_valid),
                )
                validate_publishable_rule(scope, context)
                published_row = connection.execute(
                    f"""
                    INSERT INTO coverage_rule_versions AS version (
                      coverage_rule_id, candidate_version_id, version_number,
                      schema_version, rule_kind, required, input_field_paths,
                      expression_json, result_reason_code, review_state,
                      executable, generator_version, verifier_version,
                      published_at
                    )
                    SELECT
                      version.coverage_rule_id, version.candidate_version_id,
                      version.version_number + 1, version.schema_version,
                      version.rule_kind, version.required,
                      version.input_field_paths, version.expression_json,
                      version.result_reason_code, version.review_state,
                      true, version.generator_version, version.verifier_version,
                      clock_timestamp()
                    FROM coverage_rule_versions AS version
                    WHERE version.id = %s
                    RETURNING {_RULE_VERSION_COLUMNS}
                    """,
                    (version_id,),
                ).fetchone()
                if published_row is None:
                    raise ClauseRepositoryUnavailable
                published_id = cast(UUID, published_row["id"])
                connection.execute(
                    """
                    INSERT INTO coverage_rule_evidence (
                      coverage_rule_version_id, evidence_id
                    )
                    SELECT %s, evidence_id
                    FROM coverage_rule_evidence
                    WHERE coverage_rule_version_id = %s
                    """,
                    (published_id, version_id),
                )
                updated_rule = connection.execute(
                    """
                    UPDATE coverage_rules
                    SET current_status = 'published', version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                      AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (rule.id, scope.household_space_id, aggregate_version),
                ).fetchone()
                if updated_rule is None:
                    raise ClauseVersionConflict
                published_evidence, published_evidence_valid = self._rule_evidence(
                    connection, scope, published_id
                )
                if not published_evidence_valid:
                    raise ClauseRepositoryUnavailable
                return _coverage_rule_version(
                    published_row,
                    published_evidence,
                )
        except CoverageRuleInvalid, ClauseVersionConflict:
            raise
        except psycopg.errors.UniqueViolation:
            raise ClauseVersionConflict from None
        except psycopg.Error:
            raise ClauseRepositoryUnavailable from None

    @staticmethod
    def _versions_from_rows(
        rows: Sequence[dict[str, Any]],
    ) -> tuple[CoverageRuleVersion, ...]:
        grouped: dict[UUID, tuple[dict[str, Any], list[EvidenceRef]]] = {}
        for row in rows:
            version_id = cast(UUID, row["id"])
            if version_id not in grouped:
                grouped[version_id] = (row, [])
            evidence = _evidence(row)
            if evidence is not None:
                grouped[version_id][1].append(evidence)
        return tuple(_coverage_rule_version(row, evidence) for row, evidence in grouped.values())

    @staticmethod
    def _rule_evidence(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        version_id: UUID,
    ) -> tuple[tuple[EvidenceRef, ...], bool]:
        rows = connection.execute(
            """
            SELECT
              evidence.id AS evidence_id,
              evidence.document_version_id AS evidence_document_version_id,
              evidence.extraction_id AS evidence_extraction_id,
              evidence.content_sha256 AS evidence_content_sha256,
              evidence.physical_page AS evidence_physical_page,
              evidence.x0 AS evidence_x0, evidence.y0 AS evidence_y0,
              evidence.x1 AS evidence_x1, evidence.y1 AS evidence_y1,
              evidence.review_state AS evidence_review_state,
              document_version.content_sha256 AS document_content_sha256,
              document_version.page_count AS document_page_count,
              extraction.document_version_id AS extraction_document_version_id,
              extraction.status AS extraction_status,
              page.width_points, page.height_points
            FROM coverage_rule_evidence AS linked
            JOIN evidence ON evidence.id = linked.evidence_id
            JOIN document_versions AS document_version
              ON document_version.id = evidence.document_version_id
            JOIN extractions AS extraction ON extraction.id = evidence.extraction_id
            JOIN extraction_pages AS page
              ON page.extraction_id = extraction.id
             AND page.page_number = evidence.physical_page
            WHERE linked.coverage_rule_version_id = %s
              AND evidence.household_space_id = %s
            ORDER BY evidence.physical_page, evidence.id
            """,
            (version_id, scope.household_space_id),
        ).fetchall()
        return CoverageRuleRepository._validated_evidence_rows(rows)

    @staticmethod
    def _candidate_evidence_ids(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        candidate_version_id: UUID,
    ) -> tuple[frozenset[UUID], bool]:
        rows = connection.execute(
            """
            SELECT
              candidate.document_version_id AS candidate_document_version_id,
              candidate.physical_page AS candidate_physical_page,
              candidate.x0 AS candidate_x0, candidate.y0 AS candidate_y0,
              candidate.x1 AS candidate_x1, candidate.y1 AS candidate_y1,
              evidence.id AS evidence_id,
              evidence.document_version_id AS evidence_document_version_id,
              evidence.extraction_id AS evidence_extraction_id,
              evidence.content_sha256 AS evidence_content_sha256,
              evidence.physical_page AS evidence_physical_page,
              evidence.x0 AS evidence_x0, evidence.y0 AS evidence_y0,
              evidence.x1 AS evidence_x1, evidence.y1 AS evidence_y1,
              evidence.review_state AS evidence_review_state,
              version.content_sha256 AS document_content_sha256,
              version.page_count AS document_page_count,
              extraction.document_version_id AS extraction_document_version_id,
              extraction.status AS extraction_status,
              page.width_points, page.height_points
            FROM analysis_candidate_evidence AS candidate
            JOIN analysis_candidate_versions AS candidate_version
              ON candidate_version.id = candidate.candidate_version_id
             AND candidate_version.household_space_id = %s
            JOIN evidence ON evidence.id = candidate.evidence_id
            JOIN document_versions AS version
              ON version.id = evidence.document_version_id
            JOIN extractions AS extraction ON extraction.id = evidence.extraction_id
            JOIN extraction_pages AS page
              ON page.extraction_id = extraction.id
             AND page.page_number = evidence.physical_page
            WHERE candidate.candidate_version_id = %s
              AND evidence.household_space_id = %s
            ORDER BY evidence.physical_page, evidence.id
            """,
            (
                scope.household_space_id,
                candidate_version_id,
                scope.household_space_id,
            ),
        ).fetchall()
        evidence, valid = CoverageRuleRepository._validated_evidence_rows(rows)
        for row, item in zip(rows, evidence, strict=False):
            valid = valid and bool(
                row.get("candidate_document_version_id") == item.document_version_id
                and row.get("candidate_physical_page") == item.physical_page
                and (
                    row.get("candidate_x0"),
                    row.get("candidate_y0"),
                    row.get("candidate_x1"),
                    row.get("candidate_y1"),
                )
                == (
                    row.get("evidence_x0"),
                    row.get("evidence_y0"),
                    row.get("evidence_x1"),
                    row.get("evidence_y1"),
                )
            )
        return frozenset(item.evidence_id for item in evidence), valid

    @staticmethod
    def _validated_evidence_rows(
        rows: Sequence[dict[str, Any]],
    ) -> tuple[tuple[EvidenceRef, ...], bool]:
        evidence: list[EvidenceRef] = []
        valid = bool(rows)
        for row in rows:
            item = _evidence(row)
            if item is None:
                valid = False
                continue
            width = row.get("width_points")
            height = row.get("height_points")
            valid = valid and bool(
                item.content_sha256 == row.get("document_content_sha256")
                and item.document_version_id == row.get("extraction_document_version_id")
                and row.get("extraction_status") == "succeeded"
                and item.physical_page <= int(row.get("document_page_count") or 0)
                and item.review_state in {"AI_VERIFIED", "USER_CONFIRMED"}
                and (
                    item.bbox is None
                    or (
                        isinstance(width, Decimal)
                        and isinstance(height, Decimal)
                        and item.bbox[2] <= width
                        and item.bbox[3] <= height
                    )
                )
            )
            evidence.append(item)
        return tuple(evidence), valid


__all__ = [
    "ClauseRepository",
    "ClauseSearchRepository",
    "CoverageRuleRepository",
    "RiderClauseLinkRepository",
    "SEARCH_SQL",
    "TermsEditionRepository",
]

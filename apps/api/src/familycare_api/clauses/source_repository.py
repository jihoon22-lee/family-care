"""Bind a stored Clause to one complete original region without editing its text."""

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.clauses.normalization import NORMALIZATION_VERSION, normalize_clause_text
from familycare_api.clauses.source_projection import ClauseSourceProjectionReader
from familycare_api.clauses.source_regions import ClauseSourceRegion, observe_clause_source_regions
from familycare_api.insurance_documents.repository import _database_url

logger = logging.getLogger(__name__)


def _context(
    connection: psycopg.Connection[dict[str, Any]], clause_id: UUID, household: UUID
) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT context,context::text AS context_json,"
        "encode(sha256(convert_to(context::text,'UTF8')),'hex') AS digest "
        "FROM (SELECT clause_source_input_context(%s,%s) AS context) input",
        (clause_id, household),
    ).fetchone()


def _evidence_allows(
    region: ClauseSourceRegion, evidence: list[dict[str, Any]], source: dict[str, Any]
) -> bool:
    if not evidence or any(
        e["document_version_id"] != str(source["document_version_id"])
        or e["extraction_id"] != str(source["extraction_id"])
        or e["content_sha256"] != source["content_sha256"]
        or e["review_state"] not in ("AI_VERIFIED", "USER_CONFIRMED")
        or e["extraction_status"] != "succeeded"
        or e["document_deleted_at"] is not None
        for e in evidence
    ):
        return False
    return all(
        any(
            e["physical_page"] == span.page_number
            and (
                e["x0"] is None
                or (
                    e["x0"] <= span.bbox[0]
                    and e["y0"] <= span.bbox[1]
                    and e["x1"] >= span.bbox[2]
                    and e["y1"] >= span.bbox[3]
                )
            )
            for e in evidence
        )
        for span in (region.heading, *region.body, *region.table_context)
    )


def _observe_clause(
    connection: psycopg.Connection[dict[str, Any]],
    clause: dict[str, Any],
    household: UUID,
    *,
    expected_context: str,
) -> tuple[dict[str, Any] | None, ClauseSourceRegion | None, str]:
    clause_id = clause["id"]
    source = connection.execute(
        "SELECT s.*,g.extraction_id,clause_source_input_context(c.id,c.household_space_id)::text "
        "AS observed_context_json,to_jsonb(c) AS observed_clause,"
        "(SELECT jsonb_agg(to_jsonb(item) ORDER BY item.id) FROM ("
        "SELECT ev.*,x.status AS extraction_status,d.deleted_at AS document_deleted_at "
        "FROM clause_evidence ce JOIN evidence ev ON ev.id=ce.evidence_id "
        "JOIN extractions x ON x.id=ev.extraction_id "
        "JOIN document_versions v ON v.id=ev.document_version_id "
        "JOIN documents d ON d.id=v.document_id WHERE ce.clause_id=c.id "
        "AND ev.household_space_id=c.household_space_id ORDER BY ev.id LIMIT 129) item) "
        "AS observed_evidence FROM clauses c JOIN terms_editions e "
        "ON e.id=c.terms_edition_id AND e.household_space_id=c.household_space_id "
        "JOIN terms_applicability_component_sources s ON s.id=e.source_component_id "
        "AND s.household_space_id=e.household_space_id AND s.role='terms' "
        "JOIN document_structure_generations g ON g.id=s.generation_id "
        "WHERE c.id=%s AND c.household_space_id=%s AND c.deleted_at IS NULL "
        "AND e.deleted_at IS NULL AND terms_edition_allows_pages(e.id,e.household_space_id,"
        "c.physical_page_start,c.physical_page_end)",
        (clause_id, household),
    ).fetchone()
    # All mutable inputs and their context use one statement snapshot. Subsequent
    # projection reads address an immutable generation, preventing ABA evidence reads.
    if source is not None and source["observed_context_json"] != expected_context:
        return None, None, "CLAUSE_SOURCE_INPUTS_CHANGED"
    if source is not None:
        clause = source["observed_clause"]
    region = None
    reason = "CLAUSE_SOURCE_NOT_READY"
    if source is not None:
        if (
            clause["clause_type"] != "article"
            or clause["physical_page_start"] != clause["physical_page_end"]
        ):
            reason = "CLAUSE_SOURCE_SCOPE_UNSUPPORTED"
        elif clause["normalization_version"] != NORMALIZATION_VERSION:
            reason = "CLAUSE_SOURCE_NORMALIZATION_UNSUPPORTED"
        else:
            projection = ClauseSourceProjectionReader(connection, household).read(
                source["generation_id"],
                (clause["physical_page_start"],),
            )
            if projection is not None:
                observed = observe_clause_source_regions(
                    projection,
                    clause["physical_page_start"],
                    component_page_end=source["page_end"],
                )
                evidence = source["observed_evidence"] or []
                # Normalized text selects candidates, never a physical identity.
                # The retained result carries complete raw spans and their digest.
                matches = [
                    r
                    for r in observed.regions
                    if r.complete
                    and r.label == clause["label"]
                    and normalize_clause_text(r.body_text) == clause["normalized_text"]
                    and len(evidence) <= 128
                    and _evidence_allows(r, evidence, source)
                ]
                region = matches[0] if len(matches) == 1 else None
                reason = (
                    "CLAUSE_SOURCE_VERIFIED"
                    if region is not None
                    else "CLAUSE_SOURCE_BODY_UNRESOLVED"
                )
    return source, region, reason


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedClauseSource:
    assessment_id: UUID
    clause_id: UUID
    terms_edition_id: UUID
    input_digest: str
    region: ClauseSourceRegion


def read_verified_clause_source(
    connection: psycopg.Connection[dict[str, Any]], household: UUID, clause_id: UUID
) -> VerifiedClauseSource | None:
    """Replay the complete source; stored MATCH and substring proofs are not authority."""
    before = _context(connection, clause_id, household)
    row = connection.execute(
        "SELECT * FROM current_clause_source_assessments WHERE clause_id=%s "
        "AND household_space_id=%s AND status='MATCH'",
        (clause_id, household),
    ).fetchone()
    if row is None or before is None or row["input_digest"] != before["digest"]:
        return None
    clause = connection.execute(
        "SELECT * FROM clauses WHERE id=%s AND household_space_id=%s AND deleted_at IS NULL",
        (clause_id, household),
    ).fetchone()
    if clause is None:
        return None
    source, region, _ = _observe_clause(
        connection, clause, household, expected_context=before["context_json"]
    )
    if (
        source is None
        or region is None
        or any(
            (
                row["terms_edition_id"] != clause["terms_edition_id"],
                row["source_component_id"] != source["id"],
                row["source_publication_id"] != source["metadata_publication_id"],
                row["source_generation_id"] != source["generation_id"],
                row["source_content_sha256"] != source["content_sha256"],
                row["source_region"] != json.loads(json.dumps(asdict(region))),
                _context(connection, clause_id, household) != before,
            )
        )
    ):
        return None
    return VerifiedClauseSource(
        row["id"], clause_id, clause["terms_edition_id"], row["input_digest"], region
    )


class ClauseSourceProjector:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def refresh_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] | None = None
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("CLAUSE_SOURCE_LIMIT_INVALID")
        if stop_requested is not None and stop_requested():
            return 0
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            pending = connection.execute(
                "SELECT c.id,c.household_space_id FROM clauses c JOIN terms_editions e "
                "ON e.id=c.terms_edition_id AND e.household_space_id=c.household_space_id "
                "WHERE c.deleted_at IS NULL AND e.deleted_at IS NULL "
                "AND e.source_component_id IS NOT NULL AND NOT EXISTS("
                "SELECT 1 FROM current_clause_source_assessments a WHERE a.clause_id=c.id) "
                "ORDER BY c.created_at,c.id LIMIT %s",
                (limit,),
            ).fetchall()
        changed = 0
        for item in pending:
            if stop_requested is not None and stop_requested():
                break
            try:
                with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                    changed += int(
                        self._refresh(connection, item["id"], item["household_space_id"])
                    )
            except psycopg.Error, ValueError, TypeError, KeyError:
                logger.warning(
                    "clause source assessment failed",
                    extra={"reason_code": "CLAUSE_SOURCE_ASSESSMENT_FAILED"},
                )
        return changed

    def _refresh(
        self, connection: psycopg.Connection[dict[str, Any]], clause_id: UUID, household: UUID
    ) -> bool:
        before = _context(connection, clause_id, household)
        if before is None or before["context"] is None:
            return False
        clause = connection.execute(
            "SELECT * FROM clauses WHERE id=%s AND household_space_id=%s AND deleted_at IS NULL",
            (clause_id, household),
        ).fetchone()
        if clause is None:
            return False
        source, region, reason = _observe_clause(
            connection, clause, household, expected_context=before["context_json"]
        )
        if reason == "CLAUSE_SOURCE_INPUTS_CHANGED":
            return False
        if _context(connection, clause_id, household) != before:
            return False
        result = connection.execute(
            "INSERT INTO clause_source_assessments(household_space_id,clause_id,clause_version,"
            "terms_edition_id,source_component_id,source_publication_id,source_generation_id,"
            "source_content_sha256,source_region,status,reason_codes,revision,"
            "input_context,input_digest) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'clause-source-v1',%s::jsonb,%s) "
            "ON CONFLICT(clause_id,revision,input_digest) DO NOTHING",
            (
                household,
                clause_id,
                clause["version"],
                clause["terms_edition_id"],
                source["id"] if source else None,
                source["metadata_publication_id"] if source else None,
                source["generation_id"] if source else None,
                source["content_sha256"] if source else None,
                Jsonb(asdict(region)) if region else None,
                "MATCH" if region else "UNKNOWN",
                Jsonb([reason]),
                before["context_json"],
                before["digest"],
            ),
        )
        return result.rowcount == 1

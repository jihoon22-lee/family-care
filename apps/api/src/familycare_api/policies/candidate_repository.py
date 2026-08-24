"""Direct-psycopg persistence for immutable policy-candidate versions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.candidate_errors import (
    CandidateRepositoryUnavailable,
    CandidateVersionConflict,
    InvalidCandidateCorrection,
    ReviewItemNotFound,
)
from familycare_api.policies.candidate_models import (
    CandidateCorrectionRequest,
    CandidateEvidenceRef,
    CandidateField,
    PolicyReviewItem,
    ReviewIssue,
)

_ISSUE_CODES = {
    "MISSING_EVIDENCE",
    "CONFLICTING_EVIDENCE",
    "TERMS_ONLY_RIDER",
    "UNSUPPORTED_STRUCTURE",
    "LOW_CONFIDENCE",
    "INVALID_UNIT",
    "INVALID_DATE",
}
_VISIBLE_STATUSES = {"NEEDS_REVIEW", "AI_VERIFIED", "USER_CONFIRMED"}
_KEY_PARTS = re.compile(r"[^a-z0-9]+")


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _normalized_key(value: str) -> str:
    normalized = _KEY_PARTS.sub("-", value.casefold()).strip("-")
    return normalized[:200] or "unknown"


def _bounded_issue(code: str) -> str:
    return code if code in _ISSUE_CODES else "UNSUPPORTED_STRUCTURE"


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidCandidateCorrection
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidCandidateCorrection from None


def _validate_date_ranges(values: dict[str, object]) -> None:
    for start_field, end_field in (
        ("contract_start", "contract_end"),
        ("coverage_start", "coverage_end"),
    ):
        start = _as_date(values.get(start_field))
        end = _as_date(values.get(end_field))
        if start is not None and end is not None and end < start:
            raise InvalidCandidateCorrection


def _bbox_from_row(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    values = tuple(row.get(name) for name in ("x0", "y0", "x1", "y1"))
    if values == (None, None, None, None):
        return None
    if any(value is None for value in values):
        raise CandidateRepositoryUnavailable
    present = cast(
        tuple[
            Decimal | float | int,
            Decimal | float | int,
            Decimal | float | int,
            Decimal | float | int,
        ],
        values,
    )
    return cast(tuple[float, float, float, float], tuple(float(value) for value in present))


class CandidateRepository:
    """Store and publish candidates with a household predicate on every query."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def publish(self, result: Any, evidence: Sequence[Any]) -> None:
        """Persist a sanitized Worker result and immediately publish AI-verified facts."""

        evidence_by_id: dict[UUID, Any] = {}
        for item in evidence:
            evidence_id = getattr(item, "evidence_id", None)
            if not isinstance(evidence_id, UUID) or evidence_id in evidence_by_id:
                return
            evidence_by_id[evidence_id] = item
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                for candidate in tuple(getattr(result, "candidates", ())):
                    self._persist_worker_candidate(connection, candidate, evidence_by_id)
        except psycopg.Error:
            raise CandidateRepositoryUnavailable from None

    def _persist_worker_candidate(
        self,
        connection: Connection[dict[str, Any]],
        candidate: Any,
        evidence_by_id: dict[UUID, Any],
    ) -> None:
        fields = tuple(getattr(candidate, "fields", ()))
        if not 1 <= len(fields) <= 15:
            return
        field_ids = [getattr(field, "field_id", None) for field in fields]
        if len(field_ids) != len(set(field_ids)):
            return
        field_evidence_batches = [tuple(getattr(field, "evidence_ids", ())) for field in fields]
        if any(not batch or len(batch) > 16 for batch in field_evidence_batches):
            return
        referenced_ids = {
            evidence_id
            for batch in field_evidence_batches
            for evidence_id in batch
            if isinstance(evidence_id, UUID)
        }
        if (
            not referenced_ids
            or any(len(batch) != len(set(batch)) for batch in field_evidence_batches)
            or any(
                any(not isinstance(item, UUID) for item in batch)
                for batch in field_evidence_batches
            )
            or not referenced_ids <= evidence_by_id.keys()
        ):
            return
        evidence_rows = connection.execute(
            """
            SELECT e.id, e.household_space_id, e.document_version_id,
                   e.physical_page, e.review_state, d.document_kind
            FROM evidence AS e
            JOIN document_versions AS dv ON dv.id = e.document_version_id
            JOIN documents AS d ON d.id = dv.document_id
            WHERE e.id = ANY(%s) AND d.deleted_at IS NULL
            """,
            (list(referenced_ids),),
        ).fetchall()
        if len(evidence_rows) != len(referenced_ids):
            return
        households = {cast(UUID, row["household_space_id"]) for row in evidence_rows}
        if len(households) != 1:
            return
        row_by_id = {cast(UUID, row["id"]): row for row in evidence_rows}
        for evidence_id in referenced_ids:
            stored = row_by_id[evidence_id]
            supplied = evidence_by_id[evidence_id]
            if stored["document_version_id"] != getattr(
                supplied, "document_version_id", None
            ) or stored["physical_page"] != getattr(supplied, "page", None):
                return

        household_space_id = next(iter(households))
        candidate_kind = getattr(candidate, "candidate_kind", None)
        status = getattr(candidate, "status", None)
        if candidate_kind not in {"policy_contract", "policy_party", "rider"} or status not in {
            "AI_VERIFIED",
            "NEEDS_REVIEW",
            "USER_CONFIRMED",
            "rejected",
        }:
            return
        issue_codes = [_bounded_issue(str(code)) for code in getattr(candidate, "issue_codes", ())]
        if candidate_kind == "rider" and all(
            row_by_id[evidence_id]["document_kind"] == "terms" for evidence_id in referenced_ids
        ):
            issue_codes.append("TERMS_ONLY_RIDER")
            status = "NEEDS_REVIEW"
        if status == "AI_VERIFIED" and any(
            row_by_id[evidence_id]["review_state"] == "NEEDS_REVIEW"
            for evidence_id in referenced_ids
        ):
            status = "NEEDS_REVIEW"
            issue_codes.append("MISSING_EVIDENCE")
        issue_codes = list(dict.fromkeys(issue_codes))[:8]

        aggregate_id: UUID | None = uuid4() if candidate_kind == "policy_contract" else None
        if candidate_kind == "rider":
            document_ids = {row_by_id[item]["document_version_id"] for item in referenced_ids}
            if len(document_ids) == 1:
                aggregate = connection.execute(
                    """
                    SELECT id FROM policy_contracts
                    WHERE household_space_id = %s AND source_document_version_id = %s
                      AND deleted_at IS NULL
                    ORDER BY created_at, id LIMIT 1
                    """,
                    (household_space_id, next(iter(document_ids))),
                ).fetchone()
                if aggregate is not None:
                    aggregate_id = cast(UUID, aggregate["id"])

        version_id = uuid4()
        review_item_id = uuid4()
        request_ids = tuple(getattr(candidate, "provider_request_ids", ()))
        provider_request_id = request_ids[-1] if request_ids else None
        connection.execute(
            """
            INSERT INTO analysis_candidate_versions (
                id, review_item_id, household_space_id, candidate_kind, aggregate_id,
                version, is_current, status, schema_version, generator_version,
                verifier_version, provider_request_id, issues
            ) VALUES (%s, %s, %s, %s, %s, 1, true, %s, '1',
                      'policy-structurer-v1', 'policy-verifier-v1', %s, %s::jsonb)
            """,
            (
                version_id,
                review_item_id,
                household_space_id,
                candidate_kind,
                aggregate_id,
                status,
                provider_request_id,
                Jsonb([{"code": code, "field_id": None} for code in issue_codes]),
            ),
        )
        for position, field in enumerate(fields):
            field_id = cast(str, field.field_id)
            field_evidence_ids = tuple(getattr(field, "evidence_ids", ()))
            connection.execute(
                """
                INSERT INTO analysis_candidate_fields (
                    candidate_version_id, field_id, position, value
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    version_id,
                    field_id,
                    position,
                    Jsonb(getattr(field, "value", None)),
                ),
            )
            for evidence_id in field_evidence_ids:
                supplied = evidence_by_id[evidence_id]
                bbox = getattr(supplied, "bbox", None)
                coordinates = tuple(bbox) if bbox is not None else (None, None, None, None)
                connection.execute(
                    """
                    INSERT INTO analysis_candidate_evidence (
                        candidate_version_id, field_id, document_version_id, evidence_id,
                        physical_page, bounded_excerpt, x0, y0, x1, y1
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        version_id,
                        field_id,
                        supplied.document_version_id,
                        evidence_id,
                        supplied.page,
                        supplied.text,
                        *coordinates,
                    ),
                )
        if status == "AI_VERIFIED" and not self._publish_projection(
            connection, household_space_id, version_id
        ):
            review_issues = list(dict.fromkeys([*issue_codes, "UNSUPPORTED_STRUCTURE"]))[:8]
            connection.execute(
                """
                UPDATE analysis_candidate_versions
                SET status = 'NEEDS_REVIEW', issues = %s::jsonb,
                    updated_at = clock_timestamp()
                WHERE id = %s AND household_space_id = %s
                """,
                (
                    Jsonb([{"code": code, "field_id": None} for code in review_issues]),
                    version_id,
                    household_space_id,
                ),
            )

    def list_review_items(
        self,
        scope: HouseholdScope,
        *,
        status: str = "NEEDS_REVIEW",
        domain: str = "policy",
    ) -> list[PolicyReviewItem]:
        if status not in _VISIBLE_STATUSES or domain not in {
            "policy",
            "rider_clause",
            "coverage_rule",
        }:
            return []
        domain_predicate = (
            "candidate_kind IN ('policy_contract', 'policy_party', 'rider')"
            if domain == "policy"
            else "candidate_kind = %(domain)s"
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM analysis_candidate_versions
                    WHERE household_space_id = %(household_space_id)s
                      AND status = %(status)s
                      AND {domain_predicate}
                      AND is_current AND deleted_at IS NULL
                    ORDER BY created_at, id
                    """,
                    {
                        "household_space_id": scope.household_space_id,
                        "status": status,
                        "domain": domain,
                    },
                ).fetchall()
                return [self._review_item(connection, row) for row in rows]
        except psycopg.Error:
            raise CandidateRepositoryUnavailable from None

    def get_review_item(
        self,
        scope: HouseholdScope,
        review_item_id: UUID,
    ) -> PolicyReviewItem | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT * FROM analysis_candidate_versions
                    WHERE review_item_id = %s AND household_space_id = %s
                      AND is_current AND deleted_at IS NULL
                    """,
                    (review_item_id, scope.household_space_id),
                ).fetchone()
                return self._review_item(connection, row) if row is not None else None
        except psycopg.Error:
            raise CandidateRepositoryUnavailable from None

    def correct_field(
        self,
        scope: HouseholdScope,
        *,
        request: CandidateCorrectionRequest,
        actor_id: UUID | None,
        review_item_id: UUID | None = None,
        policy_id: UUID | None = None,
    ) -> PolicyReviewItem:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._lock_current(
                    connection,
                    scope,
                    review_item_id=review_item_id,
                    policy_id=policy_id,
                    field_id=request.field_id,
                )
                if int(current["version"]) != request.expected_version:
                    raise CandidateVersionConflict
                evidence = connection.execute(
                    """
                    SELECT * FROM analysis_candidate_evidence
                    WHERE candidate_version_id = %s AND evidence_id = %s
                    ORDER BY field_id LIMIT 1
                    """,
                    (current["id"], request.evidence_id),
                ).fetchone()
                field = connection.execute(
                    """
                    SELECT position FROM analysis_candidate_fields
                    WHERE candidate_version_id = %s AND field_id = %s
                    """,
                    (current["id"], request.field_id),
                ).fetchone()
                if evidence is None or field is None:
                    raise InvalidCandidateCorrection
                current_fields = connection.execute(
                    """
                    SELECT field_id, value FROM analysis_candidate_fields
                    WHERE candidate_version_id = %s
                    """,
                    (current["id"],),
                ).fetchall()
                candidate_values = {
                    cast(str, candidate_field["field_id"]): candidate_field["value"]
                    for candidate_field in current_fields
                }
                candidate_values[request.field_id] = request.value
                _validate_date_ranges(candidate_values)
                child_id = self._insert_child(
                    connection,
                    current,
                    status="NEEDS_REVIEW",
                    actor_id=actor_id,
                )
                connection.execute(
                    """
                    INSERT INTO analysis_candidate_fields (
                        candidate_version_id, field_id, position, value
                    )
                    SELECT %s, field_id, position, value FROM analysis_candidate_fields
                    WHERE candidate_version_id = %s AND field_id <> %s
                    """,
                    (child_id, current["id"], request.field_id),
                )
                connection.execute(
                    """
                    INSERT INTO analysis_candidate_fields (
                        candidate_version_id, field_id, position, value
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (
                        child_id,
                        request.field_id,
                        field["position"],
                        Jsonb(request.value),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO analysis_candidate_evidence (
                        candidate_version_id, field_id, document_version_id, evidence_id,
                        physical_page, bounded_excerpt, x0, y0, x1, y1
                    )
                    SELECT %s, field_id, document_version_id, evidence_id,
                           physical_page, bounded_excerpt, x0, y0, x1, y1
                    FROM analysis_candidate_evidence
                    WHERE candidate_version_id = %s AND field_id <> %s
                    """,
                    (child_id, current["id"], request.field_id),
                )
                connection.execute(
                    """
                    INSERT INTO analysis_candidate_evidence (
                        candidate_version_id, field_id, document_version_id, evidence_id,
                        physical_page, bounded_excerpt, x0, y0, x1, y1
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        child_id,
                        request.field_id,
                        evidence["document_version_id"],
                        evidence["evidence_id"],
                        evidence["physical_page"],
                        evidence["bounded_excerpt"],
                        evidence["x0"],
                        evidence["y0"],
                        evidence["x1"],
                        evidence["y1"],
                    ),
                )
                child = connection.execute(
                    "SELECT * FROM analysis_candidate_versions WHERE id = %s",
                    (child_id,),
                ).fetchone()
                if child is None:
                    raise CandidateRepositoryUnavailable
                return self._review_item(connection, child)
        except psycopg.Error:
            raise CandidateRepositoryUnavailable from None

    def transition(
        self,
        scope: HouseholdScope,
        review_item_id: UUID,
        *,
        expected_version: int,
        status: str,
        actor_id: UUID | None,
        rejection_reason: str | None = None,
    ) -> PolicyReviewItem:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = self._lock_current(
                    connection,
                    scope,
                    review_item_id=review_item_id,
                )
                if int(current["version"]) != expected_version:
                    raise CandidateVersionConflict
                child_id = self._insert_child(
                    connection,
                    current,
                    status=status,
                    actor_id=actor_id,
                    rejection_reason=rejection_reason,
                    copy_payload=True,
                )
                if status == "USER_CONFIRMED" and current["candidate_kind"] in {
                    "policy_contract",
                    "policy_party",
                    "rider",
                }:
                    published = self._publish_projection(
                        connection,
                        scope.household_space_id,
                        child_id,
                    )
                    issues = current.get("issues") or []
                    terms_only = any(
                        isinstance(issue, dict) and issue.get("code") == "TERMS_ONLY_RIDER"
                        for issue in issues
                    )
                    if not published and not terms_only:
                        raise InvalidCandidateCorrection
                child = connection.execute(
                    "SELECT * FROM analysis_candidate_versions WHERE id = %s",
                    (child_id,),
                ).fetchone()
                if child is None:
                    raise CandidateRepositoryUnavailable
                return self._review_item(connection, child)
        except psycopg.Error:
            raise CandidateRepositoryUnavailable from None

    def _lock_current(
        self,
        connection: Connection[dict[str, Any]],
        scope: HouseholdScope,
        *,
        review_item_id: UUID | None = None,
        policy_id: UUID | None = None,
        field_id: str | None = None,
    ) -> dict[str, Any]:
        if review_item_id is not None:
            row = connection.execute(
                """
                SELECT * FROM analysis_candidate_versions
                WHERE review_item_id = %s AND household_space_id = %s
                  AND is_current AND deleted_at IS NULL
                FOR UPDATE
                """,
                (review_item_id, scope.household_space_id),
            ).fetchone()
        elif policy_id is not None and field_id is not None:
            rows = connection.execute(
                """
                SELECT version.* FROM analysis_candidate_versions AS version
                WHERE version.aggregate_id = %s AND version.household_space_id = %s
                  AND version.is_current AND version.deleted_at IS NULL
                  AND EXISTS (
                      SELECT 1 FROM analysis_candidate_fields AS field
                      WHERE field.candidate_version_id = version.id AND field.field_id = %s
                  )
                FOR UPDATE
                """,
                (policy_id, scope.household_space_id, field_id),
            ).fetchall()
            row = rows[0] if len(rows) == 1 else None
        else:
            row = None
        if row is None:
            raise ReviewItemNotFound
        return row

    def _insert_child(
        self,
        connection: Connection[dict[str, Any]],
        current: dict[str, Any],
        *,
        status: str,
        actor_id: UUID | None,
        rejection_reason: str | None = None,
        copy_payload: bool = False,
    ) -> UUID:
        connection.execute(
            """
            UPDATE analysis_candidate_versions
            SET is_current = false, updated_at = clock_timestamp()
            WHERE id = %s AND is_current
            """,
            (current["id"],),
        )
        child_id = uuid4()
        connection.execute(
            """
            INSERT INTO analysis_candidate_versions (
                id, review_item_id, household_space_id, candidate_kind, aggregate_id,
                parent_version_id, version, is_current, status, schema_version,
                generator_version, verifier_version, provider_request_id,
                rejection_reason, issues, actor_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                child_id,
                current["review_item_id"],
                current["household_space_id"],
                current["candidate_kind"],
                current["aggregate_id"],
                current["id"],
                int(current["version"]) + 1,
                status,
                current["schema_version"],
                current["generator_version"],
                current["verifier_version"],
                current["provider_request_id"],
                rejection_reason,
                Jsonb(current.get("issues") or []),
                actor_id,
            ),
        )
        if copy_payload:
            connection.execute(
                """
                INSERT INTO analysis_candidate_fields (
                    candidate_version_id, field_id, position, value
                )
                SELECT %s, field_id, position, value FROM analysis_candidate_fields
                WHERE candidate_version_id = %s
                """,
                (child_id, current["id"]),
            )
            connection.execute(
                """
                INSERT INTO analysis_candidate_evidence (
                    candidate_version_id, field_id, document_version_id, evidence_id,
                    physical_page, bounded_excerpt, x0, y0, x1, y1
                )
                SELECT %s, field_id, document_version_id, evidence_id,
                       physical_page, bounded_excerpt, x0, y0, x1, y1
                FROM analysis_candidate_evidence WHERE candidate_version_id = %s
                """,
                (child_id, current["id"]),
            )
        return child_id

    def _review_item(
        self,
        connection: Connection[dict[str, Any]],
        row: dict[str, Any],
    ) -> PolicyReviewItem:
        field_rows = connection.execute(
            """
            SELECT field_id, value FROM analysis_candidate_fields
            WHERE candidate_version_id = %s ORDER BY position
            """,
            (row["id"],),
        ).fetchall()
        evidence_rows = connection.execute(
            """
            SELECT ce.*, document.document_kind
            FROM analysis_candidate_evidence AS ce
            JOIN document_versions AS version ON version.id = ce.document_version_id
            JOIN documents AS document ON document.id = version.document_id
            WHERE ce.candidate_version_id = %s
            ORDER BY ce.field_id, ce.evidence_id
            """,
            (row["id"],),
        ).fetchall()
        evidence_ids_by_field: dict[str, list[UUID]] = {}
        unique_evidence: dict[UUID, CandidateEvidenceRef] = {}
        for evidence in evidence_rows:
            evidence_id = cast(UUID, evidence["evidence_id"])
            field_id = cast(str, evidence["field_id"])
            evidence_ids_by_field.setdefault(field_id, []).append(evidence_id)
            unique_evidence.setdefault(
                evidence_id,
                CandidateEvidenceRef(
                    evidence_id=evidence_id,
                    document_version_id=cast(UUID, evidence["document_version_id"]),
                    document_label=(
                        "Policy document"
                        if evidence["document_kind"] == "policy"
                        else "Terms document"
                    ),
                    page=int(evidence["physical_page"]),
                    bbox=_bbox_from_row(evidence),
                    bounded_excerpt=cast(str, evidence["bounded_excerpt"]),
                ),
            )
        fields = tuple(
            CandidateField(
                field_id=cast(Any, field["field_id"]),
                value=field["value"],
                evidence_ids=tuple(evidence_ids_by_field.get(field["field_id"], ())),
            )
            for field in field_rows
        )
        issues: list[ReviewIssue] = []
        for issue in row.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            issues.append(
                ReviewIssue(
                    code=cast(Any, _bounded_issue(str(issue.get("code", "")))),
                    field_id=cast(Any, issue.get("field_id")),
                )
            )
        return PolicyReviewItem(
            review_item_id=cast(UUID, row["review_item_id"]),
            candidate_version_id=cast(UUID, row["id"]),
            aggregate_id=cast(UUID | None, row.get("aggregate_id")),
            candidate_kind=cast(Any, row["candidate_kind"]),
            status=cast(Any, row["status"]),
            fields=fields,
            evidence=tuple(unique_evidence.values()),
            issues=tuple(issues[:8]),
            expected_version=int(row["version"]),
        )

    def _publish_projection(
        self,
        connection: Connection[dict[str, Any]],
        household_space_id: UUID,
        candidate_version_id: UUID,
    ) -> bool:
        version = connection.execute(
            """
            SELECT * FROM analysis_candidate_versions
            WHERE id = %s AND household_space_id = %s
            """,
            (candidate_version_id, household_space_id),
        ).fetchone()
        if version is None or version["status"] not in {"AI_VERIFIED", "USER_CONFIRMED"}:
            return False
        issues = version.get("issues") or []
        if any(
            isinstance(issue, dict) and issue.get("code") == "TERMS_ONLY_RIDER" for issue in issues
        ):
            return False
        field_rows = connection.execute(
            "SELECT field_id, value FROM analysis_candidate_fields WHERE candidate_version_id = %s",
            (candidate_version_id,),
        ).fetchall()
        values = {cast(str, row["field_id"]): row["value"] for row in field_rows}
        evidence = connection.execute(
            """
            SELECT ce.evidence_id, ce.document_version_id, document.document_kind
            FROM analysis_candidate_evidence AS ce
            JOIN document_versions AS dv ON dv.id = ce.document_version_id
            JOIN documents AS document ON document.id = dv.document_id
            WHERE ce.candidate_version_id = %s
            ORDER BY ce.field_id, ce.evidence_id
            LIMIT 1
            """,
            (candidate_version_id,),
        ).fetchone()
        if evidence is None or evidence["document_kind"] != "policy":
            return False
        if version["candidate_kind"] == "policy_contract":
            insurer = values.get("insurer")
            product = values.get("product_name")
            policy_status = values.get("policy_status", "unknown")
            if (
                not isinstance(insurer, str)
                or not insurer
                or not isinstance(product, str)
                or not product
                or not isinstance(version["aggregate_id"], UUID)
                or policy_status not in {"active", "inactive", "expired", "cancelled", "unknown"}
            ):
                return False
            contract_date = _as_date(values.get("contract_start"))
            coverage_end = _as_date(values.get("contract_end"))
            policy = connection.execute(
                """
                INSERT INTO policy_contracts (
                    id, household_space_id, source_document_version_id, source_evidence_id,
                    insurer_display, insurer_key, product_display, product_key,
                    contract_date, coverage_start_date, coverage_end_date,
                    status, status_evidence_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    version["aggregate_id"],
                    household_space_id,
                    evidence["document_version_id"],
                    evidence["evidence_id"],
                    insurer,
                    _normalized_key(insurer),
                    product,
                    _normalized_key(product),
                    contract_date,
                    contract_date,
                    coverage_end,
                    policy_status,
                    evidence["evidence_id"] if policy_status != "unknown" else None,
                ),
            ).fetchone()
            if policy is None:
                return False
            aggregate_id = policy["id"]
        elif version["candidate_kind"] == "rider":
            rider_name = values.get("rider_name")
            rider_key = values.get("rider_key")
            benefit_type = values.get("benefit_type")
            rider_status = values.get("rider_status", "unknown")
            policy = connection.execute(
                """
                SELECT id, source_document_version_id FROM policy_contracts
                WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                """,
                (version["aggregate_id"], household_space_id),
            ).fetchone()
            if (
                policy is None
                or policy["source_document_version_id"] != evidence["document_version_id"]
                or not isinstance(rider_name, str)
                or not rider_name
                or not isinstance(rider_key, str)
                or not rider_key
                or benefit_type not in {"fixed", "indemnity"}
                or rider_status not in {"active", "inactive", "expired", "cancelled", "unknown"}
            ):
                return False
            amount = values.get("sum_assured")
            if isinstance(amount, bool) or (
                amount is not None and not isinstance(amount, int | float)
            ):
                return False
            currency = values.get("currency")
            if currency is not None and (
                not isinstance(currency, str) or len(currency) != 3 or not currency.isupper()
            ):
                return False
            rider = connection.execute(
                """
                INSERT INTO riders (
                    household_space_id, policy_contract_id, source_evidence_id,
                    display_name, normalized_key, benefit_type, insured_amount,
                    currency, coverage_start_date, coverage_end_date, renewable,
                    status, status_evidence_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    household_space_id,
                    policy["id"],
                    evidence["evidence_id"],
                    rider_name,
                    _normalized_key(rider_key),
                    benefit_type,
                    Decimal(str(amount)) if amount is not None else None,
                    currency,
                    _as_date(values.get("coverage_start")),
                    _as_date(values.get("coverage_end")),
                    values.get("renewable"),
                    rider_status,
                    evidence["evidence_id"] if rider_status != "unknown" else None,
                ),
            ).fetchone()
            if rider is None:
                return False
            aggregate_id = policy["id"]
        else:
            return False
        connection.execute(
            """
            UPDATE analysis_candidate_versions
            SET aggregate_id = %s, published_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE id = %s AND household_space_id = %s
            """,
            (aggregate_id, candidate_version_id, household_space_id),
        )
        return True


__all__ = ["CandidateRepository"]

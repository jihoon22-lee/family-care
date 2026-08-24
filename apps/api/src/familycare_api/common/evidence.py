"""Policy Evidence validation and PostgreSQL lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.errors import EvidenceInvalid, PolicyRepositoryUnavailable

EvidenceReviewState = Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]
EvidenceBbox = tuple[Decimal, Decimal, Decimal, Decimal]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_STATES = {"AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"}


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def validate_evidence_page(physical_page: int) -> int:
    if not isinstance(physical_page, int) or isinstance(physical_page, bool) or physical_page < 1:
        raise EvidenceInvalid
    return physical_page


def validate_content_sha256(content_sha256: str) -> str:
    if not isinstance(content_sha256, str) or _SHA256.fullmatch(content_sha256) is None:
        raise EvidenceInvalid
    return content_sha256


def validate_evidence_bbox(bbox: EvidenceBbox | None) -> EvidenceBbox | None:
    if bbox is None:
        return None
    if len(bbox) != 4:
        raise EvidenceInvalid
    x0, y0, x1, y1 = bbox
    if any(not isinstance(value, Decimal) for value in bbox) or x1 <= x0 or y1 <= y0:
        raise EvidenceInvalid
    return bbox


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: UUID
    document_version_id: UUID
    extraction_id: UUID
    content_sha256: str
    physical_page: int
    bbox: EvidenceBbox | None
    review_state: EvidenceReviewState

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, UUID) and value.int != 0
            for value in (self.evidence_id, self.document_version_id, self.extraction_id)
        ):
            raise EvidenceInvalid
        validate_content_sha256(self.content_sha256)
        validate_evidence_page(self.physical_page)
        validate_evidence_bbox(self.bbox)
        if self.review_state not in _REVIEW_STATES:
            raise EvidenceInvalid


class EvidenceRepository:
    """Resolve only internally consistent policy Evidence in one household."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def validate_for_document(
        self,
        scope: HouseholdScope,
        evidence_id: UUID,
        document_version_id: UUID,
    ) -> EvidenceRef:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT
                        evidence.id AS evidence_id,
                        evidence.document_version_id,
                        evidence.extraction_id,
                        evidence.content_sha256 AS evidence_hash,
                        document_version.content_sha256 AS document_hash,
                        extraction.document_version_id AS extraction_document_version_id,
                        evidence.physical_page,
                        evidence.x0,
                        evidence.y0,
                        evidence.x1,
                        evidence.y1,
                        evidence.review_state,
                        document.document_kind
                    FROM evidence
                    JOIN document_versions AS document_version
                      ON document_version.id = evidence.document_version_id
                    JOIN documents AS document
                      ON document.id = document_version.document_id
                    JOIN extractions AS extraction
                      ON extraction.id = evidence.extraction_id
                     AND extraction.document_version_id = evidence.document_version_id
                    WHERE evidence.id = %s
                      AND evidence.household_space_id = %s
                      AND evidence.document_version_id = %s
                      AND document.document_kind = 'policy'
                      AND document.deleted_at IS NULL
                      AND extraction.status = 'succeeded'
                    """,
                    (evidence_id, scope.household_space_id, document_version_id),
                ).fetchone()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise EvidenceInvalid
        return self._from_row(row, document_version_id)

    @staticmethod
    def _from_row(row: dict[str, Any], expected_document_version_id: UUID) -> EvidenceRef:
        if (
            row.get("document_version_id") != expected_document_version_id
            or row.get("extraction_document_version_id") != expected_document_version_id
            or row.get("document_kind") != "policy"
            or row.get("evidence_hash") != row.get("document_hash")
        ):
            raise EvidenceInvalid
        coordinates = (row.get("x0"), row.get("y0"), row.get("x1"), row.get("y1"))
        bbox: EvidenceBbox | None
        if coordinates == (None, None, None, None):
            bbox = None
        elif all(isinstance(value, Decimal) for value in coordinates):
            bbox = cast(EvidenceBbox, coordinates)
        else:
            raise EvidenceInvalid
        try:
            return EvidenceRef(
                evidence_id=cast(UUID, row["evidence_id"]),
                document_version_id=cast(UUID, row["document_version_id"]),
                extraction_id=cast(UUID, row["extraction_id"]),
                content_sha256=cast(str, row["evidence_hash"]),
                physical_page=cast(int, row["physical_page"]),
                bbox=bbox,
                review_state=cast(EvidenceReviewState, row["review_state"]),
            )
        except KeyError, TypeError:
            raise EvidenceInvalid from None


__all__ = [
    "EvidenceBbox",
    "EvidenceRef",
    "EvidenceRepository",
    "EvidenceReviewState",
    "validate_content_sha256",
    "validate_evidence_bbox",
    "validate_evidence_page",
]

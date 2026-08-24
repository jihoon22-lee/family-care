"""Household-scoped Terms catalog and Clause hierarchy use cases."""

from __future__ import annotations

import os
import re
from datetime import date
from uuid import UUID

from familycare_api.clauses.domain import Clause, ClauseType, TermsEdition
from familycare_api.clauses.errors import (
    ClauseEvidenceInvalid,
    ClauseRepositoryUnavailable,
    TermsEditionNotFound,
)
from familycare_api.clauses.normalization import (
    NORMALIZATION_VERSION,
    normalize_clause_text,
)
from familycare_api.clauses.repository import ClauseRepository, TermsEditionRepository
from familycare_api.common.scope import HouseholdScope

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClauseEvidenceInvalid
    return value.strip()


class ClauseCatalogService:
    """Validate catalog inputs before one repository transaction."""

    def __init__(
        self,
        terms_repository: TermsEditionRepository,
        clause_repository: ClauseRepository,
    ) -> None:
        self.terms_repository = terms_repository
        self.clause_repository = clause_repository

    @classmethod
    def from_environment(cls) -> ClauseCatalogService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise ClauseRepositoryUnavailable
        return cls(
            TermsEditionRepository(database_url),
            ClauseRepository(database_url),
        )

    def create_terms_edition(
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
    ) -> TermsEdition:
        if (
            not isinstance(source_evidence_id, UUID)
            or source_evidence_id.int == 0
            or not isinstance(document_version_id, UUID)
            or document_version_id.int == 0
            or not isinstance(content_sha256, str)
            or _SHA256.fullmatch(content_sha256) is None
            or (
                applicability_start is not None
                and applicability_end is not None
                and applicability_end < applicability_start
            )
        ):
            raise ClauseEvidenceInvalid
        return self.terms_repository.create(
            scope,
            source_evidence_id=source_evidence_id,
            document_version_id=document_version_id,
            insurer_display=_required_text(insurer_display),
            insurer_key=_required_text(insurer_key),
            product_display=_required_text(product_display),
            product_key=_required_text(product_key),
            applicability_start=applicability_start,
            applicability_end=applicability_end,
            content_sha256=content_sha256,
            normalization_version=NORMALIZATION_VERSION,
        )

    def list_terms_editions(
        self,
        scope: HouseholdScope,
        *,
        deleted_only: bool = False,
    ) -> tuple[TermsEdition, ...]:
        return self.terms_repository.list(scope, deleted_only=deleted_only)

    def get_terms_edition(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> TermsEdition:
        edition = self.terms_repository.get(
            scope,
            terms_edition_id,
            deleted_only=deleted_only,
        )
        if edition is None:
            raise TermsEditionNotFound
        return edition

    def create_clause(
        self,
        scope: HouseholdScope,
        *,
        terms_edition_id: UUID,
        parent_clause_id: UUID | None,
        clause_type: ClauseType,
        label: str,
        title: str,
        text: str,
        physical_page_start: int,
        physical_page_end: int,
        evidence_ids: tuple[UUID, ...],
    ) -> Clause:
        if (
            not isinstance(terms_edition_id, UUID)
            or terms_edition_id.int == 0
            or (parent_clause_id is not None and not isinstance(parent_clause_id, UUID))
            or isinstance(physical_page_start, bool)
            or not isinstance(physical_page_start, int)
            or isinstance(physical_page_end, bool)
            or not isinstance(physical_page_end, int)
            or physical_page_start < 1
            or physical_page_end < physical_page_start
            or not isinstance(evidence_ids, tuple)
            or not evidence_ids
            or any(not isinstance(item, UUID) or item.int == 0 for item in evidence_ids)
        ):
            raise ClauseEvidenceInvalid
        normalized_title = normalize_clause_text(title)
        normalized_text = normalize_clause_text(text)
        if not normalized_title or not normalized_text:
            raise ClauseEvidenceInvalid
        return self.clause_repository.create(
            scope,
            terms_edition_id=terms_edition_id,
            parent_clause_id=parent_clause_id,
            clause_type=clause_type,
            label=_required_text(label),
            normalized_title=normalized_title,
            normalized_text=normalized_text,
            physical_page_start=physical_page_start,
            physical_page_end=physical_page_end,
            evidence_ids=evidence_ids,
            normalization_version=NORMALIZATION_VERSION,
        )

    def get_clause_hierarchy(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
    ) -> tuple[Clause, ...]:
        self.get_terms_edition(scope, terms_edition_id)
        clauses = self.clause_repository.get_hierarchy(scope, terms_edition_id)
        if any(not clause.evidence for clause in clauses):
            raise ClauseEvidenceInvalid
        return clauses


__all__ = ["ClauseCatalogService"]

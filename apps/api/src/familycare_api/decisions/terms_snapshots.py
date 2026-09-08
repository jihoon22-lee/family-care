"""Bounded JSON codec for immutable, event-specific terms selection metadata."""

import json
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from familycare_api.clauses.terms_change_selection import (
    SelectedTermsEdition,
    TermsEventSelection,
    TermsScopeUncertainty,
    TermsSelectionScope,
    TermsStatus,
)

_Reason = Annotated[str, Field(pattern=r"^[A-Z0-9_]+$", min_length=1, max_length=80)]
_Reasons = Annotated[tuple[_Reason, ...], Field(max_length=96)]
_Relations = Annotated[tuple[UUID, ...], Field(max_length=128)]
_MAX_BYTES = 8 * 1024 * 1024


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Scope(_SnapshotModel):
    household_space_id: UUID
    policy_contract_id: UUID
    family_member_id: UUID
    rider_id: UUID | None
    clause_id: UUID | None


class _Edition(_SnapshotModel):
    edition_id: UUID
    status: TermsStatus
    relation_ids: _Relations
    reason_codes: _Reasons


class _Uncertainty(_SnapshotModel):
    relation_id: UUID
    reason_codes: _Reasons


class _Selection(_SnapshotModel):
    scope: _Scope
    event_date: date | None
    editions: Annotated[tuple[_Edition, ...], Field(max_length=512)]
    applied_relation_ids: _Relations
    uncertain_relation_ids: _Relations
    scope_uncertainties: Annotated[tuple[_Uncertainty, ...], Field(max_length=128)]
    base_assessment_ids: Annotated[tuple[UUID, ...], Field(max_length=512)] = ()
    scope_relation_ids: _Relations = ()

    def domain(self) -> TermsEventSelection:
        if (
            len({e.edition_id for e in self.editions}) != len(self.editions)
            or set(self.applied_relation_ids) & set(self.uncertain_relation_ids)
            or any(
                u.relation_id not in self.uncertain_relation_ids for u in self.scope_uncertainties
            )
        ):
            raise ValueError("TERMS_SNAPSHOT_INVALID")
        return TermsEventSelection(
            TermsSelectionScope(**self.scope.model_dump()),
            self.event_date,
            tuple(SelectedTermsEdition(**e.model_dump()) for e in self.editions),
            self.applied_relation_ids,
            self.uncertain_relation_ids,
            tuple(TermsScopeUncertainty(**u.model_dump()) for u in self.scope_uncertainties),
            self.base_assessment_ids,
            self.scope_relation_ids,
        )


def decode_selections(value: object) -> tuple[TermsEventSelection, ...]:
    """Legacy null has no invented applicability; callers retain the legacy marker."""
    if value is None:
        return ()
    try:
        if not isinstance(value, list) or len(value) > 1024:
            raise ValueError
        if len(json.dumps(value, ensure_ascii=True).encode()) > _MAX_BYTES:
            raise ValueError
        result = tuple(_Selection.model_validate(item).domain() for item in value)
        if len({item.scope for item in result}) != len(result):
            raise ValueError
        return result
    except TypeError, ValueError, ValidationError:
        raise ValueError("TERMS_SNAPSHOT_INVALID") from None


def encode_selections(selections: tuple[TermsEventSelection, ...]) -> list[dict[str, Any]]:
    try:
        result = [
            _Selection.model_validate(asdict(item)).model_dump(mode="json") for item in selections
        ]
        for item in result:
            # Preserve the legacy JSON shape when there is no Clause lineage.
            if not item["scope_relation_ids"]:
                item.pop("scope_relation_ids")
        decode_selections(result)
        return result
    except TypeError, ValueError, ValidationError:
        raise ValueError("TERMS_SNAPSHOT_INVALID") from None

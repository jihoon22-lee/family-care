"""Authenticated and path-free HTTP boundary for private PDF imports."""

from __future__ import annotations

import os
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from familycare_api.documents.batch_service import BatchService, DocumentBatchUnavailable
from familycare_api.documents.generated_batch_contracts import (
    BatchErrorCode,
    BatchItemState,
    BatchState,
    OcrState,
    OcrWarningCode,
)
from familycare_api.documents.import_sources import (
    ImportSourceCatalog,
    normalize_display_label,
)
from familycare_api.errors import ErrorResponse
from familycare_api.identity.context import AuthContext, resolve_auth_context


class ImportSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_label: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[^\u0000-\u001f\u007f-\u009f]+$",
    )
    size_bytes: int = Field(ge=0, le=25 * 1024 * 1024)
    encrypted: bool


class BatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"]
    family_member_id: UUID
    source_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            len(source_id) != 64
            or any(character not in "0123456789abcdef" for character in source_id)
            for source_id in value
        ):
            raise ValueError("invalid source id")
        return value


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    password: str = Field(min_length=1, max_length=8192, repr=False)


class BatchItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_label: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[^\u0000-\u001f\u007f-\u009f]+$",
    )
    state: BatchItemState
    error_code: BatchErrorCode | None
    attempts: int = Field(ge=0, le=20)
    ocr_state: OcrState
    ocr_pages_processed: int = Field(ge=0, le=500)
    ocr_warning_codes: list[OcrWarningCode] = Field(
        max_length=8,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("ocr_warning_codes")
    @classmethod
    def validate_ocr_warning_codes(cls, value: list[OcrWarningCode]) -> list[OcrWarningCode]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate OCR warning code")
        return value


class BatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"]
    batch_id: UUID
    family_member_id: UUID
    state: BatchState
    items: list[BatchItemResponse]


AuthDependency = Annotated[AuthContext, Depends(resolve_auth_context)]


def get_batch_service(_context: AuthDependency) -> BatchService:
    return BatchService.from_environment()


def get_import_source_catalog(_context: AuthDependency) -> ImportSourceCatalog:
    root = os.getenv("FAMILYCARE_IMPORT_ROOT", "")
    if not root:
        raise DocumentBatchUnavailable
    try:
        from pathlib import Path

        return ImportSourceCatalog(Path(root))
    except ValueError:
        raise DocumentBatchUnavailable from None


ServiceDependency = Annotated[BatchService, Depends(get_batch_service)]
CatalogDependency = Annotated[ImportSourceCatalog, Depends(get_import_source_catalog)]

router = APIRouter(prefix="/api/v1", tags=["private document import"])


def _label(value: object) -> str:
    return normalize_display_label(value)


def _source_response(value: object) -> ImportSourceResponse | None:
    if isinstance(value, dict):

        def getter(name: str) -> object:
            return value.get(name)

    else:

        def getter(name: str) -> object:
            return getattr(value, name, None)

    try:
        return ImportSourceResponse.model_validate(
            {
                "source_id": getter("source_id"),
                "display_label": _label(getter("display_label")),
                "size_bytes": getter("size_bytes"),
                "encrypted": getter("encrypted"),
            }
        )
    except TypeError, ValueError:
        return None


@router.get(
    "/document-import-sources",
    response_model=list[ImportSourceResponse],
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def list_import_sources(
    context: AuthDependency,
    catalog: CatalogDependency,
) -> list[ImportSourceResponse]:
    values = catalog.list(context)
    return [item for value in values if (item := _source_response(value)) is not None]


@router.post(
    "/document-batches",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchResponse,
)
def create_batch(
    request: BatchCreateRequest,
    context: AuthDependency,
    service: ServiceDependency,
) -> object:
    return service.create(
        context=context,
        family_member_id=request.family_member_id,
        source_ids=tuple(request.source_ids),
    )


@router.get("/document-batches/{batch_id}", response_model=BatchResponse)
def get_batch(
    batch_id: UUID,
    context: AuthDependency,
    service: ServiceDependency,
) -> object:
    return service.get_status(context=context, batch_id=batch_id)


@router.post(
    "/document-batches/{batch_id}/password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchResponse,
)
def handoff_password(
    batch_id: UUID,
    request: PasswordRequest,
    context: AuthDependency,
    service: ServiceDependency,
) -> object:
    return service.handoff_password(
        context=context,
        batch_id=batch_id,
        password=request.password,
    )


@router.post(
    "/document-batches/{batch_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchResponse,
)
def cancel_batch(
    batch_id: UUID,
    context: AuthDependency,
    service: ServiceDependency,
) -> object:
    return service.cancel(context=context, batch_id=batch_id)


__all__ = ["get_batch_service", "get_import_source_catalog", "router"]

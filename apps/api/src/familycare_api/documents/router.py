"""Explicitly gated HTTP routes for local synthetic document analysis."""

from __future__ import annotations

import re
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from familycare_api.documents.generated_contracts import (
    DocumentIngestionRequest,
    ErrorCode,
    JobState,
)
from familycare_api.documents.service import DocumentAnalysisService
from familycare_api.errors import ErrorResponse

SOURCE_KEY_PATTERN_TEXT = (
    r"^(?!/)(?![A-Za-z]:)(?!.*\\)(?!.*[\r\n])(?!.*(?:^|/)\.\.(?:/|$))[^\u0000]+$"
)
SOURCE_KEY_PATTERN = re.compile(SOURCE_KEY_PATTERN_TEXT)

DocumentKind = Literal["amendment", "application", "claim", "policy", "supporting", "terms"]
TableStrategy = Literal["auto", "lines", "text"]


class ExtractorConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: Literal["quality-v1"]
    quality_rule_version: Literal["quality-v1"]
    table_strategy: TableStrategy


class DocumentAnalysisRequest(BaseModel):
    """Exact v1 request; files and credentials are intentionally absent."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1",
                    "source_key": "synthetic/policy-001.pdf",
                    "document_kind": "policy",
                    "extractor_config": {
                        "profile": "quality-v1",
                        "quality_rule_version": "quality-v1",
                        "table_strategy": "auto",
                    },
                }
            ]
        },
    )

    schema_version: Literal["1"]
    source_key: str = Field(
        min_length=1,
        max_length=512,
        json_schema_extra={"pattern": SOURCE_KEY_PATTERN_TEXT},
    )
    document_kind: DocumentKind
    extractor_config: ExtractorConfigRequest

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        if SOURCE_KEY_PATTERN.fullmatch(value) is None:
            raise ValueError("source key must be relative")
        return value

    def to_contract(self) -> DocumentIngestionRequest:
        return cast(DocumentIngestionRequest, self.model_dump(mode="python"))


class AnalysisAcceptedResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1",
                    "job_id": "00000000-0000-4000-8000-000000000001",
                    "state": "queued",
                    "status_url": ("/api/v1/analysis-jobs/00000000-0000-4000-8000-000000000001"),
                }
            ]
        },
    )

    schema_version: Literal["1"]
    job_id: UUID
    state: Literal["queued"]
    status_url: str


class ExtractionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    cell_count: int = Field(ge=0)


class AnalysisJobStatusResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1",
                    "job_id": "00000000-0000-4000-8000-000000000001",
                    "document_id": "00000000-0000-4000-8000-000000000002",
                    "state": "succeeded",
                    "attempts": 1,
                    "extraction_summary": {
                        "page_count": 1,
                        "block_count": 3,
                        "table_count": 0,
                        "cell_count": 0,
                    },
                }
            ]
        },
    )

    schema_version: Literal["1"]
    job_id: UUID
    document_id: UUID
    state: JobState
    attempts: int = Field(ge=0)
    error_code: ErrorCode | None = None
    extraction_summary: ExtractionSummaryResponse | None = None


def get_document_analysis_service() -> DocumentAnalysisService:
    """Resolve one short-lived service from local process configuration."""

    return DocumentAnalysisService.from_environment()


ServiceDependency = Annotated[DocumentAnalysisService, Depends(get_document_analysis_service)]

router = APIRouter(
    prefix="/api/v1",
    tags=["local synthetic document analysis"],
)

_LOCAL_ONLY_DESCRIPTION = (
    "Local synthetic-only development endpoint. It has no authentication or authorization and "
    "is not production-safe. The API enqueues metadata only and never opens the PDF."
)


@router.post(
    "/documents/analysis",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAcceptedResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Stable sanitized invalid request"},
        503: {"model": ErrorResponse, "description": "Local database unavailable"},
    },
    summary="Enqueue a synthetic document analysis job",
    description=_LOCAL_ONLY_DESCRIPTION,
)
def submit_document_analysis(
    request: DocumentAnalysisRequest,
    service: ServiceDependency,
) -> AnalysisAcceptedResponse:
    submitted = service.submit(request.to_contract())
    return AnalysisAcceptedResponse(
        schema_version="1",
        job_id=submitted.job_id,
        state=submitted.state,
        status_url=f"/api/v1/analysis-jobs/{submitted.job_id}",
    )


@router.get(
    "/analysis-jobs/{job_id}",
    response_model=AnalysisJobStatusResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse, "description": "Analysis job not found"},
        422: {"model": ErrorResponse, "description": "Stable sanitized invalid request"},
        503: {"model": ErrorResponse, "description": "Local database unavailable"},
    },
    summary="Get a synthetic document analysis job",
    description=_LOCAL_ONLY_DESCRIPTION,
)
def get_analysis_job(
    job_id: UUID,
    service: ServiceDependency,
) -> AnalysisJobStatusResponse:
    result = service.get_status(job_id)
    summary = (
        ExtractionSummaryResponse(**result.extraction_summary)
        if result.extraction_summary is not None
        else None
    )
    return AnalysisJobStatusResponse(
        schema_version="1",
        job_id=result.job_id,
        document_id=result.document_id,
        state=result.state,
        attempts=result.attempts,
        error_code=result.error_code,
        extraction_summary=summary,
    )


__all__ = [
    "AnalysisAcceptedResponse",
    "AnalysisJobStatusResponse",
    "DocumentAnalysisRequest",
    "ExtractorConfigRequest",
    "get_document_analysis_service",
    "router",
]

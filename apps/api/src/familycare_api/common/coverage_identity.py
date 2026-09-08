"""Common coverage references and their source-preserving verification snapshot."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CoverageConflictField = Literal["insured_amount", "currency", "display_name"]


class CanonicalCoverageRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["PRIVATE_KNOWLEDGE_COVERAGE", "OPERATIONAL_RIDER"]
    contract_id: UUID
    coverage_id: UUID


class CanonicalCoverageIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1"] = "1"
    ref: CanonicalCoverageRef
    source_refs: tuple[CanonicalCoverageRef, ...] = Field(min_length=2, max_length=2)
    authority: Literal["PROGRAM_VERIFIED_SOURCE_IDENTITY"]
    ledger_version: int = Field(ge=1)
    verification_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    field_conflicts: tuple[CoverageConflictField, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def consistent_sources(self) -> Self:
        if (
            self.ref.kind != "OPERATIONAL_RIDER"
            or self.ref not in self.source_refs
            or {source.kind for source in self.source_refs}
            != {"PRIVATE_KNOWLEDGE_COVERAGE", "OPERATIONAL_RIDER"}
        ):
            raise ValueError("invalid coverage identity sources")
        if len(set(self.field_conflicts)) != len(self.field_conflicts):
            raise ValueError("duplicate coverage identity conflict")
        return self

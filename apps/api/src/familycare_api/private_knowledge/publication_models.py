"""Strict models for private-knowledge-rule-publication.sol-v1."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictStr,
    StringConstraints,
    model_validator,
)

from familycare_api.clauses.dsl import RuleValidationError, validate_field_path


def _private_text(value: str) -> str:
    if "\x00" in value or not value.strip():
        raise ValueError("invalid private text")
    return value


def _event_field_path(value: str) -> str:
    try:
        validate_field_path(value)
    except RuleValidationError:
        raise ValueError("invalid event field path") from None
    return value


ShortText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=240),
    AfterValidator(_private_text),
]
MediumText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=800),
    AfterValidator(_private_text),
]
PrivatePhrase = MediumText
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ReasonCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$"),
]
EventFieldPath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=160),
    AfterValidator(_event_field_path),
]
PositivePage = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
RuleDocument = dict[str, JsonValue]
EvidencePurpose = Literal[
    "ELIGIBILITY",
    "DEFINITION",
    "EXCLUSION",
    "WAITING",
    "REDUCTION",
    "FREQUENCY",
    "AMOUNT",
    "RENEWAL",
    "INDEMNITY",
    "LIMIT",
    "DEDUCTIBLE",
]


class StrictPublicationModel(BaseModel):
    """Reject coercion and undeclared fields at the private package boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PublicationManifestFile(StrictPublicationModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    bytes: Annotated[int, Field(ge=0, le=16 * 1024 * 1024)]
    sha256: Sha256


class PublicationCounts(StrictPublicationModel):
    subject_count: NonNegativeInt
    contract_count: NonNegativeInt
    coverage_count: NonNegativeInt
    disposition_count: NonNegativeInt
    published_disposition_count: NonNegativeInt
    blocked_disposition_count: NonNegativeInt
    not_applicable_disposition_count: NonNegativeInt
    status_interval_count: NonNegativeInt
    fact_normalizer_count: NonNegativeInt
    rule_publication_count: NonNegativeInt
    rule_citation_count: NonNegativeInt
    calculation_publication_count: NonNegativeInt
    calculation_citation_count: NonNegativeInt

    @classmethod
    def zero(cls) -> PublicationCounts:
        return cls(
            subject_count=0,
            contract_count=0,
            coverage_count=0,
            disposition_count=0,
            published_disposition_count=0,
            blocked_disposition_count=0,
            not_applicable_disposition_count=0,
            status_interval_count=0,
            fact_normalizer_count=0,
            rule_publication_count=0,
            rule_citation_count=0,
            calculation_publication_count=0,
            calculation_citation_count=0,
        )


class PublicationManifest(StrictPublicationModel):
    schema_version: Literal["private-knowledge-rule-publication.sol-v1"]
    source_knowledge_package_digest_sha256: Sha256
    source_knowledge_projection_digest_sha256: Sha256
    publisher_version: ShortText
    review_state: Literal["USER_CONFIRMED"]
    counts: PublicationCounts
    files: Annotated[list[PublicationManifestFile], Field(min_length=8, max_length=8)]


class CoverageDispositionRecord(StrictPublicationModel):
    source_subject_key: ShortText
    family_alias: ShortText
    canonical_policy_id: ShortText
    canonical_coverage_id: ShortText
    benefit_type: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    disposition: Literal["PUBLISHED", "BLOCKED", "NOT_APPLICABLE"]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=32)]
    review_state: Literal["USER_CONFIRMED"]


class ContractStatusIntervalRecord(StrictPublicationModel):
    canonical_policy_id: ShortText
    effective_from: date
    effective_through: date
    decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    confirmed_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    authority: Literal["USER_CONFIRMED_EVENT_DATE", "REVIEWED_STATUS_DOCUMENT"]
    reason_code: ReasonCode
    review_state: Literal["USER_CONFIRMED"]

    @model_validator(mode="after")
    def validate_interval(self) -> ContractStatusIntervalRecord:
        if self.effective_through < self.effective_from:
            raise ValueError("invalid interval")
        if self.decision == "MATCH" and self.confirmed_status == "unknown":
            raise ValueError("matched status must be known")
        if self.decision != "MATCH" and self.confirmed_status != "unknown":
            raise ValueError("unmatched status must be unknown")
        return self


class FactNormalizerRecord(StrictPublicationModel):
    normalizer_key: ShortText
    field_path: EventFieldPath
    match_kind: Literal["EXACT_TOKEN_SEQUENCE"]
    phrase: PrivatePhrase
    normalized_value: StrictStr | StrictBool
    priority: Annotated[int, Field(ge=0, le=1000)]
    review_state: Literal["USER_CONFIRMED"]


class RulePublicationRecord(StrictPublicationModel):
    rule_key: ShortText
    canonical_policy_id: ShortText
    canonical_coverage_id: ShortText
    rule_kind: Literal[
        "eligibility",
        "classification",
        "temporal",
        "exclusion",
        "frequency",
        "required_document",
    ]
    schema_version: Literal["coverage-rule-v1"]
    required: bool
    rule_document: RuleDocument
    result_reason_code: ReasonCode
    review_state: Literal["USER_CONFIRMED"]


class PublicationCitationRecord(StrictPublicationModel):
    citation_key: ShortText
    canonical_policy_id: ShortText
    canonical_coverage_id: ShortText
    terms_source_alias: MediumText
    source_section_key: ShortText
    source_clause_index: PositivePage | None
    source_fact_key: ShortText | None
    evidence_purpose: EvidencePurpose
    page_start: PositivePage
    page_end: PositivePage
    source_text_sha256: Sha256

    @model_validator(mode="after")
    def validate_pages(self) -> PublicationCitationRecord:
        if self.page_end < self.page_start:
            raise ValueError("invalid page range")
        return self


class RuleCitationRecord(PublicationCitationRecord):
    rule_key: ShortText


class CalculationPublicationRecord(StrictPublicationModel):
    calculation_key: ShortText
    canonical_policy_id: ShortText
    canonical_coverage_id: ShortText
    calculation_kind: Literal["FIXED", "INDEMNITY"]
    schema_version: Literal["coverage-rule-v1"]
    calculation_document: RuleDocument
    result_reason_code: ReasonCode
    review_state: Literal["USER_CONFIRMED"]


class CalculationCitationRecord(PublicationCitationRecord):
    calculation_key: ShortText

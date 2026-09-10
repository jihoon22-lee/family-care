"""Generated from packages/contracts/schemas; do not edit manually."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

__all__ = [
    "DocumentMetadataComponent",
    "DocumentMetadataFact",
    "DocumentMetadataField",
    "DocumentMetadataProposal",
    "DocumentMetadataRangeEvidence",
    "DocumentMetadataRole",
    "DocumentMetadataSpan",
]


DocumentMetadataField = Literal[
    "amendment_effective_date",
    "applicability_end",
    "applicability_start",
    "contract_date",
    "edition_code",
    "edition_date",
    "edition_reference",
    "insurer",
    "product_code",
    "product_name",
    "rider_code",
    "terms_code",
    "terms_reference",
]


DocumentMetadataRole = Literal[
    "amendment",
    "application",
    "policy",
    "product_explanation",
    "terms",
]


class DocumentMetadataComponent(TypedDict):
    authority: Literal["CONTENT_CLASSIFICATION_ONLY"]
    conflicting_fields: list[DocumentMetadataField]
    facts: list[DocumentMetadataFact]
    identity: str
    page_end: int
    page_start: int
    range_evidence: NotRequired[list[DocumentMetadataRangeEvidence]]
    role: DocumentMetadataRole
    role_spans: list[DocumentMetadataSpan]
    unresolved_fields: list[DocumentMetadataField]


class DocumentMetadataFact(TypedDict):
    field: DocumentMetadataField
    spans: list[DocumentMetadataSpan]
    value: str


class DocumentMetadataProposal(TypedDict):
    components: list[DocumentMetadataComponent]
    generation_id: str
    revision: Literal[
        "document-metadata-v1",
        "document-metadata-v10",
        "document-metadata-v11",
        "document-metadata-v2",
        "document-metadata-v3",
        "document-metadata-v4",
        "document-metadata-v5",
        "document-metadata-v6",
        "document-metadata-v7",
        "document-metadata-v8",
        "document-metadata-v9",
    ]
    schema_version: Literal["1"]
    structure_identity_sha256: str
    unresolved_pages: list[int]


class DocumentMetadataRangeEvidence(TypedDict):
    article_numbers: list[int]
    article_sequence_verified: bool
    basis: Literal[
        "ARTICLE_CONTINUATION", "CONTRACTUAL_PROVISIONS", "FORMAL_METADATA", "TABLE_CONTINUATION"
    ]
    page_number: int
    previous_page: int | None
    role_span_indices: list[int]


class DocumentMetadataSpan(TypedDict):
    anchor_end: int
    anchor_start: int
    end: int
    node_id: str
    page_number: int
    start: int
    text: str


METADATA_ROLE_TITLES: dict[str, tuple[str, ...]] = {
    "policy": (
        "보험증권",
        "보험가입증서",
        "policy certificate",
        "insurance certificate",
    ),
    "terms": (
        "보험약관",
        "보통약관",
        "특별약관",
        "약관",
        "policy terms",
        "insurance terms",
    ),
    "product_explanation": (
        "상품설명서",
        "보험상품설명서",
        "product explanation",
    ),
    "application": (
        "보험청약서",
        "청약서",
        "insurance application",
    ),
    "amendment": (
        "계약변경서",
        "보험계약변경서",
        "policy amendment",
    ),
}


METADATA_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "insurer": (
        "보험사",
        "보험회사",
        "insurer",
    ),
    "product_name": (
        "상품명",
        "보험상품명",
        "product name",
    ),
    "product_code": (
        "상품코드",
        "보험상품코드",
        "product code",
    ),
    "rider_code": (
        "특약코드",
        "담보코드",
        "rider code",
    ),
    "terms_code": (
        "약관코드",
        "terms code",
    ),
    "edition_code": (
        "판본코드",
        "판본번호",
        "edition code",
    ),
    "edition_date": (
        "판본일",
        "판본일자",
        "약관개정일",
        "edition date",
    ),
    "applicability_start": (
        "적용시작일",
        "적용개시일",
        "applicable from",
    ),
    "applicability_end": (
        "적용종료일",
        "applicable through",
    ),
    "amendment_effective_date": (
        "변경적용일",
        "amendment effective date",
    ),
    "contract_date": (
        "계약일",
        "계약일자",
        "contract date",
    ),
    "terms_reference": (
        "참조약관코드",
        "적용약관코드",
        "terms reference",
    ),
    "edition_reference": (
        "적용판본코드",
        "edition reference",
    ),
}


METADATA_INSURER_CAPTIONS_V11: frozenset[str] = frozenset(
    {
        "한화생명",
        "ABL생명",
        "삼성생명",
        "흥국생명",
        "교보생명",
        "iM라이프생명",
        "미래에셋생명",
        "KDB생명",
        "DB생명",
        "동양생명",
        "메트라이프생명",
        "KB라이프생명",
        "신한라이프생명",
        "처브라이프생명",
        "하나생명",
        "BNP파리바카디프생명",
        "푸본현대생명",
        "라이나생명",
        "AIA생명",
        "NH농협생명",
        "메리츠화재",
        "한화손보",
        "롯데손보",
        "흥국화재",
        "삼성화재",
        "현대해상",
        "KB손보",
        "DB손해보험",
        "서울보증",
        "AXA손해보험",
        "하나손보",
        "AIG손보",
        "라이나손보",
        "신한EZ손해보험",
        "농협손해보험",
        "예별손보",
        "카카오페이손해보험",
    }
)

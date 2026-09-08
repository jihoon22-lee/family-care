"""Generated from terms-semantic-knowledge.v1.schema.json; do not edit."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SemanticContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class SemanticCitation(SemanticContract):
    bbox: Annotated[
        list[Annotated[float, Field(ge=0, le=100000)]], Field(min_length=4, max_length=4)
    ]
    citation_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=36,
            pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        ),
    ]
    end: Annotated[int, Field(ge=1, le=262144)]
    node_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    page_number: Annotated[int, Field(ge=1, le=500)]
    source_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    source_layer: Literal["native", "ocr"]
    start: Annotated[int, Field(ge=0, le=262144)]
    text: Annotated[str, Field(min_length=1, max_length=8192)]


class SemanticClassification(SemanticContract):
    code_system: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    code_version: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    codes: Annotated[
        list[
            Annotated[
                str, Field(min_length=1, max_length=64, pattern="^[a-z0-9][a-z0-9._:-]{0,63}$")
            ]
        ],
        Field(min_length=1, max_length=64),
    ]
    field: Literal[
        "MedicalEvent.classification", "MedicalEvent.diagnosis_code", "MedicalEvent.procedure_code"
    ]
    kind: Literal["classification"]


class SemanticCodeDefinition(SemanticContract):
    code_system: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    code_version: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    codes: Annotated[
        list[
            Annotated[
                str, Field(min_length=1, max_length=64, pattern="^[a-z0-9][a-z0-9._:-]{0,63}$")
            ]
        ],
        Field(min_length=1, max_length=64),
    ]
    effect: Literal["classification_codes"]
    field: Literal[
        "MedicalEvent.classification", "MedicalEvent.diagnosis_code", "MedicalEvent.procedure_code"
    ]
    kind: Literal["definition"]
    meaning: Annotated[str, Field(min_length=1, max_length=4096)]
    term: Annotated[str, Field(min_length=1, max_length=160)]


class SemanticCondition(SemanticContract):
    field: Literal[
        "MedicalEvent.admission",
        "MedicalEvent.performed",
        "MedicalEvent.diagnosis_confirmed",
        "MedicalEvent.admission_days",
        "PolicyContract.contract_start",
        "ClaimHistory.counted_occurrence",
    ]
    kind: Literal["condition"]
    operator: Literal["equals", "days_since", "count_before", "range", "count_below"]
    rule_kind: Literal["eligibility", "exclusion", "temporal", "frequency"]
    unit: Literal["days", "occurrences"] | None
    value: (
        bool
        | Annotated[int, Field(ge=0, le=100000)]
        | Annotated[list[Annotated[int, Field(ge=0, le=100000)]], Field(min_length=2, max_length=2)]
    )


class SemanticDailyCalculation(SemanticContract):
    basis: Literal["insured_amount_per_payable_day"]
    currency: Annotated[str, Field(min_length=1, max_length=3, pattern="^[A-Z]{3}$")]
    kind: Literal["calculation"]
    mode: Literal["daily"]
    rounding: Literal["half_up", "half_even", "up", "down"]


class SemanticDefinition(SemanticContract):
    effect: Literal["explanation_only"]
    kind: Literal["definition"]
    meaning: Annotated[str, Field(min_length=1, max_length=4096)]
    term: Annotated[str, Field(min_length=1, max_length=160)]


class SemanticEdge(SemanticContract):
    from_node_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    relation: Literal["DEPENDS_ON", "OVERRIDES"]
    to_node_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]


class SemanticFixedCalculation(SemanticContract):
    amount: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern="^(?:0|[1-9][0-9]{0,17})(?:\\.[0-9]{1,12})?$"),
    ]
    currency: Annotated[str, Field(min_length=1, max_length=3, pattern="^[A-Z]{3}$")]
    kind: Literal["calculation"]
    mode: Literal["fixed"]
    rounding: Literal["half_up", "half_even", "up", "down"]


class SemanticFootnote(SemanticContract):
    days: Annotated[int, Field(ge=0, le=36500)]
    effect: Literal["initial_excluded_days"]
    kind: Literal["footnote"]


class SemanticInformation(SemanticContract):
    effect: Literal["explanation_only", "unsupported_condition", "unsupported_calculation"]
    kind: Literal["information"]
    reason_code: Annotated[
        str, Field(min_length=1, max_length=64, pattern="^[A-Z][A-Z0-9_]{0,63}$")
    ]


class SemanticLimit(SemanticContract):
    currency: Annotated[str, Field(min_length=1, max_length=3, pattern="^[A-Z]{3}$")] | None
    kind: Literal["limit"]
    measure: Literal["payable_days", "maximum_amount"]
    unit: Literal["days", "amount"]
    value: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern="^(?:0|[1-9][0-9]{0,17})(?:\\.[0-9]{1,12})?$"),
    ]


class SemanticNode(SemanticContract):
    citation_ids: Annotated[
        list[
            Annotated[
                str,
                Field(
                    min_length=1,
                    max_length=36,
                    pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                ),
            ]
        ],
        Field(min_length=1, max_length=64),
    ]
    node_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    payload: (
        SemanticDailyCalculation
        | SemanticFixedCalculation
        | SemanticRatioCalculation
        | SemanticClassification
        | SemanticCondition
        | SemanticFootnote
        | SemanticLimit
        | SemanticDefinition
        | SemanticInformation
        | SemanticCodeDefinition
    )
    region_ids: Annotated[
        list[
            Annotated[
                str,
                Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
            ]
        ],
        Field(min_length=1, max_length=64),
    ]
    source_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    statement: Annotated[str, Field(min_length=1, max_length=4096)]


class SemanticProcessing(SemanticContract):
    consumed_region_ids: Annotated[
        list[
            Annotated[
                str,
                Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
            ]
        ],
        Field(min_length=0, max_length=4096),
    ]
    expected_region_ids: Annotated[
        list[
            Annotated[
                str,
                Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
            ]
        ],
        Field(min_length=0, max_length=4096),
    ]
    unresolved_region_ids: Annotated[
        list[
            Annotated[
                str,
                Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
            ]
        ],
        Field(min_length=0, max_length=4096),
    ]


class SemanticRatioCalculation(SemanticContract):
    basis: Literal["insured_amount"]
    currency: Annotated[str, Field(min_length=1, max_length=3, pattern="^[A-Z]{3}$")]
    kind: Literal["calculation"]
    mode: Literal["insured_ratio"]
    ratio: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern="^(?:0|[1-9][0-9]{0,17})(?:\\.[0-9]{1,12})?$"),
    ]
    rounding: Literal["half_up", "half_even", "up", "down"]


class SemanticSource(SemanticContract):
    content_sha256: Annotated[str, Field(min_length=1, max_length=64, pattern="^[0-9a-f]{64}$")]
    document_version_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=36,
            pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        ),
    ]
    generation_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=36,
            pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        ),
    ]
    source_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    structure_identity_sha256: Annotated[
        str, Field(min_length=1, max_length=64, pattern="^[0-9a-f]{64}$")
    ]
    terms_edition_id: (
        Annotated[
            str,
            Field(
                min_length=1,
                max_length=36,
                pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            ),
        ]
        | None
    )


class TermsSemanticKnowledge(SemanticContract):
    citations: Annotated[list[SemanticCitation], Field(min_length=1, max_length=1024)]
    edges: Annotated[list[SemanticEdge], Field(min_length=0, max_length=1024)]
    model_revision: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    nodes: Annotated[list[SemanticNode], Field(min_length=1, max_length=256)]
    processing: SemanticProcessing
    prompt_revision: Annotated[
        str, Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    roots: Annotated[
        list[
            Annotated[
                str,
                Field(min_length=1, max_length=128, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
            ]
        ],
        Field(min_length=1, max_length=32),
    ]
    schema_revision: Literal["terms-semantic-v1"]
    schema_version: Literal["1"]
    sources: Annotated[list[SemanticSource], Field(min_length=1, max_length=64)]


SemanticCitation.model_rebuild()
SemanticClassification.model_rebuild()
SemanticCodeDefinition.model_rebuild()
SemanticCondition.model_rebuild()
SemanticDailyCalculation.model_rebuild()
SemanticDefinition.model_rebuild()
SemanticEdge.model_rebuild()
SemanticFixedCalculation.model_rebuild()
SemanticFootnote.model_rebuild()
SemanticInformation.model_rebuild()
SemanticLimit.model_rebuild()
SemanticNode.model_rebuild()
SemanticProcessing.model_rebuild()
SemanticRatioCalculation.model_rebuild()
SemanticSource.model_rebuild()
TermsSemanticKnowledge.model_rebuild()

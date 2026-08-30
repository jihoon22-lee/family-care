"""Strict recommendation schema and one-call boundary tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.recommender import (
    RECOMMENDER_SCHEMA_NAME,
    RecommendationCandidate,
    RecommendationFact,
    RecommendationRequest,
    RecommendationValidationError,
    recommend_clauses,
    recommender_schema,
)


class _Provider:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> ProviderResponse:
        self.calls.append(dict(kwargs))
        return ProviderResponse(
            payload=self.payload,  # type: ignore[arg-type]
            request_id="synthetic-request-001",
        )


def _candidate(index: int) -> RecommendationCandidate:
    return RecommendationCandidate(
        token=f"candidate-{index:02d}",
        contract_label=f"Sample Policy {index}",
        coverage_label=f"Sample Coverage {index}",
        clause_label=f"Sample Clause {index}",
        excerpt=f"Sample bounded excerpt {index}",
        page_start=index,
        page_end=index,
        citation_kind="FACT_CITATION",
    )


def _request(count: int = 2) -> RecommendationRequest:
    return RecommendationRequest(
        situation="Synthetic bounded event situation marker",
        facts=(
            RecommendationFact(
                field_id="MedicalEvent.procedure_kind",
                value="sample_procedure",
                confirmation="USER_CONFIRMED",
            ),
        ),
        candidates=tuple(_candidate(index) for index in range(1, count + 1)),
    )


def test_schema_is_closed_and_one_call_can_only_reorder_supplied_tokens() -> None:
    provider = _Provider(
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "candidate-02",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                },
                {
                    "token": "candidate-01",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": "CONFIRM_PROCEDURE_KIND",
                },
            ],
        }
    )

    result = recommend_clauses(request=_request(), provider=provider, model="synthetic-model-v1")

    assert [item.token for item in result.selections] == ["candidate-02", "candidate-01"]
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["schema_name"] == RECOMMENDER_SCHEMA_NAME
    assert call["model"] == "synthetic-model-v1"
    payload = call["input_payload"]
    assert isinstance(payload, dict)
    assert set(payload) == {"schema_version", "event", "candidates"}
    assert [item["token"] for item in payload["candidates"]] == [  # type: ignore[index]
        "candidate-01",
        "candidate-02",
    ]


def test_recommender_schema_forbids_extra_decision_and_amount_fields() -> None:
    schema = recommender_schema()

    assert schema["additionalProperties"] is False
    item = schema["properties"]["recommendations"]["items"]  # type: ignore[index]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == {  # type: ignore[arg-type]
        "token",
        "explanation_code",
        "question_code",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "candidate-99",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                }
            ],
        },
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "candidate-01",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                },
                {
                    "token": "candidate-01",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                },
            ],
        },
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "candidate-01",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                    "payable_amount": "100",
                }
            ],
        },
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "candidate-01",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                    "eligibility_result": "MATCH",
                }
            ],
        },
    ],
)
def test_unknown_duplicate_or_authoritative_output_is_rejected(payload: object) -> None:
    provider = _Provider(payload)

    with pytest.raises(RecommendationValidationError) as raised:
        recommend_clauses(request=_request(), provider=provider, model="synthetic-model-v1")

    assert str(raised.value) == "INVALID_RECOMMENDATION_RESPONSE"
    assert len(provider.calls) == 1


def test_request_rejects_more_than_twelve_candidates_and_non_opaque_tokens() -> None:
    with pytest.raises(ValueError):
        _request(13)
    with pytest.raises(ValueError):
        replace(_request(), candidates=(replace(_candidate(1), token="not-opaque"),))


def test_request_and_result_repr_hide_event_facts_excerpts_and_request_id() -> None:
    request = _request()
    provider = _Provider(
        {
            "schema_version": "1",
            "recommendations": [
                {
                    "token": "candidate-01",
                    "explanation_code": "RELATED_CLAUSE",
                    "question_code": None,
                }
            ],
        }
    )

    result = recommend_clauses(request=request, provider=provider, model="synthetic-model-v1")
    rendered = f"{request!r} {result!r}"

    assert "bounded event situation marker" not in rendered
    assert "sample_procedure" not in rendered
    assert "bounded excerpt" not in rendered
    assert "synthetic-request-001" not in rendered


def test_request_rejects_database_ids_and_paths_before_provider_call() -> None:
    provider = _Provider({})

    with pytest.raises(ValueError):
        unsafe = replace(
            _request(),
            situation="00000000-0000-4000-8000-000000000001 /private/source.pdf",
        )
        recommend_clauses(request=unsafe, provider=provider, model="synthetic-model-v1")

    assert provider.calls == []

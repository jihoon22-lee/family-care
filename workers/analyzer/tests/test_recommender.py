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

    result = recommend_clauses(
        request=_request(),
        provider=provider,
        model="synthetic-model-v1",
        sensitive_terms=("Family Member A",),
    )

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
    assert schema["properties"]["schema_version"] == {  # type: ignore[index]
        "const": "1",
        "type": "string",
    }
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
        recommend_clauses(
            request=_request(),
            provider=provider,
            model="synthetic-model-v1",
            sensitive_terms=("Family Member A",),
        )

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

    result = recommend_clauses(
        request=request,
        provider=provider,
        model="synthetic-model-v1",
        sensitive_terms=("Family Member A",),
    )
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
        recommend_clauses(
            request=unsafe,
            provider=provider,
            model="synthetic-model-v1",
            sensitive_terms=("Family Member A",),
        )

    assert provider.calls == []


def test_provider_projection_minimizes_every_free_text_field() -> None:
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
    request = RecommendationRequest(
        situation="Family Member A sample@example.invalid synthetic visit",
        facts=(
            RecommendationFact(
                field_id="MedicalEvent.note",
                value="Family Member A 010-0000-0000",
                confirmation="USER_CONFIRMED",
            ),
        ),
        candidates=(
            RecommendationCandidate(
                token="candidate-01",
                contract_label="Family Member A Sample Policy",
                coverage_label="Family Member A Sample Coverage",
                clause_label="Family Member A Sample Clause",
                excerpt="Family Member A 증권번호: synthetic-policy-001",
                page_start=1,
                page_end=1,
                citation_kind="FACT_CITATION",
            ),
        ),
    )

    recommend_clauses(
        request=request,
        provider=provider,
        model="synthetic-model-v1",
        sensitive_terms=("Family Member A",),
    )

    payload = provider.calls[0]["input_payload"]
    assert isinstance(payload, dict)
    event = payload["event"]
    candidates = payload["candidates"]
    assert isinstance(event, dict)
    assert isinstance(candidates, list)
    projected = (
        event["situation"],
        event["facts"][0]["value"],
        candidates[0]["contract_label"],
        candidates[0]["coverage_label"],
        candidates[0]["clause_label"],
        candidates[0]["excerpt"],
    )
    assert all("[REDACTED]" in value for value in projected)
    assert all("Family Member A" not in value for value in projected)
    assert "sample@example.invalid" not in event["situation"]
    assert "010-0000-0000" not in event["facts"][0]["value"]
    assert "synthetic-policy-001" not in candidates[0]["excerpt"]


def test_provider_projection_redacts_identifiers_crossing_the_240_character_boundary() -> None:
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
    request = replace(
        _request(count=1),
        situation=f"{'x' * 225} sample@example.invalid synthetic tail",
        candidates=(
            replace(
                _candidate(1),
                coverage_label=f"{'y' * 230} Family Member A synthetic tail",
            ),
        ),
    )

    recommend_clauses(
        request=request,
        provider=provider,
        model="synthetic-model-v1",
        sensitive_terms=("Family Member A",),
    )

    payload = provider.calls[0]["input_payload"]
    assert isinstance(payload, dict)
    event = payload["event"]
    candidates = payload["candidates"]
    assert isinstance(event, dict)
    assert isinstance(candidates, list)
    assert "sample@" not in event["situation"]
    assert "Family" not in candidates[0]["coverage_label"]


def test_provider_projection_redacts_labelled_identity_values_longer_than_160_chars() -> None:
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
    synthetic_address = ("SyntheticRoad" * 15) + "ABCD"
    assert len(synthetic_address) == 199
    request = replace(
        _request(count=1),
        situation=f"주소: {synthetic_address}",
    )

    recommend_clauses(
        request=request,
        provider=provider,
        model="synthetic-model-v1",
        sensitive_terms=("Family Member A",),
    )

    assert len(provider.calls) == 1
    payload = provider.calls[0]["input_payload"]
    assert isinstance(payload, dict)
    event = payload["event"]
    assert isinstance(event, dict)
    assert event["situation"] == "주소: [REDACTED]"
    assert synthetic_address not in repr(payload)

"""Cost and replay boundaries for the explicit synthetic review evaluation."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx2
import openai
import pytest

from scripts.fixed_review_evaluation import EvaluationBudget, EvaluationBudgetError, usage_cost


def _wire_body() -> dict[str, Any]:
    from familycare_worker.ai.guidance_reviewer import (
        REVIEW_INSTRUCTION,
        SCHEMA_NAME,
        guidance_review_schema,
    )

    return {
        "model": "gpt-5.6-terra",
        "store": False,
        "max_output_tokens": 4000,
        "instructions": REVIEW_INSTRUCTION,
        "input": "{}",
        "text": {
            "format": {
                "type": "json_schema",
                "name": SCHEMA_NAME,
                "schema": guidance_review_schema(),
                "strict": True,
            }
        },
    }


def test_wire_budget_is_durable_before_send_and_duplicate_resume_never_sends(
    tmp_path: Path,
) -> None:
    from scripts.fixed_review_evaluation import BudgetedReviewTransport, EvaluationJournal

    path = tmp_path / "journal.json"
    sent = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        record = json.loads(path.read_text())
        assert record["budget"]["reservations"]["synthetic-case-a"]["settled"] is False
        assert json.loads(request.content)["service_tier"] == "default"
        sent.append(True)
        return httpx2.Response(
            200,
            json={
                "model": "gpt-5.6-terra",
                "service_tier": "default",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    for attempt in range(2):
        with EvaluationJournal(path, source="a" * 64, limit=Decimal("1")) as journal:
            transport = BudgetedReviewTransport(journal, httpx2.MockTransport(handle))
            transport.case_id = "synthetic-case-a"
            with httpx2.Client(transport=transport) as client:
                if attempt:
                    with pytest.raises(EvaluationBudgetError):
                        client.post(
                            "https://api.openai.com/v1/responses",
                            json=_wire_body(),
                        )
                else:
                    assert (
                        client.post(
                            "https://api.openai.com/v1/responses",
                            json=_wire_body(),
                        ).status_code
                        == 200
                    )
    assert sent == [True]
    settled = json.loads(path.read_text())["budget"]["reservations"]["synthetic-case-a"]
    assert settled["actual_usd"] == "0.000085"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "alteration",
    [
        "model",
        "host",
        "size",
        "output",
        "store",
        "tools",
        "previous_response_id",
        "conversation",
        "image",
    ],
)
def test_invalid_wire_request_never_reaches_network(tmp_path: Path, alteration: str) -> None:
    from scripts.fixed_review_evaluation import BudgetedReviewTransport, EvaluationJournal

    def handle(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("unexpected network call")

    with EvaluationJournal(
        tmp_path / "journal.json", source="a" * 64, limit=Decimal("1")
    ) as journal:
        transport = BudgetedReviewTransport(journal, httpx2.MockTransport(handle))
        transport.case_id = "synthetic-case-a"
        body = _wire_body()
        url = "https://api.openai.com/v1/responses"
        if alteration == "host":
            url = "https://synthetic.invalid/v1/responses"
        elif alteration == "size":
            body["input"] = "x" * 32769
        elif alteration == "output":
            body["max_output_tokens"] = 4001
        elif alteration == "store":
            body["store"] = True
        elif alteration in {"tools", "previous_response_id", "conversation"}:
            body[alteration] = "unexpected"
        elif alteration == "image":
            body["input"] = [
                {"type": "input_image", "image_url": "https://synthetic.invalid/image"}
            ]
        else:
            body["model"] = "unexpected-model"
        with httpx2.Client(transport=transport) as client, pytest.raises(EvaluationBudgetError):
            client.post(url, json=body)
        assert journal.budget.request_count == 0


def test_late_response_after_journal_close_cannot_overwrite_retained_reservation(
    tmp_path: Path,
) -> None:
    from scripts.fixed_review_evaluation import BudgetedReviewTransport, EvaluationJournal

    path = tmp_path / "journal.json"
    with EvaluationJournal(path, source="a" * 64, limit=Decimal("1")) as journal:

        def interrupted(request: httpx2.Request) -> httpx2.Response:
            journal.__exit__()
            return httpx2.Response(
                200,
                json={
                    "model": "gpt-5.6-terra",
                    "service_tier": "default",
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            )

        transport = BudgetedReviewTransport(journal, httpx2.MockTransport(interrupted))
        transport.case_id = "synthetic-case-a"
        with httpx2.Client(transport=transport) as client:
            assert (
                client.post("https://api.openai.com/v1/responses", json=_wire_body()).status_code
                == 200
            )
    retained = json.loads(path.read_text())["budget"]["reservations"]["synthetic-case-a"]
    assert retained["settled"] is False and retained["actual_usd"] is None


def test_actual_sdk_wire_passes_the_guard_with_one_mocked_standard_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker.ai.guidance_reviewer import (
        REVIEW_INSTRUCTION,
        SCHEMA_NAME,
        guidance_review_schema,
    )
    from familycare_worker.ai.provider import OpenAiResponsesAdapter, _OpenAiClient

    from scripts.fixed_review_evaluation import BudgetedReviewTransport, EvaluationJournal

    attempts = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["service_tier"] == "default" and body["store"] is False
        attempts.append(True)
        return httpx2.Response(
            200,
            json={
                "id": "resp_synthetic_wire",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.6-terra",
                "service_tier": "default",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "id": "msg_synthetic",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"schema_version":"1"}',
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    with EvaluationJournal(
        tmp_path / "journal.json", source="a" * 64, limit=Decimal("1")
    ) as journal:
        transport = BudgetedReviewTransport(journal, httpx2.MockTransport(handle))
        transport.case_id = "synthetic-sdk-wire"
        with httpx2.Client(transport=transport) as client:
            provider = OpenAiResponsesAdapter(
                {SCHEMA_NAME: guidance_review_schema()},
                client_factory=lambda key: cast(
                    _OpenAiClient,
                    openai.OpenAI(
                        api_key=key,
                        max_retries=0,
                        base_url="https://api.openai.com/v1",
                        http_client=client,
                    ),
                ),
                output_token_limits={SCHEMA_NAME: 4000},
            )
            result = provider.complete(
                model="gpt-5.6-terra",
                schema_name=SCHEMA_NAME,
                system_instruction=REVIEW_INSTRUCTION,
                input_payload={},
            )
        assert result.metadata is not None and result.metadata.usage is not None
        assert result.metadata.usage.total_tokens == 140
        assert journal.budget.request_count == 1
        assert journal.budget.committed_cost == Decimal("0.00073")
    assert attempts == [True]


def test_cost_accounts_cached_and_written_input_without_double_counting() -> None:
    assert usage_cost(
        input_tokens=1000, output_tokens=200, cached_input_tokens=100, cache_write_input_tokens=200
    ) == Decimal("0.00432")


def test_account_failure_stops_remaining_cases_and_retains_unknown_charge(tmp_path: Path) -> None:
    from scripts.fixed_review_evaluation import BudgetedReviewTransport, EvaluationJournal

    with EvaluationJournal(
        tmp_path / "journal.json", source="a" * 64, limit=Decimal("1")
    ) as journal:
        transport = BudgetedReviewTransport(
            journal,
            httpx2.MockTransport(
                lambda request: httpx2.Response(
                    429, json={"error": {"code": "insufficient_quota"}}
                ),
            ),
        )
        transport.case_id = "synthetic-quota-case"
        with httpx2.Client(transport=transport) as client:
            assert (
                client.post("https://api.openai.com/v1/responses", json=_wire_body()).status_code
                == 429
            )
        assert transport.rejection == "EVALUATION_PROVIDER_HTTP_FAILURE"
        assert journal.budget.request_count == 1 and journal.budget.committed_cost > 0


def test_unknown_usage_retains_the_entire_reservation() -> None:
    budget = EvaluationBudget(Decimal("1"), maximum_requests=20)
    budget.reserve("synthetic-case-a", input_bound=10000, output_bound=4000)
    budget.settle("synthetic-case-a", None)
    assert budget.committed_cost == Decimal("0.073")
    with pytest.raises(EvaluationBudgetError):
        budget.reserve("synthetic-case-a", input_bound=10000, output_bound=4000)


def test_known_usage_releases_only_unused_cost_and_never_the_request_identity() -> None:
    budget = EvaluationBudget(Decimal("0.08"), maximum_requests=20)
    budget.reserve("synthetic-case-a", input_bound=10000, output_bound=4000)
    budget.settle("synthetic-case-a", Decimal("0.004"))
    budget.reserve("synthetic-case-b", input_bound=10000, output_bound=4000)
    assert budget.committed_cost == Decimal("0.077")
    with pytest.raises(EvaluationBudgetError):
        budget.reserve("synthetic-case-c", input_bound=10000, output_bound=4000)


def test_resume_preserves_uncertain_cost_and_request_count() -> None:
    budget = EvaluationBudget(Decimal("1"), maximum_requests=1)
    budget.reserve("synthetic-case-a", input_bound=10000, output_bound=4000)
    restored = EvaluationBudget.from_record(budget.to_record())
    assert restored.committed_cost == Decimal("0.073")
    with pytest.raises(EvaluationBudgetError):
        restored.reserve("synthetic-case-b", input_bound=10, output_bound=10)


@pytest.mark.parametrize("invalid", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_or_over_reservation_usage_cannot_free_budget(invalid: Decimal) -> None:
    budget = EvaluationBudget(Decimal("1"), maximum_requests=20)
    budget.reserve("synthetic-case-a", input_bound=10, output_bound=10)
    with pytest.raises(EvaluationBudgetError):
        budget.settle("synthetic-case-a", invalid)
    assert budget.committed_cost > 0


def test_actual_charge_above_reservation_retains_charge_and_halts_further_calls() -> None:
    budget = EvaluationBudget(Decimal("1"), maximum_requests=20)
    budget.reserve("synthetic-case-a", input_bound=10, output_bound=10)
    budget.settle("synthetic-case-a", Decimal("0.5"))
    assert budget.committed_cost == Decimal("0.5")
    with pytest.raises(EvaluationBudgetError):
        budget.reserve("synthetic-case-b", input_bound=10, output_bound=10)

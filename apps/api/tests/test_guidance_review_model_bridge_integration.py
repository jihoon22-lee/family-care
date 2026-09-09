"""Executable synthetic review crosses SDK, Worker hydration and API source replay."""

import json
from copy import deepcopy

import httpx2
import openai
import psycopg
import pytest
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_worker.ai.guidance_reviewer import SCHEMA_NAME, guidance_review_schema
from familycare_worker.ai.provider import OpenAiResponsesAdapter
from familycare_worker.guidance_review_budget import GuidanceReviewBudget
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue, _lease
from familycare_worker.guidance_review_runner import GuidanceReviewRunner
from psycopg.rows import dict_row

from apps.api.tests.test_guidance_review_projection_integration import _project
from apps.api.tests.test_guidance_review_reassessment_integration import (
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_review_reassessment_integration import (
    unreviewed_original as unreviewed_original,
)
from apps.api.tests.test_terms_change_integration import _psycopg_url
from workers.analyzer.tests.test_guidance_review_provider import _response

pytestmark = pytest.mark.integration


def _wire_graph(sample, packet):
    """Express the fixture's executable meaning using only the actual request aliases."""
    original = sample.packet.to_payload()["envelope"]
    envelope = packet["envelope"]
    regions = {}
    citations = {}
    for source_region, wire_region in zip(original["regions"], envelope["regions"], strict=True):
        regions[source_region["region_id"]] = wire_region["region_id"]
        for source_citation, wire_citation in zip(
            source_region["citations"], wire_region["citations"], strict=True
        ):
            citations[source_citation["citation_id"]] = wire_citation
    graph = deepcopy(sample.graph)
    nodes = {node["node_id"]: f"synthetic-node-{i}" for i, node in enumerate(graph["nodes"])}
    graph["sources"] = [deepcopy(envelope["source"])]
    graph["citations"] = [deepcopy(citations[c["citation_id"]]) for c in graph["citations"]]
    for node in graph["nodes"]:
        node["node_id"] = nodes[node["node_id"]]
        node["source_id"] = envelope["source"]["source_id"]
        node["region_ids"] = [regions[key] for key in node["region_ids"]]
        node["citation_ids"] = [citations[key]["citation_id"] for key in node["citation_ids"]]
    graph["roots"] = [nodes[key] for key in graph["roots"]]
    for edge in graph["edges"]:
        edge["from_node_id"] = nodes[edge["from_node_id"]]
        edge["to_node_id"] = nodes[edge["to_node_id"]]
    graph["processing"] = {
        "expected_region_ids": list(regions.values()),
        "consumed_region_ids": [
            regions[key] for key in sample.graph["processing"]["consumed_region_ids"]
        ],
        "unresolved_region_ids": [
            regions[key]
            for key in sample.graph["processing"]["unresolved_region_ids"]
            if key in regions
        ],
    }
    return graph


def test_executable_review_crosses_model_wire_and_recomputes_missing_candidate(
    unreviewed_original, monkeypatch
):
    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        lease = _lease(
            connection.execute(
                "SELECT * FROM guidance_review_jobs WHERE id=%s", (sample.job.id,)
            ).fetchone()
        )

    class ClaimedQueue(GuidanceReviewQueue):
        claimed_once = False

        def claim(self):
            if self.claimed_once:
                return None
            self.claimed_once = True
            return lease

    attempts = []

    def handle(request):
        assert request.url.host == "synthetic.invalid"
        body = json.loads(request.content)
        attempts.append(body)
        assert body["store"] is False and body["max_output_tokens"] == 4000
        assert body["text"]["format"]["strict"] is True
        payload = json.loads(body["input"])
        assert payload["local_answer"]["candidates"] == []
        packet = payload["source_packets"][0]
        proposal = {
            "schema_revision": "guidance-review-proposals-v1",
            "reviewed_packet_aliases": [packet["packet_alias"]],
            "unreviewed_packet_aliases": [
                item["packet_alias"] for item in payload["source_packets"][1:]
            ],
            "suggestions": [
                {
                    "packet_alias": packet["packet_alias"],
                    "kind": "ADDITIONAL_CANDIDATE",
                    "graph": _wire_graph(sample, packet),
                    "affected_fact_paths": ["MedicalEvent.admission_days"],
                    "proposed_amount": "999999",
                }
            ],
        }
        response = _response()
        response["output"][0]["content"][0]["text"] = json.dumps(proposal)
        return httpx2.Response(200, json=response)

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    with httpx2.Client(transport=httpx2.MockTransport(handle)) as http_client:
        adapter = OpenAiResponsesAdapter(
            {SCHEMA_NAME: guidance_review_schema()},
            client_factory=lambda api_key: openai.OpenAI(
                api_key=api_key,
                max_retries=0,
                base_url="https://synthetic.invalid/v1",
                organization="synthetic-org",
                project="synthetic-project",
                http_client=http_client,
            ),
            output_token_limits={SCHEMA_NAME: 4000},
            request_timeouts={SCHEMA_NAME: 40.0},
        )
        runner = GuidanceReviewRunner(
            queue=ClaimedQueue(sample.url),
            provider=adapter,
            request_budget=GuidanceReviewBudget(sample.url),
        )
        assert runner.run_once("synthetic-model-bridge")
        assert not runner.run_once("synthetic-model-bridge")

    repository = GuidanceReviewRepository(sample.url)
    staged = repository.get_job(sample.scope, sample.job.id)
    assert staged.state == "running" and staged.error_code is None
    assert staged.result is None
    assert _project(sample) == 1
    reviewed = repository.get_job(sample.scope, sample.job.id)
    assert len(attempts) == 1
    assert reviewed.state == "partial" and reviewed.error_code is None
    assert reviewed.result is not None
    candidate = next(
        item
        for item in reviewed.result.guidance.candidates
        if item.ref == sample.packet.coverage_ref
    )
    assert candidate.estimate.amount == "300"
    assert reviewed.result.differences[0].change == "ADDED"
    assert reviewed.result.findings[0].status == "APPLIED"
    assert "REVIEW_ADVISORY_AMOUNT_IGNORED" in reviewed.result.findings[0].reason_codes
    assert reviewed.usage.requests_reserved == 1 and reviewed.usage.total_tokens == 140
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (sample.original.run_id,)
        ).fetchone() == (sample.original.local_guidance.model_dump(mode="json"),)
        assert connection.execute(
            "SELECT count(*) FROM guidance_review_publications WHERE review_job_id=%s",
            (sample.job.id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM terms_semantic_publications"
        ).fetchone() == (0,)

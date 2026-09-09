"""Frozen candidate cases retain their meaning in the real database review boundary."""

import json
import os
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

import httpx2
import openai
import psycopg
import pytest
from familycare_api.guidance_review.projector import GuidanceReviewProjector
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_worker.ai.guidance_reviewer import SCHEMA_NAME, guidance_review_schema
from familycare_worker.ai.provider import OpenAiResponsesAdapter
from familycare_worker.guidance_review_budget import GuidanceReviewBudget
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue
from familycare_worker.guidance_review_runner import GuidanceReviewRunner

from apps.api.tests.fixed_guidance_review_fixture import (
    EVENT_KIND_SYSTEM,
    EVENT_KIND_VERSION,
    exact_companion_amount,
    reference_review_proposals,
    seed_fixed_review_case,
)
from apps.api.tests.test_terms_change_integration import _psycopg_url
from scripts.claim_guidance_benchmark import DEFAULT_CASES, load_cases
from workers.analyzer.tests.test_guidance_review_provider import _response

pytestmark = pytest.mark.integration
CASES = load_cases(DEFAULT_CASES)


def test_unattached_older_same_hash_version_cannot_supply_the_policy_source(monkeypatch):
    from apps.api.tests import fixed_guidance_review_fixture as fixture

    original = CASES[0]
    parameters = json.loads(json.dumps(original.scenario_parameters))
    names = {
        key: f"synthetic-orphan-coverage-{index}"
        for index, key in enumerate(original.candidate_pool)
    }
    for raw in parameters["coverages"]:
        raw["coverage_key"] = names[raw["coverage_key"]]
    case = replace(
        original,
        case_id="synthetic-orphan-versions",
        contract_group="synthetic-orphan-versions",
        scenario_parameters=parameters,
        candidate_pool=tuple(names.values()),
        expected_primary=tuple(names[key] for key in original.expected_primary),
        expected_conditional=tuple(names[key] for key in original.expected_conditional),
    )
    add = fixture._add_document
    orphan_versions = []

    def add_after_retained_orphan(url, job, text, *, kind, digest):
        if kind == "policy":
            document, version, extraction = uuid4(), uuid4(), uuid4()
            with psycopg.connect(_psycopg_url(url)) as connection:
                connection.execute(
                    "INSERT INTO documents(id,source_key,document_kind,status,page_count) "
                    "VALUES(%s,%s,'policy','ready',1)",
                    (document, f"synthetic/orphan-{document}.pdf"),
                )
                connection.execute(
                    "INSERT INTO document_versions(id,document_id,version_number,"
                    "content_sha256,byte_size,page_count) "
                    "VALUES(%s,%s,1,%s,128,1)",
                    (version, document, digest),
                )
                connection.execute(
                    "INSERT INTO extractions(id,document_version_id,extractor_name,"
                    "extractor_version,extractor_config_hash,quality_rule_version,"
                    "status,succeeded_at) "
                    "VALUES(%s,%s,'synthetic',"
                    "'v1',%s,'quality-v1','succeeded',clock_timestamp())",
                    (extraction, version, "f" * 64),
                )
            orphan_versions.append(version)
        add(url, job, text, kind=kind, digest=digest)

    monkeypatch.setattr(fixture, "_add_document", add_after_retained_orphan)
    sample = seed_fixed_review_case(os.environ["FAMILYCARE_TEST_DATABASE_URL"], case)
    assert orphan_versions and sample.original.local_guidance is not None
    with psycopg.connect(_psycopg_url(sample.database_url)) as connection:
        versions = connection.execute(
            "SELECT source_document_version_id FROM policy_contracts WHERE household_space_id=%s",
            (sample.scope.household_space_id,),
        ).fetchall()
    assert versions and all(version not in orphan_versions for (version,) in versions)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_fixed_case_database_answer_matches_independent_frozen_expectations(case, monkeypatch):
    sample = seed_fixed_review_case(os.environ["FAMILYCARE_TEST_DATABASE_URL"], case)
    guidance = sample.original.local_guidance
    assert guidance is not None
    scoped_kind = next(
        item for item in sample.event.structured_facts if item["field_id"] == "condition_class"
    )
    assert scoped_kind["value"] == case.scenario_parameters["event_facts"]["event.kind"]
    assert scoped_kind["code_system"] == EVENT_KIND_SYSTEM
    assert scoped_kind["code_version"] == EVENT_KIND_VERSION
    primary = {
        sample.coverage_keys[candidate.ref.coverage_id]
        for candidate in guidance.candidates
        if candidate.group == "PRIMARY"
    }
    conditional = {
        sample.coverage_keys[candidate.ref.coverage_id]
        for candidate in guidance.candidates
        if candidate.group == "CONDITIONAL"
    }
    assert primary == set(case.expected_primary)
    assert conditional == set(case.expected_conditional)
    assert not (
        not guidance.candidates and guidance.outcome in {"KNOWLEDGE_PENDING", "INPUT_UNRESOLVED"}
    )
    assert sample.sources.packets
    assert all(
        packet.coverage_ref.coverage_id in sample.coverage_keys for packet in sample.sources.packets
    )
    raw_by_key = {raw["coverage_key"]: raw for raw in case.scenario_parameters["coverages"]}
    for packet in sample.sources.packets:
        raw = raw_by_key[sample.coverage_keys[packet.coverage_ref.coverage_id]]
        assert raw["enrollment"] == "confirmed" and raw["subject"] == "same_member"
    supplied = json.dumps(
        {
            "local": guidance.model_dump(mode="json"),
            "envelopes": [packet.to_payload()["envelope"] for packet in sample.sources.packets],
        }
    )
    assert case.case_id not in supplied
    assert all(key not in supplied for key in case.candidate_pool)
    assert "expected_amount" not in supplied and "expected_primary" not in supplied
    with psycopg.connect(_psycopg_url(sample.database_url)) as connection:
        ledger = connection.execute(
            "SELECT r.id,r.policy_contract_id,party.family_member_id FROM riders r "
            "JOIN policy_parties party ON party.policy_contract_id=r.policy_contract_id "
            "WHERE r.household_space_id=%s AND party.role='primary_insured'",
            (sample.scope.household_space_id,),
        ).fetchall()
        originals = connection.execute(
            "SELECT DISTINCT g.document_version_id,node->>'text' FROM "
            "document_structure_generations g JOIN document_versions v ON "
            "v.id=g.document_version_id JOIN documents d ON d.id=v.document_id "
            "CROSS JOIN LATERAL jsonb_array_elements("
            "document_structure_projection(g.id,g.household_space_id,ARRAY[1])->'nodes') node "
            "WHERE g.household_space_id=%s AND d.document_kind='policy'",
            (sample.scope.household_space_id,),
        ).fetchall()
    retained = {
        sample.coverage_keys[rider]: (contract, member) for rider, contract, member in ledger
    }
    assert set(retained) == {
        key for key, raw in raw_by_key.items() if raw["enrollment"] == "confirmed"
    }
    implicit_contracts = {}
    explicit_contracts = {}
    for key, (contract, member) in retained.items():
        raw = raw_by_key[key]
        assert (member == sample.event.family_member_id) == (raw["subject"] == "same_member")
        identity = raw.get("contract_identity")
        if identity is None:
            implicit_contracts.setdefault(raw["subject"], set()).add(contract)
        else:
            explicit_contracts.setdefault(identity, set()).add(contract)
    assert all(len(contracts) == 1 for contracts in implicit_contracts.values())
    assert all(len(contracts) == 1 for contracts in explicit_contracts.values())
    assert len(set.union(set(), *explicit_contracts.values())) == len(explicit_contracts)
    for ordinal, raw in enumerate(case.scenario_parameters["coverages"], 1):
        documents = {
            version
            for version, text in originals
            if f"Sample Benefit {ordinal} enrollment:" in text
        }
        assert len(documents) == raw["source_count"]
    repository = GuidanceReviewRepository(sample.database_url, model="gpt-5.6-terra")
    review = repository.enqueue(
        sample.scope,
        sample.event.id,
        run_id=sample.original.run_id,
        expected_event_version=sample.event.version,
    )
    assert review.state == "queued"
    assert review.usage.requests_reserved == 0
    attempts = []
    proposal_errors = []

    def handle(request):
        assert request.url.host == "synthetic.invalid"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.6-terra"
        assert body["store"] is False and body["max_output_tokens"] == 4000
        assert body["text"]["format"]["strict"] is True
        attempts.append(True)
        try:
            proposal = reference_review_proposals(json.loads(body["input"]))
        except Exception as error:
            # SDK provider errors intentionally hide request details; retain the
            # original synthetic test-helper error for an actionable test failure.
            proposal_errors.append(error)
            raise
        response = _response()
        response["id"] = f"resp_synthetic_{review.id.hex}"
        response["model"] = body["model"]
        response["output"][0]["content"][0]["text"] = json.dumps(proposal)
        return httpx2.Response(200, json=response)

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    with httpx2.Client(transport=httpx2.MockTransport(handle)) as http_client:
        provider = OpenAiResponsesAdapter(
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
            queue=GuidanceReviewQueue(sample.database_url),
            provider=provider,
            request_budget=GuidanceReviewBudget(sample.database_url, daily=len(CASES)),
        )
        assert runner.run_once("synthetic-fixed-model-bridge")
    if proposal_errors:
        raise proposal_errors[0]
    assert len(attempts) == 1
    assert GuidanceReviewProjector(sample.database_url).project_pending(limit=1) == 1
    reviewed = repository.get_job(sample.scope, review.id)
    assert reviewed.state in {"partial", "completed"}
    assert reviewed.error_code is None and reviewed.result is not None
    assert reviewed.result.findings
    assert any(
        finding.status in {"APPLIED", "AGREEMENT"}
        and "REVIEW_SOURCE_VERIFIED" in finding.reason_codes
        for finding in reviewed.result.findings
    )
    assert reviewed.usage.requests_reserved == 1
    assert reviewed.usage.total_tokens == 140
    after = reviewed.result.guidance
    assert {
        sample.coverage_keys[item.ref.coverage_id]
        for item in after.candidates
        if item.group == "PRIMARY"
    } == primary
    assert {
        sample.coverage_keys[item.ref.coverage_id]
        for item in after.candidates
        if item.group == "CONDITIONAL"
    } == conditional
    with psycopg.connect(_psycopg_url(sample.database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM guidance_review_publications WHERE review_job_id=%s",
            (review.id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s",
            (sample.original.run_id,),
        ).fetchone() == (guidance.model_dump(mode="json"),)


def test_expected_answers_do_not_affect_fixture_data_or_stored_local_answer():
    case = CASES[0]
    url = os.environ["FAMILYCARE_TEST_DATABASE_URL"]
    first = seed_fixed_review_case(url, case)
    poisoned = replace(case, expected_primary=(), expected_conditional=(), answerable=False)
    second = seed_fixed_review_case(url, poisoned)
    assert second.scope == first.scope
    assert second.event == first.event
    assert second.original == first.original
    assert second.coverage_keys == first.coverage_keys
    assert second.sources == first.sources


def test_companion_rounding_preserves_exact_frozen_input_arithmetic():
    checked = set()
    for case in CASES:
        parameters = case.scenario_parameters
        for raw in parameters["coverages"]:
            result = exact_companion_amount(parameters, raw)
            if result is not None:
                assert result == result.quantize(Decimal(1), rounding=ROUND_HALF_UP)
                poisoned = json.loads(json.dumps(raw))
                poisoned["calculation"]["expected_amount"] = "999999"
                assert exact_companion_amount(parameters, poisoned) == result
                checked.add(raw["calculation"]["kind"])
    assert checked == {"fixed", "daily", "ratio"}

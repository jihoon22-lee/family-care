"""Saved local guidance remains separate from recorded insurer payment."""

from dataclasses import replace
from uuid import uuid4

import pytest
from familycare_api.claims import snapshot as snapshots
from familycare_api.claims.schemas import ClaimCaseResponse, ClaimCreateRequest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from pydantic import ValidationError

from apps.api.tests.test_guidance_local_event_engine import context
from apps.api.tests.test_private_knowledge_engine import HOUSEHOLD_ID, _event


def _guidance():
    event = replace(_event(), facts={}, situation="5일 입원 예정입니다.")
    return LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())


def test_guidance_request_names_exact_run_event_version_and_coverage_only():
    guidance = _guidance()
    request = ClaimCreateRequest.model_validate(
        {
            "guidance": {
                "run_id": str(uuid4()),
                "expected_event_version": 1,
                "coverage": guidance.candidates[0].ref.model_dump(mode="json"),
            }
        }
    )
    assert request.rider_id is None
    assert request.guidance.expected_event_version == 1
    with pytest.raises(ValidationError):
        ClaimCreateRequest.model_validate({})
    with pytest.raises(ValidationError):
        ClaimCreateRequest.model_validate({**request.model_dump(), "rider_id": uuid4()})
    with pytest.raises(ValidationError):
        ClaimCreateRequest.model_validate(
            {"guidance": {**request.guidance.model_dump(), "amount": "999999"}}
        )


def test_saved_guidance_preserves_formula_scenario_trace_and_all_versions():
    guidance = _guidance()
    candidate = guidance.candidates[0]
    run_id = uuid4()
    saved = snapshots.build_guidance_claim_snapshot(guidance, candidate.ref, run_id=run_id)
    payload = saved.persistence_values()
    local = payload["candidate_snapshot"]["local_guidance"]
    assert local["run_id"] == str(run_id)
    assert local["event_version"] == guidance.event_version
    assert local["versions"] == guidance.versions.model_dump(mode="json")
    assert local["candidate"] == candidate.model_dump(mode="json")
    assert local["candidate"]["estimate"]["kind"] == "FORMULA"
    assert local["candidate"]["scenarios"][0]["estimate"]["amount"] == "300"
    assert local["candidate"]["scenarios"][0]["estimate"]["trace"]["steps"]
    assert "paid_amount" not in local
    payload["candidate_snapshot"]["local_guidance"]["candidate"]["estimate"]["amount"] = "999"
    assert (
        saved.persistence_values()["candidate_snapshot"]["local_guidance"]["candidate"]["estimate"][
            "amount"
        ]
        is None
    )
    assert saved.snapshot_sha256 == snapshots.snapshot_sha256(saved.payload())
    from scripts.check_contracts import CLAIM_SCHEMA_PATH, load_json, validate_schema_instance

    schema = load_json(CLAIM_SCHEMA_PATH)
    assert (
        validate_schema_instance(
            schema["$defs"]["CandidateSnapshot"],
            saved.persistence_values()["candidate_snapshot"],
            root_schema=schema,
        )
        == []
    )


def test_unselected_coverage_cannot_enter_snapshot():
    guidance = _guidance()
    wrong = guidance.candidates[0].ref.model_copy(update={"coverage_id": uuid4()})
    with pytest.raises(snapshots.SnapshotValidationError):
        snapshots.build_guidance_claim_snapshot(guidance, wrong, run_id=uuid4())


@pytest.mark.parametrize(
    "update",
    [
        {"rider_id": None},
        {"policy_contract_id": None},
        {"insurer_key": None},
        {
            "coverage": {
                "kind": "PRIVATE_KNOWLEDGE_COVERAGE",
                "contract_id": str(uuid4()),
                "coverage_id": str(uuid4()),
            }
        },
    ],
)
def test_claim_response_rejects_mixed_or_incomplete_source(update):
    from apps.api.tests.test_claim_workflow_api import _claim

    with pytest.raises(ValidationError):
        ClaimCaseResponse.model_validate({**_claim(), **update})


def test_private_claim_source_is_valid_without_inventing_operational_ids():
    from familycare_api.claims.domain import ClaimCase, ClaimHistoryRecord

    from apps.api.tests.test_claim_workflow_api import _claim
    from scripts.check_contracts import CLAIM_SCHEMA_PATH, load_json, validate_schema_instance

    source = {
        "private_contract_id": uuid4(),
        "private_coverage_id": uuid4(),
        "policy_contract_id": None,
        "rider_id": None,
    }
    ids = {
        "id": uuid4(),
        "household_space_id": HOUSEHOLD_ID,
        "medical_event_id": uuid4(),
        "family_member_id": uuid4(),
    }
    claim = ClaimCase(**ids, **source, insurer_key=None, insurer_display="Synthetic Insurer")
    history = ClaimHistoryRecord(
        **ids, **source, outcome="denied", payment_date=None, counted_occurrence=False
    )
    assert claim.rider_id is history.rider_id is None
    response = ClaimCaseResponse.model_validate(
        {
            **_claim(),
            "policy_contract_id": None,
            "rider_id": None,
            "insurer_key": None,
            "insurer_display": "Synthetic Insurer",
            "coverage": {
                "kind": "PRIVATE_KNOWLEDGE_COVERAGE",
                "contract_id": source["private_contract_id"],
                "coverage_id": source["private_coverage_id"],
            },
        }
    )
    assert response.coverage.coverage_id == source["private_coverage_id"]
    schema = load_json(CLAIM_SCHEMA_PATH)
    from scripts.check_contracts import CLAIM_EXAMPLE_PATH

    example = load_json(CLAIM_EXAMPLE_PATH)
    for name, fixture in (
        ("ClaimCase", example["claim_case"]),
        ("ClaimHistory", example["history"][0]),
    ):
        private = {**fixture, **{k: str(v) if v is not None else None for k, v in source.items()}}
        if name == "ClaimCase":
            private.update(insurer_key=None, insurer_display="Synthetic Insurer")
        assert validate_schema_instance(schema["$defs"][name], private, root_schema=schema) == []
        for bad in ({"private_coverage_id": None}, {"rider_id": str(uuid4())}):
            assert validate_schema_instance(
                schema["$defs"][name], {**private, **bad}, root_schema=schema
            )
    for bad in ({"private_coverage_id": None}, {"rider_id": uuid4()}):
        with pytest.raises(ValueError, match="complete coverage source"):
            replace(claim, **bad)
        with pytest.raises(ValueError, match="complete coverage source"):
            replace(history, **bad)

"""Source-scoped issuer uncertainty cannot authorize invented values or other revisions."""

from copy import deepcopy
from unittest.mock import Mock
from uuid import UUID, uuid5

import pytest
from familycare_api.policies.schemas import PolicyCreateRequest
from familycare_api.policies.source_scoped_identity import source_scoped_identity
from pydantic import ValidationError


def _inputs():
    document, member, candidate = UUID(int=1), UUID(int=2), UUID(int=3)
    contract = "증권번호: synthetic-policy-001"
    product = "Sample Plan_보험증권"
    fields = [
        {"field_id": "product_name", "value": "Sample Plan", "evidence_ids": [str(UUID(int=4))]}
    ]
    nodes = [
        {
            "node_id": "contract",
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": contract,
            "issue_codes": [],
        },
        {
            "node_id": "insured",
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": "피보험자: Family Member A",
            "issue_codes": [],
        },
        {
            "node_id": "product",
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": product,
            "issue_codes": [],
        },
    ]
    source = {
        "candidate_version_id": candidate,
        "pipeline_version": "retained-policy-association-v8",
        "provider_candidate_id": UUID(int=5),
        "job_id": UUID(int=6),
        "generation_id": UUID(int=7),
        "envelope_id": "a" * 64,
        "structure_json": {
            "lineage": {"document_version_id": str(document), "content_sha256": "b" * 64},
            "nodes": nodes,
        },
        "association_json": {
            "state": "RESOLVED",
            "family_member_id": str(member),
            "contract_scope_id": str(uuid5(document, "local-contract-v1:synthetic-policy-001")),
            "anchor_refs": [
                {
                    "node_id": "contract",
                    "kind": "contract",
                    "page": 1,
                    "start": 0,
                    "end": len(contract),
                },
                {
                    "node_id": "insured",
                    "kind": "insured",
                    "page": 1,
                    "start": 0,
                    "end": len(nodes[1]["text"]),
                },
            ],
        },
        "source_refs": [
            {
                "node_id": "product",
                "page": 1,
                "start": 0,
                "end": len(product),
                "primary": True,
                "source_role": "policy",
                "evidence_id": str(UUID(int=4)),
            }
        ],
    }
    version = {
        "id": candidate,
        "candidate_kind": "policy_contract",
        "status": "AI_VERIFIED",
        "generator_version": "policy-draft-normalization-v3",
    }
    checked = {
        "candidate_id": str(UUID(int=5)),
        "candidate_kind": "policy_contract",
        "status": "AI_VERIFIED",
        "fields": fields,
        "provider_request_ids": ["synthetic-independent-verifier"],
    }
    row = {
        "normalization_revision": "policy-draft-normalization-v3",
        "normalized_batch_json": {"candidates": [deepcopy(checked)]},
        "result_json": {
            "program_validation_version": "range-grounding-v4",
            "result": {"candidates": [checked]},
        },
    }
    evidence = [{"field_id": "product_name", "evidence_id": UUID(int=4)}]
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = row
    return connection, version, source, {"product_name": "Sample Plan"}, evidence, row


def test_unknown_issuer_has_explicit_source_identity_and_never_an_invented_company():
    connection, version, source, values, evidence, _ = _inputs()
    original = deepcopy((version, source, values, evidence))
    proof = source_scoped_identity(connection, version, source, values, evidence)
    assert proof is not None and proof["reason_code"] == "INSURER_SOURCE_UNVERIFIED"
    assert proof["insurer_state"] == "UNKNOWN"
    assert proof["contract_source"]["family_member_id"] == str(UUID(int=2))
    assert (version, source, values, evidence) == original
    assert "insurer" not in values


@pytest.mark.parametrize(
    "fault",
    [
        "legacy",
        "user_approval",
        "old_grounding",
        "old_receipt",
        "wrong_product",
        "wrong_citation",
        "cropped_contract",
        "ocr_product",
        "supplied_issuer",
    ],
)
def test_unknown_issuer_exception_requires_each_independent_source_boundary(fault):
    connection, version, source, values, evidence, row = _inputs()
    if fault == "legacy":
        source["pipeline_version"] = "retained-policy-association-v7"
    elif fault == "user_approval":
        version["status"] = "USER_CONFIRMED"
    elif fault == "old_grounding":
        row["result_json"]["program_validation_version"] = "range-grounding-v3"
    elif fault == "old_receipt":
        row["normalization_revision"] = "policy-draft-normalization-v2"
    elif fault == "wrong_product":
        source["structure_json"]["nodes"][2]["text"] = "Another Plan_보험증권"
    elif fault == "wrong_citation":
        evidence[0]["evidence_id"] = UUID(int=99)
    elif fault == "cropped_contract":
        source["association_json"]["anchor_refs"][0]["end"] -= 1
    elif fault == "ocr_product":
        source["structure_json"]["nodes"][2]["source_layer"] = "ocr"
    else:
        values["insurer"] = "Sample Insurer"
    assert source_scoped_identity(connection, version, source, values, evidence) is None


@pytest.mark.parametrize(
    "caption,accepted",
    [
        ("발급기관: Sample Insurer", True),
        ("보험사: Sample Insurer | synthetic note", True),
        ("보험자: Sample Insurer", False),
        ("참고회사: Sample Insurer", False),
        ("보험사: Sample Insurer Plus", False),
    ],
)
def test_later_issuer_enrichment_requires_explicit_issuer_role(caption, accepted):
    connection, version, source, values, evidence, row = _inputs()
    values["insurer"] = "Sample Insurer"
    node = {**source["structure_json"]["nodes"][2], "node_id": "issuer", "text": caption}
    source["structure_json"]["nodes"].append(node)
    source["source_refs"].append(
        {
            **source["source_refs"][0],
            "node_id": "issuer",
            "end": len(caption),
            "evidence_id": str(UUID(int=8)),
        }
    )
    evidence.append({"field_id": "insurer", "evidence_id": UUID(int=8)})
    field = {"field_id": "insurer", "value": "Sample Insurer", "evidence_ids": [str(UUID(int=8))]}
    row["result_json"]["result"]["candidates"][0]["fields"].append(field)
    row["normalized_batch_json"]["candidates"][0]["fields"].append(deepcopy(field))
    assert (
        source_scoped_identity(connection, version, source, values, evidence, allow_insurer=True)
        is not None
    ) == accepted


def test_manual_policy_creation_still_requires_both_issuer_fields():
    values = {
        "source_document_version_id": UUID(int=1),
        "source_evidence_id": UUID(int=2),
        "insurer_display": "Sample Insurer",
        "insurer_key": "sample-insurer",
        "product_display": "Sample Plan",
        "product_key": "sample-plan",
        "parties": (
            {
                "family_member_id": UUID(int=3),
                "role": "primary_insured",
                "evidence_id": UUID(int=2),
            },
        ),
    }
    assert PolicyCreateRequest.model_validate(values).insurer_display == "Sample Insurer"
    with pytest.raises(ValidationError) as error:
        PolicyCreateRequest.model_validate({**values, "insurer_display": None, "insurer_key": None})
    assert {item["loc"] for item in error.value.errors()} == {
        ("insurer_display",),
        ("insurer_key",),
    }


def test_missing_issuers_never_match_terms_even_with_equal_product_and_period():
    from familycare_api.clauses.terms_applicability import assess_terms_applicability

    from apps.api.tests.test_terms_applicability import _period

    policy, terms = _period()
    for item in (policy, terms):
        item["facts"] = [fact for fact in item["facts"] if fact["field"] != "insurer"]
    assessment = assess_terms_applicability(policy, terms)
    assert assessment.status == "UNKNOWN" and assessment.matched_by is None


def test_operational_claim_schema_preserves_missing_issuer_without_a_placeholder():
    from apps.api.tests.test_claim_workflow_contracts import (
        EXAMPLE_PATH,
        SCHEMA_PATH,
        load_json,
        load_schema_validator,
    )

    schema = load_json(SCHEMA_PATH)
    claim = load_json(EXAMPLE_PATH)["claim_case"]
    claim["insurer_key"] = None
    assert not load_schema_validator()(schema["$defs"]["ClaimCase"], claim, root_schema=schema)


def test_claim_snapshot_keeps_operational_source_ids_when_issuer_is_unknown():
    from dataclasses import asdict, replace

    from familycare_api.claims.domain import ClaimCase

    claim = ClaimCase(
        id=UUID(int=10),
        household_space_id=UUID(int=11),
        medical_event_id=UUID(int=12),
        family_member_id=UUID(int=13),
        policy_contract_id=UUID(int=14),
        rider_id=UUID(int=15),
        insurer_key=None,
    )
    before = asdict(claim)
    changed = replace(claim, version=2)
    assert changed.insurer_key is None and changed.policy_contract_id == claim.policy_contract_id
    assert asdict(claim) == before

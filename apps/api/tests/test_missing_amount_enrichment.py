"""Only a source-bound, previously removed amount may fill an empty ledger pair."""

from copy import deepcopy

import pytest
from familycare_api.policies.currency_enrichment import (
    amount_enrichment_proven,
    currency_enrichment_proven,
)

from apps.api.tests.test_guidance_amount_source import Rows, uid
from apps.api.tests.test_policy_currency_enrichment import CurrencyProof


class MissingAmountProof(CurrencyProof):
    def __init__(self):
        super().__init__()
        self.version["generator_version"] = "policy-draft-normalization-v6"
        self.source["pipeline_version"] = "retained-policy-association-v12"
        self.values["sum_assured"] = 3170000
        self.target["insured_amount"] = None
        del self.previous["field_values"]["sum_assured"]
        self.old.update(
            old_job_id=uid(20),
            old_envelope_id=self.source["envelope_id"],
            old_provider_candidate_id=uid(5),
            old_generator_revision="policy-draft-normalization-v1",
        )
        self.receipt["source_provider_request_id"] = uid(22)
        self.receipt["adjustments_json"].append(
            {
                "candidate_id": str(uid(5)),
                "field_id": "sum_assured",
                "reason": "AMOUNT_SCALED_FROM_EXPLICIT_UNIT",
            }
        )
        row = self.source["structure_json"]["nodes"][0]
        row.update(
            kind="TABLE_ROW",
            row_role="data",
            row_index=1,
            text="Sample Rider | 317",
            context_node_ids=["amount-header"],
            cells=[
                {"row_index": 1, "column_index": 0, "text": "Sample Rider"},
                {"row_index": 1, "column_index": 1, "text": "317"},
            ],
        )
        header = {
            "node_id": "amount-header",
            "kind": "TABLE_ROW",
            "row_role": "header",
            "row_index": 0,
            "source_layer": "native",
            "page_number": 1,
            "text": "담보명 | 가입금액(만원)",
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "담보명"},
                {"row_index": 0, "column_index": 1, "text": "가입금액(만원)"},
            ],
        }
        self.source["structure_json"]["nodes"].append(header)
        self.source["source_refs"][0]["end"] = len(row["text"])
        self.old["source_refs"][0]["end"] = len(row["text"])
        self.source["source_refs"].append(
            {
                **self.source["source_refs"][0],
                "evidence_id": str(uid(15)),
                "node_id": "amount-header",
                "primary": False,
                "end": len(header["text"]),
            }
        )
        checked = self.receipt["result_json"]["result"]["candidates"][0]
        draft = self.receipt["normalized_batch_json"]["candidates"][0]
        for candidate in (checked, draft):
            next(f for f in candidate["fields"] if f["field_id"] == "sum_assured")["value"] = (
                3170000
            )
        for field in checked["fields"]:
            if field["field_id"] in {"sum_assured", "currency"}:
                field["evidence_ids"].append(str(uid(15)))
                self.evidence.append({"field_id": field["field_id"], "evidence_id": uid(15)})
        raw = deepcopy(draft)
        raw["fields"] = [f for f in raw["fields"] if f["field_id"] != "currency"]
        next(f for f in raw["fields"] if f["field_id"] == "sum_assured")["value"] = 317
        normalized = deepcopy(raw)
        normalized["fields"] = [f for f in normalized["fields"] if f["field_id"] != "sum_assured"]
        self.prior = {
            "request_id": "synthetic-raw",
            "response_json": {"candidates": [raw]},
            "normalized_batch_json": {"candidates": [normalized]},
            "adjustments_json": [
                {
                    "candidate_id": str(uid(5)),
                    "field_id": "sum_assured",
                    "reason": "OPTIONAL_FIELD_UNSUPPORTED",
                }
            ],
            "verifier_response": {
                "decisions": [
                    {
                        "candidate_id": str(uid(5)),
                        "decision": "approved",
                        "issue_codes": [],
                        "evidence_ids": [str(uid(9))],
                    }
                ]
            },
        }

    def execute(self, query, params):
        if "analysis_candidate_versions c" in query:
            return super().execute(query, params)
        if "FROM policy_range_replay_sources receipt" in query:
            assert "normalization_revision='policy-draft-normalization-v1'" in query
            assert "verification.state='SUCCEEDED'" in query and "FOR SHARE" in query
            assert "current_range.envelope_json=old_range.envelope_json" in query
            assert "current_range.envelope_id=old_range.envelope_id" in query
            assert "privacy_fingerprint" not in query
            assert params[-1] == self.receipt["source_provider_request_id"]
            return Rows([self.prior] if self.prior is not None else [])
        assert f"normalization_revision='{self.version['generator_version']}'" in query
        return Rows([self.receipt])

    def check(self):
        return amount_enrichment_proven(
            self, self.version, self.source, self.values, self.evidence, self.target, self.previous
        )


def test_explicit_unit_recovery_preserves_normalized_and_grounded_evidence_distinction():
    proof = MissingAmountProof()
    before = deepcopy((proof.values, proof.previous, proof.target, proof.receipt, proof.prior))
    assert proof.check()
    assert (proof.values, proof.previous, proof.target, proof.receipt, proof.prior) == before
    assert not currency_enrichment_proven(
        proof,
        proof.version,
        proof.source,
        proof.values,
        proof.evidence,
        proof.target,
        proof.previous,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "known_amount",
        "known_currency",
        "old_field_present",
        "other_field_change",
        "version_changed",
        "user_publication",
        "old_review",
        "old_revision",
        "new_revision",
        "pipeline",
        "removal_reason",
        "new_amount_reason",
        "new_currency_reason",
        "raw_currency",
        "raw_unchanged_amount",
        "raw_citation_changed",
        "wrong_candidate",
        "old_normalized_amount",
        "missing_receipt",
        "verifier_rejected",
        "verifier_missing_citation",
        "same_old_verifier",
        "wrong_unit",
        "ocr_row",
    ],
)
def test_missing_amount_recovery_rejects_unproved_or_user_owned_changes(fault):
    p = MissingAmountProof()
    if fault == "known_amount":
        p.target["insured_amount"] = 317
    elif fault == "known_currency":
        p.target["currency"] = "KRW"
    elif fault == "old_field_present":
        p.previous["field_values"]["sum_assured"] = None
    elif fault == "other_field_change":
        p.values["benefit_type"] = "fixed"
    elif fault == "version_changed":
        p.target["version"] += 1
    elif fault == "user_publication":
        p.previous["authority"] = "USER_CONFIRMED"
    elif fault == "old_review":
        p.old = None
    elif fault == "old_revision":
        p.old["old_generator_revision"] = "policy-draft-normalization-v2"
    elif fault == "new_revision":
        p.version["generator_version"] = "policy-draft-normalization-v5"
    elif fault == "pipeline":
        p.source["pipeline_version"] = "retained-policy-association-v9"
    elif fault == "removal_reason":
        p.prior["adjustments_json"] = []
    elif fault == "new_amount_reason":
        p.receipt["adjustments_json"] = p.receipt["adjustments_json"][:1]
    elif fault == "new_currency_reason":
        p.receipt["adjustments_json"] = p.receipt["adjustments_json"][1:]
    elif fault == "raw_currency":
        p.prior["response_json"]["candidates"][0]["fields"].append(
            {"field_id": "currency", "value": "KRW", "evidence_ids": [str(uid(9))]}
        )
    elif fault == "raw_unchanged_amount":
        next(
            f
            for f in p.prior["response_json"]["candidates"][0]["fields"]
            if f["field_id"] == "sum_assured"
        )["value"] = 3170000
    elif fault == "raw_citation_changed":
        next(
            f
            for f in p.prior["response_json"]["candidates"][0]["fields"]
            if f["field_id"] == "sum_assured"
        )["evidence_ids"] = [str(uid(15))]
    elif fault == "wrong_candidate":
        p.old["old_provider_candidate_id"] = uid(99)
    elif fault == "old_normalized_amount":
        p.prior["normalized_batch_json"]["candidates"][0]["fields"].append(
            {"field_id": "sum_assured", "value": 317, "evidence_ids": [str(uid(9))]}
        )
    elif fault == "missing_receipt":
        p.prior = None
    elif fault == "verifier_rejected":
        p.prior["verifier_response"]["decisions"][0]["decision"] = "needs_review"
    elif fault == "verifier_missing_citation":
        p.prior["verifier_response"]["decisions"][0]["evidence_ids"] = []
    elif fault == "same_old_verifier":
        p.prior["request_id"] = "synthetic-independent-verifier"
    elif fault == "wrong_unit":
        p.source["structure_json"]["nodes"][-1]["cells"][1]["text"] = "가입금액(원)"
    elif fault == "ocr_row":
        p.source["structure_json"]["nodes"][0]["source_layer"] = "ocr"
    assert not p.check()


def _proven_context_money():
    proof = MissingAmountProof()
    proof.source["pipeline_version"] = "retained-policy-association-v13"
    proof.version["generator_version"] = "policy-draft-normalization-v7"
    proof.receipt["result_json"]["program_validation_version"] = "range-grounding-v5"
    for field in proof.receipt["normalized_batch_json"]["candidates"][0]["fields"]:
        if field["field_id"] in {"sum_assured", "currency"}:
            field["evidence_ids"].append(str(uid(15)))
    proof.prior["verifier_response"]["decisions"][0]["evidence_ids"].append(str(uid(15)))
    return proof


def test_v13_proven_header_keeps_original_money_citation_and_requires_fresh_verification():
    proof = _proven_context_money()
    assert proof.check()
    proof.prior["verifier_response"]["decisions"][0]["evidence_ids"].remove(str(uid(15)))
    assert not proof.check()


@pytest.mark.parametrize("fault", ["removed_primary", "foreign_context", "ocr_header"])
def test_v13_context_cannot_replace_original_amount_source_or_add_unproved_evidence(fault):
    proof = _proven_context_money()
    if fault == "ocr_header":
        proof.source["structure_json"]["nodes"][-1]["source_layer"] = "ocr"
    else:
        for candidate in (
            proof.receipt["normalized_batch_json"]["candidates"][0],
            proof.receipt["result_json"]["result"]["candidates"][0],
        ):
            for field in candidate["fields"]:
                if field["field_id"] in {"sum_assured", "currency"}:
                    field["evidence_ids"] = (
                        [str(uid(15))]
                        if fault == "removed_primary"
                        else [str(uid(9)), str(uid(999))]
                    )
    assert not proof.check()

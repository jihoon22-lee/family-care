"""Absent currency needs a fresh bounded receipt and an independent original amount witness."""

from copy import deepcopy
from uuid import uuid5

import pytest
from familycare_api.policies.currency_enrichment import currency_enrichment_proven

from apps.api.tests.test_guidance_amount_source import Rows, SourceDatabase, uid


class CurrencyProof:
    def __init__(self):
        original = SourceDatabase()
        pub = original.publications[0]
        self.values = {**pub["field_values"], "benefit_type": "unknown"}
        self.version = {
            "id": uid(4),
            "household_space_id": uid(1),
            "candidate_kind": "rider",
            "status": "AI_VERIFIED",
            "generator_version": "policy-draft-normalization-v4",
        }
        contract = "증권번호: synthetic-currency-001"
        projection = original.projection
        projection["nodes"].append(
            {
                "node_id": "contract",
                "kind": "TEXT_LINE",
                "page_number": 1,
                "text": contract,
                "source_layer": "native",
            }
        )
        association = {
            "state": "RESOLVED",
            "family_member_id": str(uid(10)),
            "contract_scope_id": str(uuid5(uid(6), "local-contract-v1:synthetic-currency-001")),
            "anchor_refs": [
                {
                    "kind": "contract",
                    "node_id": "contract",
                    "page": 1,
                    "start": 0,
                    "end": len(contract),
                }
            ],
        }
        self.source = {
            "candidate_version_id": uid(4),
            "provider_candidate_id": uid(5),
            "pipeline_version": "retained-policy-association-v9",
            "structure_json": projection,
            "source_refs": pub["source_refs"],
            "association_json": association,
            "generation_id": uid(11),
            "document_version_id": uid(6),
            "extraction_id": uid(7),
            "job_id": uid(12),
            "envelope_id": uid(13),
        }
        self.old = {
            k: deepcopy(self.source[k])
            for k in (
                "source_refs",
                "association_json",
                "generation_id",
                "document_version_id",
                "extraction_id",
            )
        }
        self.old["name_evidence"] = [str(uid(9))]
        self.evidence = [{"field_id": k, "evidence_id": uid(9)} for k in self.values]
        self.target = {**original.rider, "currency": None}
        self.previous = {
            "candidate_version_id": uid(14),
            "ledger_version": 1,
            "authority": "PROGRAM_VERIFIED",
            "field_values": {k: v for k, v in self.values.items() if k != "currency"},
        }
        fields = [
            {"field_id": k, "value": v, "evidence_ids": [str(uid(9))]}
            for k, v in self.values.items()
        ]
        checked = {
            "candidate_id": str(uid(5)),
            "candidate_kind": "rider",
            "status": "AI_VERIFIED",
            "provider_request_ids": ["synthetic-raw", "synthetic-independent-verifier"],
            "fields": fields,
        }
        self.receipt = {
            "result_json": {
                "program_validation_version": "range-grounding-v4",
                "result": {"candidates": [checked]},
            },
            "normalized_batch_json": {
                "candidates": [
                    {
                        "candidate_id": str(uid(5)),
                        "candidate_kind": "rider",
                        "fields": [deepcopy(f) for f in fields if f["field_id"] != "benefit_type"],
                    }
                ]
            },
            "adjustments_json": [
                {
                    "candidate_id": str(uid(5)),
                    "field_id": "currency",
                    "reason": "CURRENCY_DERIVED_FROM_AMOUNT",
                }
            ],
        }

    def execute(self, query, params):
        if "analysis_candidate_versions c" in query:
            assert "c.is_current" in query and "c.actor_id IS NULL" in query
            assert "policy_structuring_source_current" in query and "FOR SHARE" in query
            return Rows([self.old] if self.old is not None else [])
        assert "normalization_revision='policy-draft-normalization-v4'" in query
        return Rows([self.receipt])

    def check(self):
        return currency_enrichment_proven(
            self, self.version, self.source, self.values, self.evidence, self.target, self.previous
        )


def test_currency_only_proof_accepts_grounder_added_unknown_without_rewriting_draft():
    proof = CurrencyProof()
    assert proof.check()
    proof.receipt["adjustments_json"][0]["reason"] = "CURRENCY_EVIDENCE_REALIGNED"
    assert proof.check()


@pytest.mark.parametrize(
    "fault",
    [
        "revision",
        "pipeline",
        "grounding",
        "reason",
        "currency",
        "amount",
        "ledger_version",
        "user_authority",
        "old_review",
        "other_source",
        "citation",
        "unit",
        "ocr",
        "verifier",
        "draft_currency",
        "draft_citation",
    ],
)
def test_currency_enrichment_rejects_non_equivalent_or_unproved_changes(fault):
    p = CurrencyProof()
    if fault == "revision":
        p.version["generator_version"] = "policy-draft-normalization-v3"
    elif fault == "pipeline":
        p.source["pipeline_version"] = "retained-policy-association-v8"
    elif fault == "grounding":
        p.receipt["result_json"]["program_validation_version"] = "range-grounding-v3"
    elif fault == "reason":
        p.receipt["adjustments_json"] = []
    elif fault == "currency":
        p.target["currency"] = "USD"
    elif fault == "amount":
        p.values["sum_assured"] = 318
    elif fault == "ledger_version":
        p.target["version"] += 1
    elif fault == "user_authority":
        p.previous["authority"] = "USER_CONFIRMED"
    elif fault == "old_review":
        p.old = None
    elif fault == "other_source":
        p.old["generation_id"] = uid(99)
    elif fault == "citation":
        p.old["name_evidence"] = [str(uid(99))]
    elif fault == "unit":
        p.source["structure_json"]["nodes"][0]["text"] = "Sample Rider fixed sum assured: 317 USD"
    elif fault == "ocr":
        p.source["structure_json"]["nodes"][0]["source_layer"] = "ocr"
    elif fault == "verifier":
        p.receipt["result_json"]["result"]["candidates"][0]["provider_request_ids"] = []
    elif fault == "draft_currency":
        p.receipt["normalized_batch_json"]["candidates"][0]["fields"][2]["value"] = "USD"
    elif fault == "draft_citation":
        p.receipt["normalized_batch_json"]["candidates"][0]["fields"][2]["evidence_ids"] = [
            str(uid(99))
        ]
    assert not p.check()


def test_table_currency_uses_full_data_row_and_grounder_added_header_proof():
    p = CurrencyProof()
    nodes = p.source["structure_json"]["nodes"]
    row = nodes[0]
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
        "text": "담보명 | 가입금액(원)",
        "cells": [
            {"row_index": 0, "column_index": 0, "text": "담보명"},
            {"row_index": 0, "column_index": 1, "text": "가입금액(원)"},
        ],
    }
    nodes.append(header)
    p.source["source_refs"][0]["end"] = len(row["text"])
    p.old["source_refs"][0]["end"] = len(row["text"])
    p.source["source_refs"].append(
        {
            **p.source["source_refs"][0],
            "evidence_id": str(uid(15)),
            "node_id": header["node_id"],
            "end": len(header["text"]),
            "primary": False,
        }
    )
    checked = p.receipt["result_json"]["result"]["candidates"][0]
    for field in checked["fields"]:
        if field["field_id"] in {"sum_assured", "currency"}:
            field["evidence_ids"].append(str(uid(15)))
            p.evidence.append({"field_id": field["field_id"], "evidence_id": uid(15)})
    assert p.check()
    # A receipt with only a header does not prove which Rider owns the amount.
    for field in checked["fields"]:
        if field["field_id"] == "currency":
            field["evidence_ids"] = [str(uid(15))]
    p.evidence = [
        e for e in p.evidence if e["field_id"] != "currency" or e["evidence_id"] == uid(15)
    ]
    assert not p.check()

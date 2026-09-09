"""Synthetic independent source packets stay data-only across review proposals."""

import hashlib
import json
from copy import deepcopy
from uuid import UUID

import pytest
from familycare_worker.ai.provider import ProviderCallMetadata, ProviderResponse, ProviderUsage

from workers.analyzer.tests.test_terms_structurer import envelope, response_for


def sources(count=2):
    index, packets = [], []
    for number in range(count):
        ref = {
            "kind": "OPERATIONAL_RIDER",
            "contract_id": str(UUID(int=1000 + number, version=4)),
            "coverage_id": str(UUID(int=2000 + number, version=4)),
        }
        packet_id = f"internal-review-packet-{number}"
        original = envelope().model_dump()
        original["source"]["document_version_id"] = str(UUID(int=3000 + number, version=4))
        index.append(
            {
                "ref": ref,
                "native_ref": None,
                "contract_label": "Sample Policy",
                "coverage_label": f"Sample Coverage {number}",
                "enrollment_decision": "MATCH",
                "enrollment_authority": "POLICY_LEDGER",
                "source_state": "AVAILABLE",
                "packet_ids": [packet_id],
                "reason_codes": [],
            }
        )
        packets.append(
            {
                "packet_id": packet_id,
                "coverage_ref": ref,
                "envelope": original,
                "link_id": str(UUID(int=4000 + number, version=4)),
                "clause_id": str(UUID(int=5000 + number, version=4)),
            }
        )
    result = {
        "schema_revision": "guidance-review-sources-v1",
        "household_space_id": str(UUID(int=1, version=4)),
        "family_member_id": str(UUID(int=2, version=4)),
        "medical_event_id": str(UUID(int=3, version=4)),
        "event_version": 1,
        "event_date": "2026-09-01",
        "index": index,
        "packets": packets,
        "manifest": {
            "complete": True,
            "total_coverage_count": count,
            "indexed_coverage_count": count,
            "omitted_coverage_count": 0,
        },
    }
    result["digest_sha256"] = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def build(source=None, event=None, local=None, **kwargs):
    from familycare_worker.ai.guidance_reviewer import build_review_request

    source = source or sources()
    return build_review_request(
        sources=source,
        event=event
        or {
            "situation": "Synthetic admission for five days.",
            "facts": {"MedicalEvent.admission_days": {"value": 5, "confirmation": "user"}},
        },
        local_guidance=local
        or {
            "candidates": [
                {
                    "ref": source["index"][0]["ref"],
                    "group": "PRIMARY",
                    "condition_result": "MATCH",
                    "assumptions": ["DOCUMENT_CONTINUITY_ASSUMED"],
                    "estimate": {
                        "kind": "POINT",
                        "amount": "50",
                        "currency": "KRW",
                        "formula": "50 × 1",
                    },
                }
            ]
        },
        model="synthetic-review-model",
        **kwargs,
    )


class FakeProvider:
    def __init__(self, mutate=lambda result: None):
        self.calls = []
        self.mutate = mutate
        self.metadata = ProviderCallMetadata(
            usage=ProviderUsage(100, 50, 150), model="synthetic-review-model"
        )

    def complete(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        packets = kwargs["input_payload"]["source_packets"]
        result = {
            "schema_revision": "guidance-review-proposals-v1",
            "reviewed_packet_aliases": [item["packet_alias"] for item in packets],
            "unreviewed_packet_aliases": [],
            "suggestions": [
                {
                    "packet_alias": item["packet_alias"],
                    "kind": "ADDITIONAL_CANDIDATE",
                    "graph": response_for(item["envelope"]),
                    "affected_fact_paths": ["MedicalEvent.admission_days"],
                    "proposed_amount": "999.50",
                }
                for item in packets
            ],
        }
        self.mutate(result)
        return ProviderResponse(result, "resp_synthetic_review", self.metadata)


def test_independent_index_survives_local_candidate_filter_and_hydrates_one_call():
    original = sources()
    request = build(original)
    provider = FakeProvider()
    result = request.call(provider)
    assert len(provider.calls) == 1
    sent = provider.calls[0]["input_payload"]
    assert len(sent["independent_source_index"]) == 2
    assert len(sent["local_answer"]["candidates"]) == 1
    assert len(sent["source_packets"]) == len(result.suggestions) == 2
    assert result.metadata == provider.metadata
    assert result.request_id == "resp_synthetic_review"
    for index, suggestion in enumerate(result.suggestions):
        assert suggestion.packet_id == original["packets"][index]["packet_id"]
        assert (
            suggestion.graph.sources[0].document_version_id
            == original["packets"][index]["envelope"]["source"]["document_version_id"]
        )
        assert suggestion.proposed_amount == "999.50"
    assert set(request.document_version_ids) == {
        UUID(item["envelope"]["source"]["document_version_id"]) for item in original["packets"]
    }
    assert result.omitted_packet_ids == ()
    stored = result.to_payload()
    assert "metadata" not in stored and "request_id" not in stored
    assert stored["suggestions"][0]["packet_id"] == original["packets"][0]["packet_id"]
    assert stored["suggestions"][0]["graph"]["schema_revision"] == "terms-semantic-v1"
    assert json.loads(json.dumps(stored)) == stored


def test_source_packet_aliases_are_independent_even_with_repeated_original_names():
    request = build()
    packets = request.payload["source_packets"]
    assert (
        packets[0]["envelope"]["source"]["source_id"]
        != packets[1]["envelope"]["source"]["source_id"]
    )
    assert (
        packets[0]["envelope"]["regions"][0]["citations"][0]["citation_id"]
        != packets[1]["envelope"]["regions"][0]["citations"][0]["citation_id"]
    )
    provider = FakeProvider(
        lambda result: result["suggestions"][0].update(
            graph=deepcopy(result["suggestions"][1]["graph"])
        )
    )
    from familycare_worker.ai.guidance_reviewer import GuidanceReviewInvalid

    with pytest.raises(GuidanceReviewInvalid) as raised:
        request.call(provider)
    assert raised.value.metadata == provider.metadata


def test_minimization_removes_event_names_paths_and_all_original_packet_identifiers():
    original = sources()
    secret = original["household_space_id"]
    text = f"Admin A admission {secret} /tmp/synthetic-private-policy.pdf"
    event = {
        "situation": text,
        "facts": {
            "MedicalEvent.admission_days": {"value": 5, "confirmation": "ai_structured"},
            "MedicalEvent.performed": True,
        },
    }
    request = build(original, event=event, sensitive_terms=("Admin A",))
    sent = json.dumps(request.payload)
    for forbidden in (
        "Admin A",
        secret,
        "/tmp/synthetic-private-policy.pdf",
        "household_space_id",
        "family_member_id",
        "medical_event_id",
    ):
        assert forbidden not in sent
    for packet in original["packets"]:
        assert packet["packet_id"] not in sent
        assert packet["coverage_ref"]["coverage_id"] not in sent
        assert packet["envelope"]["source"]["document_version_id"] not in sent
    facts = request.payload["event"]["facts"]
    assert not any(fact["field_path"] == "MedicalEvent.performed" for fact in facts)
    assert facts[0]["confirmation"] == "ai_structured"


def test_whole_packet_omissions_include_schema_and_instruction_in_input_bound():
    from familycare_worker.ai.guidance_reviewer import MAX_REQUEST_BYTES

    original = sources(8)
    for packet in original["packets"]:
        for region in packet["envelope"]["regions"]:
            citation = region["citations"][0]
            citation["text"] = "Synthetic words " * 200
            citation["end"] = citation["start"] + len(citation["text"])
    request = build(original)
    assert 0 < len(request.payload["source_packets"]) < 8
    assert request.wire_byte_count <= MAX_REQUEST_BYTES == 32768
    assert request.conservative_token_bound == request.wire_byte_count
    assert request.omitted_packet_ids
    assert request.omitted_coverage_aliases
    supplied = {item["packet_alias"] for item in request.payload["source_packets"]}
    assert len(supplied) + len(request.omitted_packet_ids) == 8


@pytest.mark.parametrize(
    "fault", ["extra", "packet", "fact", "amount", "citation", "scope", "instruction"]
)
def test_hostile_or_unrecognized_output_is_rejected_without_losing_usage(fault):
    from familycare_worker.ai.guidance_reviewer import GuidanceReviewInvalid

    def mutate(result):
        suggestion = result["suggestions"][0]
        if fault == "extra":
            result["execute"] = "synthetic-injected-command"
        elif fault == "packet":
            suggestion["packet_alias"] = "packet-999"
        elif fault == "fact":
            suggestion["affected_fact_paths"] = ["AppUser.password"]
        elif fault == "amount":
            suggestion["proposed_amount"] = "NaN"
        elif fault == "citation":
            suggestion["graph"]["citations"][0]["text"] = "synthetic invented quote"
        elif fault == "scope":
            result["unreviewed_packet_aliases"] = result["reviewed_packet_aliases"][:1]
        else:
            suggestion["graph"]["nodes"][0]["payload"] = {
                "kind": "calculation",
                "code": "synthetic-injected-command",
            }

    provider = FakeProvider(mutate)
    with pytest.raises(GuidanceReviewInvalid) as raised:
        build().call(provider)
    assert raised.value.metadata == provider.metadata
    assert "synthetic-injected-command" not in str(raised.value)


def test_schema_uses_canonical_graph_and_nullable_advisory_amount():
    from familycare_worker.ai.guidance_reviewer import guidance_review_schema

    schema = guidance_review_schema()
    assert "TermsSemanticKnowledge" in schema["$defs"]
    assert schema["additionalProperties"] is False
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])


def test_index_coverage_without_original_packet_is_reported_as_unreviewed_scope():
    original = sources(3)
    original["packets"].pop()
    original["index"][2].update(packet_ids=[], source_state="UNAVAILABLE")
    original["manifest"]["complete"] = False
    request = build(original)
    assert "coverage-3" in request.omitted_coverage_aliases
    assert len(request.payload["independent_source_index"]) == 3
    result = request.call(FakeProvider())
    assert "coverage-3" in result.to_payload()["omitted_coverage_aliases"]

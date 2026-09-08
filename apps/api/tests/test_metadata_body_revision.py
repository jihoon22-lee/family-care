"""The neutral metadata contract preserves old proofs and requires v3 range evidence."""

import json
from pathlib import Path
from typing import Any

from scripts.check_document_contracts import validate_schema_instance

ROOT = Path(__file__).resolve().parents[3]


def _proposal() -> dict[str, Any]:
    span = {
        "node_id": "synthetic-title",
        "page_number": 1,
        "start": 0,
        "end": 4,
        "text": "보험약관",
        "anchor_start": 0,
        "anchor_end": 4,
    }
    return {
        "schema_version": "1",
        "revision": "document-metadata-v3",
        "generation_id": "00000000-0000-4000-8000-000000000001",
        "structure_identity_sha256": "a" * 64,
        "unresolved_pages": [],
        "components": [
            {
                "identity": "b" * 64,
                "role": "terms",
                "page_start": 1,
                "page_end": 1,
                "role_spans": [span],
                "facts": [],
                "conflicting_fields": [],
                "unresolved_fields": [],
                "authority": "CONTENT_CLASSIFICATION_ONLY",
                "range_evidence": [
                    {
                        "page_number": 1,
                        "basis": "FORMAL_METADATA",
                        "previous_page": None,
                        "article_numbers": [],
                        "article_sequence_verified": False,
                        "role_span_indices": [0],
                    }
                ],
            }
        ],
    }


def _errors(proposal: dict[str, Any]) -> list[str]:
    schema = json.loads(
        (ROOT / "packages/contracts/schemas/document-metadata-proposal.v1.schema.json").read_text()
    )
    return validate_schema_instance(schema, proposal)


def test_v3_proposal_carries_source_addressed_range_evidence() -> None:
    assert _errors(_proposal()) == []


def test_v3_cannot_omit_range_evidence_or_claim_enrollment_authority() -> None:
    proposal = _proposal()
    del proposal["components"][0]["range_evidence"]
    assert _errors(proposal)
    proposal = _proposal()
    proposal["components"][0]["range_evidence"][0]["basis"] = "ENROLLMENT_CONFIRMED"
    assert _errors(proposal)


def test_old_proposals_remain_valid_without_new_evidence() -> None:
    proposal = _proposal()
    del proposal["components"][0]["range_evidence"]
    for revision in ("document-metadata-v1", "document-metadata-v2"):
        proposal["revision"] = revision
        assert _errors(proposal) == []

"""Primary support headers cannot displace the independently located Rider name."""

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from familycare_api.insurance_reconciliation.canonical_repository import _name_source
from familycare_api.policies.enrollment_locator import physical_enrollment_locator

from apps.api.tests.test_enrollment_locator import _refs, _source

GENERATION = UUID(int=17)


class Inventory:
    def __init__(self, source: dict[str, Any]):
        self.source = source
        self.pages: list[int] = []

    def page(self, digest: str, page: int) -> dict[UUID, dict[str, Any]]:
        assert digest == self.source["lineage"]["content_sha256"]
        self.pages.append(page)
        return {GENERATION: self.source}


def _row() -> dict[str, Any]:
    return {
        "content_sha256": "a" * 64,
        "generation_id": GENERATION,
        "original_rider_name": "Sample Rider",
    }


def _citations(source: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    refs = _refs(source, *keys)
    for number, ref in enumerate(refs, 1):
        ref["evidence_id"] = str(UUID(int=number))
    return refs


@pytest.mark.parametrize(
    "keys",
    [
        ("row",),
        ("header", "row"),
        ("row", "header"),
        ("header", "row", "line"),
        ("line", "header", "row"),
    ],
)
def test_actual_name_evidence_is_selected_without_dropping_support_or_equivalent_views(keys):
    source = _source()
    refs = _citations(source, *keys)
    before = deepcopy((source, refs))
    proof = _name_source(Inventory(source), _row(), refs, {1})
    assert proof is not None
    locator, evidence = proof
    assert locator == physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    selected = next(ref for ref in refs if UUID(ref["evidence_id"]) == evidence)
    assert selected["node_id"] in {"row", "line"}
    assert physical_enrollment_locator(source, "Sample Rider", [selected]) == locator
    assert (source, refs) == before
    assert _name_source(Inventory(source), _row(), list(reversed(refs)), {1}) == proof


def test_primary_header_on_another_unbound_page_does_not_choose_the_name_page():
    source = _source()
    next(n for n in source["nodes"] if n["node_id"] == "header")["page_number"] = 2
    refs = _citations(source, "header", "row")
    inventory = Inventory(source)
    proof = _name_source(inventory, _row(), refs, {1})
    assert proof is not None and proof[0]["physical_page"] == 1
    assert proof[1] == UUID(refs[1]["evidence_id"])
    assert inventory.pages == [1]
    assert _name_source(Inventory(source), _row(), refs, {2}) is None


def test_two_same_name_rows_at_distinct_native_positions_remain_ambiguous():
    source = _source()
    other = deepcopy(source)
    for node in other["nodes"]:
        node["node_id"] += "-other"
        node["context_node_ids"] = [key + "-other" for key in node.get("context_node_ids", ())]
        for span in node.get("source_spans", ()):
            span["block_node_id"] += "-other"
        if node.get("bbox"):
            node["bbox"][1] += 50
            node["bbox"][3] += 50
        for cell in node.get("cells", ()):
            if cell.get("bbox"):
                cell["bbox"][1] += 50
                cell["bbox"][3] += 50
    source["nodes"].extend(other["nodes"])
    refs = _citations(source, "header", "row", "row-other")
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "row")) is not None
    assert (
        physical_enrollment_locator(source, "Sample Rider", _refs(source, "row-other")) is not None
    )
    assert _name_source(Inventory(source), _row(), refs, {1}) is None


@pytest.mark.parametrize(
    "fault",
    [
        "header_only",
        "cropped_header",
        "ocr_header",
        "ocr_row",
        "wrong_page",
        "wrong_role",
        "missing_node",
        "name_not_primary",
    ],
)
def test_invalid_original_primary_citations_cannot_be_ignored(fault):
    source = _source()
    refs = _citations(source, "header", "row")
    if fault == "header_only":
        refs = refs[:1]
    elif fault == "cropped_header":
        refs[0]["end"] -= 1
    elif fault == "ocr_header":
        next(n for n in source["nodes"] if n["node_id"] == "header")["source_layer"] = "ocr"
    elif fault == "ocr_row":
        next(n for n in source["nodes"] if n["node_id"] == "row")["source_layer"] = "ocr"
    elif fault == "wrong_page":
        refs[0]["page"] = 2
    elif fault == "wrong_role":
        refs[0]["source_role"] = "terms"
    elif fault == "missing_node":
        refs[0]["node_id"] = "missing"
    elif fault == "name_not_primary":
        refs[1]["primary"] = False
    assert _name_source(Inventory(source), _row(), refs, {1}) is None


def test_repository_proposal_preserves_every_original_name_ref(monkeypatch):
    import hashlib

    from familycare_api.common.scope import HouseholdScope
    from familycare_api.insurance_reconciliation import canonical_repository as repository

    source = _source()
    aliases = {
        n["node_id"]: hashlib.sha256(n["node_id"].encode()).hexdigest() for n in source["nodes"]
    }
    refs = _citations(source, "header", "row", "line")
    for node in source["nodes"]:
        node["node_id"] = aliases[node["node_id"]]
        node["context_node_ids"] = [aliases[key] for key in node.get("context_node_ids", ())]
        for span in node.get("source_spans", ()):
            span["block_node_id"] = aliases[span["block_node_id"]]
    for ref in refs:
        ref.update(
            node_id=aliases[ref["node_id"]],
            document_version_id=str(UUID(int=20)),
            extraction_id=str(UUID(int=21)),
        )
    record = {
        "name": "Sample Rider",
        "certificate_review": {
            "name": "Sample Rider",
            "enrollment_decision": "MATCH",
            "component_class": "BENEFIT_COVERAGE",
            "evidence_locations": [
                {"document_alias": "Synthetic Policy", "line": 700, "physical_page": 1}
            ],
        },
    }
    common = {"insured_amount": 317, "currency": "KRW", "display_name": "Sample Rider"}
    coverage = {
        **common,
        "id": UUID(int=31),
        "import_run_id": UUID(int=32),
        "knowledge_contract_id": UUID(int=33),
        "family_member_id": UUID(int=34),
        "source_record_json": record,
        "source_record_digest_sha256": repository._digest(record),
        "snapshot_policy_id": None,
        "rider_id": None,
        "source_contract_key": "synthetic-contract",
    }
    binding = {
        "id": UUID(int=35),
        "source_alias": "Synthetic Policy",
        "document_version_id": UUID(int=20),
        "content_sha256": "a" * 64,
        "page_count": 1,
        "document_kind": "policy",
    }
    publication = {
        **common,
        **_row(),
        "candidate_version_id": UUID(int=36),
        "policy_contract_id": UUID(int=37),
        "rider_id": UUID(int=38),
        "source_refs": refs,
        "name_ids": [r["evidence_id"] for r in refs],
        "family_member_id": UUID(int=34),
        "document_version_id": UUID(int=20),
        "ledger_version": 1,
        "publication_authority": "PROGRAM_VERIFIED",
        "name_source_candidate_version_id": UUID(int=36),
    }

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, query, params):
            if "SELECT c.*, k.source_contract_key" in query:
                return Rows([coverage])
            if "SELECT b.*, d.source_alias" in query:
                return Rows([binding])
            if "SELECT p.candidate_version_id" in query:
                return Rows([publication])
            assert any(
                fragment in query
                for fragment in (
                    "SELECT link.*",
                    "SELECT id,policy_contract_id",
                    "SELECT id,rider_id",
                )
            )
            return Rows([])

    monkeypatch.setattr(repository, "_SourceInventory", lambda *_: Inventory(source))
    before = deepcopy(refs)
    proposals = repository._proposals(Connection(), HouseholdScope(UUID(int=39)))
    assert len(proposals) == 1
    proof = proposals[0].proofs[0]
    assert proof["source_refs"] == before
    assert proof["evidence_id"] == refs[1]["evidence_id"]
    assert proof["physical_locator"]["name_bbox"] == [10.125, 20.25, 73.625, 30.75]
    assert refs == before

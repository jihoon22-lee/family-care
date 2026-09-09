"""A complete original packet can still contain an independently unreviewed benefit."""

from uuid import UUID

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance_review import reassessment
from familycare_api.guidance_review.sources import (
    ReviewCoverageSource,
    ReviewNativePacket,
    ReviewSourceManifest,
    ReviewSources,
    _regions,
)
from familycare_api.terms_knowledge.repository import SemanticSourcePlan
from familycare_api.terms_knowledge.work_repository import build_work_envelope

from apps.api.tests.test_terms_semantic_core import amount
from apps.api.tests.test_terms_source_verification import source_fixture


def test_complete_four_region_packet_has_valid_300_root_but_incomplete_interpretation(monkeypatch):
    graph, snapshots = source_fixture()
    plan = SemanticSourcePlan({}, "c" * 64, snapshots["terms"])
    envelope = build_work_envelope(plan, ("region-daily", "region-unrelated"))
    by_node = {c.node_id: c.model_dump(mode="json") for r in envelope.regions for c in r.citations}
    ids = {c["citation_id"]: by_node[c["node_id"]]["citation_id"] for c in graph["citations"]}
    graph["citations"] = [by_node[c["node_id"]] for c in graph["citations"]]
    for node in graph["nodes"]:
        node["citation_ids"] = [ids[value] for value in node["citation_ids"]]
    graph["nodes"] = [node for node in graph["nodes"] if node["node_id"] != "unrelated"]
    graph["roots"] = ["daily"]
    graph["processing"]["consumed_region_ids"].remove("region-unrelated")
    graph["processing"]["unresolved_region_ids"] = ["region-unrelated"]
    ref = CanonicalCoverageRef(
        kind="OPERATIONAL_RIDER", contract_id=UUID(int=200), coverage_id=UUID(int=201)
    )
    packet = ReviewNativePacket(
        "synthetic-packet",
        ref,
        UUID(int=202),
        1,
        UUID(int=203),
        UUID(int=204),
        "d" * 64,
        "e" * 64,
        0,
        envelope.model_dump_json(),
    )
    regions = _regions([packet], [packet])
    assert regions[0].omitted_region_count == 0
    index = ReviewCoverageSource(
        ref,
        "Sample Policy",
        "Sample Coverage",
        "MATCH",
        "POLICY_LEDGER",
        "f" * 64,
        native_ref=ref,
        source_state="AVAILABLE",
        packet_ids=(packet.packet_id,),
    )
    sources = ReviewSources(
        UUID(int=205),
        UUID(int=206),
        UUID(int=207),
        1,
        None,
        (index,),
        (packet,),
        ReviewSourceManifest(1, 1, 0, "a" * 64, "b" * 64, regions, (), True),
        "0" * 64,
    )
    monkeypatch.setattr(reassessment, "_plan", lambda *args: plan)
    prepared, verified = reassessment._prepare(
        None, HouseholdScope(sources.household_space_id), sources, packet, graph
    )
    assert prepared.verified_root_ids == ("daily",) and prepared.unverified_root_ids == ()
    assert not verified.compilation.processing_complete
    assert amount(verified.compilation.roots[0]) == 300

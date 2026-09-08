"""Full semantic processing includes known definitions and excluded source context."""

from dataclasses import replace

import pytest
from familycare_api.terms_knowledge.projector import _assess, _proposal
from familycare_api.terms_knowledge.repository import SemanticSourcePlan
from familycare_api.terms_knowledge.source_layout import SemanticSourceLayout

from apps.api.tests.test_terms_local_candidates import _snapshot
from apps.api.tests.test_terms_source_verification import FIXED, FOOTNOTE


@pytest.mark.parametrize("records", [[("Article 1", [FIXED])], [("Footnote 1", [FOOTNOTE])]])
def test_complete_accounting_does_not_require_each_observation_executable(records):
    plan = SemanticSourcePlan({}, "a" * 64, _snapshot(records))
    manifest, graphs = _proposal(plan)
    assert manifest["unresolved_regions"] == []
    assert _assess(plan, manifest, graphs) == "COMPLETE"


def test_excluded_context_is_fully_processed_without_manufacturing_a_rule():
    snapshot = _snapshot([("Article 1", ["Synthetic title"])])
    region = replace(
        snapshot.layout.regions[0],
        kind="unresolved",
        reason_codes=("SEMANTIC_TERMS_TITLE_CONTEXT",),
    )
    snapshot = replace(snapshot, layout=SemanticSourceLayout((region,), (region.region_id,), True))
    plan = SemanticSourcePlan({}, "a" * 64, snapshot)
    manifest, graphs = _proposal(plan)
    assert graphs == ()
    assert _assess(plan, manifest, graphs) == "COMPLETE"

"""Partial worker output cannot displace a complete result for the same original statement."""

from dataclasses import replace

import pytest
from familycare_api.guidance.semantic_binding import bind_semantic_root
from familycare_api.guidance.semantic_repository import prefer_bound_root

from apps.api.tests.test_guidance_semantic_binding import source_and_root


@pytest.mark.parametrize("partial_first", [True, False])
def test_complete_original_root_wins_independently_of_candidate_root_id_order(partial_first):
    source, clause, current = source_and_root()
    complete = bind_semantic_root(current, clause, source.source.model_dump())
    partial = replace(complete, calculation=None, complete=False)
    ordered = (partial, complete) if partial_first else (complete, partial)
    assert prefer_bound_root(*ordered) == complete


def test_reader_scopes_each_page_and_cache_to_the_actual_original_clause(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import familycare_api.guidance.semantic_repository as module
    from familycare_api.common.scope import HouseholdScope

    _, clause, _ = source_and_root()
    calls = []

    def read(*args, **kwargs):
        calls.append(kwargs)
        return ()

    monkeypatch.setattr(module, "read_semantic_root_page", read)
    reader = module.SemanticGuidanceReader(
        None, HouseholdScope(uuid4()), SimpleNamespace(), "unused"
    )
    edition = uuid4()
    spans = (*clause.body, *clause.table_context)
    reader._roots(edition, spans)
    reader._roots(edition, spans)
    other = (replace(spans[0], node_id="other-original-node"),)
    reader._roots(edition, other)
    assert len(calls) == 2
    assert calls[0]["source_spans"] == spans
    assert calls[1]["source_spans"] == other


def test_reader_reports_clause_root_budget_instead_of_returning_partial_page(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import familycare_api.guidance.semantic_repository as module
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.terms_knowledge.core import SemanticKnowledgeError

    _, clause, _ = source_and_root()
    page = tuple(SimpleNamespace(root_node_id=f"synthetic-root-{n}") for n in range(32))
    monkeypatch.setattr(module, "read_semantic_root_page", lambda *args, **kwargs: page)
    reader = module.SemanticGuidanceReader(
        None, HouseholdScope(uuid4()), SimpleNamespace(), "unused"
    )
    with pytest.raises(SemanticKnowledgeError, match="SEMANTIC_CLAUSE_ROOT_LIMIT_EXCEEDED"):
        reader._roots(uuid4(), clause.body)

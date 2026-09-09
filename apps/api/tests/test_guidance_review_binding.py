"""Review provenance and root replacement reuse the ordinary semantic binder."""

from dataclasses import replace
from uuid import UUID

from familycare_api.guidance.repository import merge_review_roots
from familycare_api.guidance.semantic_binding import bind_semantic_root

from apps.api.tests.test_guidance_semantic_binding import source_and_root


def _review_root():
    snapshot, clause, current = source_and_root()
    job_id = UUID(int=900, version=4)
    bound = bind_semantic_root(current, clause, snapshot.source.model_dump(), review_job_id=job_id)
    assert bound is not None
    return bound, job_id


def test_binder_retains_actual_publication_and_review_job_in_all_rule_and_calculation_citations():
    bound, job_id = _review_root()
    assert bound.calculation is not None
    for item in (*bound.rules, bound.calculation):
        assert item.source_kind == "SEMANTIC_NODE"
        assert all(c.evidence.review_job_id == job_id for c in item.citations)
        assert all(c.evidence.publication_id == item.publication_id for c in item.citations)


def test_complete_review_replaces_only_its_original_root_and_preserves_old_objects():
    reviewed, _ = _review_root()
    original = replace(reviewed, calculation=None, complete=False)
    unrelated = replace(reviewed, original_anchor=("synthetic-other-original",))
    roots, codes = merge_review_roots((original, unrelated), (reviewed,))
    assert roots == (reviewed, unrelated)
    assert original.calculation is None and not original.complete
    assert "REVIEW_SEMANTIC_CORRECTION" in codes


def test_partial_review_never_replaces_a_complete_original_or_adds_an_executable_case():
    original, _ = _review_root()
    partial = replace(original, calculation=None, complete=False)
    roots, codes = merge_review_roots((original,), (partial,))
    assert roots == (original,)
    assert "REVIEW_SEMANTIC_PARTIAL" in codes


def test_verified_new_root_is_added_without_losing_original_case():
    original, _ = _review_root()
    additional = replace(original, original_anchor=("synthetic-additional-original",))
    roots, codes = merge_review_roots((original,), (additional,))
    assert roots == (original, additional)
    assert "REVIEW_SEMANTIC_ADDITION" in codes


def test_conflicting_review_meanings_for_one_original_do_not_select_by_input_order():
    first, _ = _review_root()
    conflicting = replace(first, benefit_kind="UNKNOWN")
    for reviewed in ((first, conflicting), (conflicting, first)):
        roots, codes = merge_review_roots((), reviewed)
        assert roots == ()
        assert "REVIEW_SEMANTIC_DISAGREEMENT" in codes

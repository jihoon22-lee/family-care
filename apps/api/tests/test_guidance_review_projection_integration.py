"""Review proposals improve missing local results only after source-bound reevaluation."""

from copy import deepcopy

import psycopg
import pytest
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_worker.guidance_review_budget import GuidanceReviewBudget
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue, _lease
from psycopg.rows import dict_row

from apps.api.tests.test_guidance_review_reassessment_integration import (
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_review_reassessment_integration import (
    unreviewed_original as unreviewed_original,
)
from apps.api.tests.test_terms_change_integration import _psycopg_url

pytestmark = pytest.mark.integration


def _stage(sample, *, graph=None, kind="ADDITIONAL_CANDIDATE", no_suggestions=False):
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        job = _lease(
            connection.execute(
                "SELECT * FROM guidance_review_jobs WHERE id=%s", (sample.job.id,)
            ).fetchone()
        )
    queue = GuidanceReviewQueue(sample.url)
    work = queue.load_inputs(job)
    budget = GuidanceReviewBudget(sample.url)
    reservation = budget.reserve(
        job,
        phase="discover",
        document_ids=tuple(sorted(set(work.document_versions.values()))),
        fingerprint="c" * 64,
        input_tokens=4000,
        output_tokens=4000,
    )
    budget.finish(reservation, succeeded=True, metadata=None)
    assert queue.record_proposal(
        job,
        {
            "schema_revision": "guidance-review-proposals-v1",
            "suggestions": []
            if no_suggestions
            else [
                {
                    "packet_id": sample.packet.packet_id,
                    "kind": kind,
                    "graph": graph or sample.graph,
                    "affected_fact_paths": ["MedicalEvent.admission_days"],
                    "proposed_amount": "999999",
                }
            ],
            "reviewed_packet_ids": [] if no_suggestions else [sample.packet.packet_id],
            "unreviewed_packet_ids": [
                p.packet_id
                for p in sample.sources.packets
                if no_suggestions or p.packet_id != sample.packet.packet_id
            ],
            "omitted_packet_ids": [],
            "omitted_coverage_aliases": [],
            "local_comparison_complete": True,
            "omitted_local_sections": [],
        },
    )


def _project(sample):
    from familycare_api.guidance_review.projector import GuidanceReviewProjector

    return GuidanceReviewProjector(sample.url).project_pending(limit=1)


def _read(sample):
    return GuidanceReviewRepository(sample.url).get_job(sample.scope, sample.job.id)


def test_review_finds_missing_candidate_and_recomputes_amount_ignoring_ai_number(
    unreviewed_original,
):
    sample = unreviewed_original
    _stage(sample)
    assert _project(sample) == 1
    reviewed = _read(sample)
    assert reviewed.state == "partial"  # Unrelated original regions were not all supplied.
    assert reviewed.result is not None and not reviewed.result.scope.complete
    assert reviewed.result.scope.expected_regions > reviewed.result.scope.supplied_regions
    assert reviewed.result.scope.unsupplied_regions > 0
    candidate = next(
        c for c in reviewed.result.guidance.candidates if c.ref == sample.packet.coverage_ref
    )
    assert candidate.estimate.amount == "300"
    assert reviewed.result.differences[0].change == "ADDED"
    assert "REVIEW_ADVISORY_AMOUNT_IGNORED" in reviewed.result.findings[0].reason_codes
    assert reviewed.result.findings[0].evidence
    assert reviewed.usage.input_tokens is None and reviewed.usage.requests_reserved == 1
    assert _project(sample) == 0
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM terms_semantic_publications"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (sample.original.run_id,)
        ).fetchone() == (sample.original.local_guidance.model_dump(mode="json"),)


@pytest.mark.parametrize("fault", ["citation", "opposite_meaning"])
def test_false_proposals_cannot_add_candidate_or_amount(unreviewed_original, fault):
    sample = unreviewed_original
    graph = deepcopy(sample.graph)
    if fault == "citation":
        graph["citations"][0]["page_number"] = 499
    else:
        node = next(
            n for n in graph["nodes"] if n["payload"].get("effect") == "initial_excluded_days"
        )
        node["payload"]["days"] = 0
    _stage(sample, graph=graph)
    assert _project(sample) == 1
    reviewed = _read(sample)
    assert reviewed.state == "disagreement"
    assert reviewed.result.findings[0].status == "REJECTED"
    assert not reviewed.result.guidance.candidates
    assert not reviewed.result.differences
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM guidance_review_publications"
        ).fetchone() == (0,)


def test_no_suggestion_control_preserves_known_missing_candidate(unreviewed_original):
    sample = unreviewed_original
    _stage(sample, no_suggestions=True)
    assert _project(sample) == 1
    reviewed = _read(sample)
    assert reviewed.state == "partial" and not reviewed.result.scope.complete
    assert not reviewed.result.guidance.candidates
    assert not reviewed.result.differences
    assert not reviewed.result.findings
    assert reviewed.result.scope.reviewed_packets == 0
    assert reviewed.result.scope.unreviewed_packets == len(sample.sources.packets)


def test_cancelled_review_is_not_projected_after_worker_finished(unreviewed_original):
    sample = unreviewed_original
    _stage(sample)
    GuidanceReviewRepository(sample.url).cancel(sample.scope, sample.job.id)
    assert _project(sample) == 0
    reviewed = _read(sample)
    assert reviewed.state == "cancelled" and reviewed.result is None
    assert reviewed.usage.requests_reserved == 1


def test_changed_input_fails_projection_and_preserves_original(unreviewed_original):
    sample = unreviewed_original
    _stage(sample)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE medical_events SET version=version+1 WHERE id=%s", (sample.event.id,)
        )
    assert _project(sample) == 1
    reviewed = _read(sample)
    assert reviewed.state == "failed" and reviewed.error_code == "REVIEW_INPUT_CHANGED"
    assert reviewed.stale and reviewed.result is None


def test_deleted_linked_clause_blocks_worker_before_reservation(unreviewed_original):
    from familycare_api.clauses.repository import ClauseRepository
    from familycare_worker.guidance_review_jobs import ReviewQueueUnavailable

    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        job = _lease(
            connection.execute(
                "SELECT * FROM guidance_review_jobs WHERE id=%s", (sample.job.id,)
            ).fetchone()
        )
        version = connection.execute(
            "SELECT version FROM clauses WHERE id=%s", (sample.packet.clause_id,)
        ).fetchone()["version"]
    ClauseRepository(sample.url).soft_delete(
        sample.scope, sample.packet.clause_id, expected_version=version
    )
    with pytest.raises(ReviewQueueUnavailable):
        GuidanceReviewQueue(sample.url).load_inputs(job)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_requests").fetchone() == (
            0,
        )


def test_fully_supplied_but_partly_interpreted_sources_remain_partial(
    unreviewed_original, monkeypatch
):
    from dataclasses import replace

    from familycare_api.guidance_review import projector

    sample = unreviewed_original
    # Isolate semantic completeness from the independently tested retrieval flag.
    # Original packet contents, source replay, graph compilation and DB publication stay real.
    read = projector.read_review_sources

    def all_supplied(*args, **kwargs):
        sources = read(*args, **kwargs)
        return replace(sources, manifest=replace(sources.manifest, complete=True))

    monkeypatch.setattr(projector, "read_review_sources", all_supplied)
    _stage(sample)
    assert _project(sample) == 1
    reviewed = _read(sample)
    assert reviewed.result.guidance.candidates[0].estimate.amount == "300"
    assert reviewed.state == "partial" and not reviewed.result.scope.complete


@pytest.mark.parametrize("unreviewed_original", [True], indirect=True)
def test_detached_selected_terms_block_loaded_request_and_budget(unreviewed_original):
    from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
    from familycare_worker.guidance_review_budget import ReviewBudgetRejected
    from familycare_worker.guidance_review_jobs import ReviewQueueUnavailable

    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        lease = _lease(
            connection.execute(
                "SELECT * FROM guidance_review_jobs WHERE id=%s", (sample.job.id,)
            ).fetchone()
        )
    queue = GuidanceReviewQueue(sample.url)
    loaded = queue.load_inputs(lease)
    assert loaded.sources["packets"]
    assert InsuranceDocumentRepository(sample.url).detach_set_item(
        sample.scope, item_id=sample.selected_item.id, expected_version=sample.selected_item.version
    )
    with pytest.raises(ReviewQueueUnavailable):
        queue.load_inputs(lease)
    with pytest.raises((ReviewQueueUnavailable, ReviewBudgetRejected)):
        GuidanceReviewBudget(sample.url).reserve(
            lease,
            phase="discover",
            document_ids=tuple(sorted(set(loaded.document_versions.values()))),
            fingerprint="e" * 64,
            input_tokens=4000,
            output_tokens=4000,
        )
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_requests").fetchone() == (
            0,
        )

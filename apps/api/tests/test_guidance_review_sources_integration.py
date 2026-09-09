"""Review inventory precedes candidates and retains complete scoped original windows."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.repository import RiderClauseLinkRepository
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance_review import sources as review_sources
from familycare_api.guidance_review.sources import ReviewSourcesInvalid, read_review_sources
from psycopg.rows import dict_row

from apps.api.tests.test_clause_change_integration import _native_link
from apps.api.tests.test_clause_source_publication import _clause
from apps.api.tests.test_decision_integration import _service
from apps.api.tests.test_private_knowledge_decision_integration import _seed_private_publication
from apps.api.tests.test_semantic_local_guidance_integration import _published_semantic_sources
from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)
from apps.api.tests.test_terms_source_verification import DAILY, FOOTNOTE

pytestmark = pytest.mark.integration


@pytest.fixture()
def native_review(changes_database, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    url, job = changes_database
    sources, links, scope = _published_semantic_sources(url, job)
    service = _service(url, scope)
    event = service.create_medical_event(
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic unrelated event without local candidates.",
        event_date=date(2025, 6, 15),
        facts={},
    )
    return SimpleNamespace(
        url=url,
        job=job,
        sources=sources,
        links=links,
        scope=scope,
        service=service,
        event=event,
    )


def _read(sample, *, event=None, scope=None):
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return read_review_sources(
            connection, scope or sample.scope, event or sample.event, sample.service.repository
        )


def test_native_inventory_does_not_need_a_decision_candidate_and_keeps_whole_footnote(
    native_review,
):
    sample = native_review
    result = _read(sample)
    index = next(c for c in result.index if c.ref.coverage_id == sample.sources["Sample Rider"])
    assert index.enrollment_decision == "MATCH"
    assert index.packet_ids
    packet = next(p for p in result.packets if p.packet_id in index.packet_ids)
    envelope = json.loads(packet.envelope_json)
    assert envelope["source"]["terms_edition_id"] == str(sample.sources["EDITION-A"])
    text = "\n".join(c["text"] for r in envelope["regions"] for c in r["citations"])
    assert DAILY in text and FOOTNOTE in text
    assert "Synthetic unrelated context." not in text
    assert any(r.omitted_region_ids for r in result.manifest.regions)
    assert result == _read(sample)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM decision_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone() == (0,)


@pytest.mark.parametrize("fault", ["household", "member", "version", "date"])
def test_forged_or_stale_scope_cannot_read_originals(native_review, fault):
    sample = native_review
    changed = {
        "household": {"household_space_id": uuid4()},
        "member": {"family_member_id": uuid4()},
        "version": {"version": sample.event.version + 1},
        "date": {"event_date": date(2024, 1, 1)},
    }[fault]
    with pytest.raises(ReviewSourcesInvalid):
        _read(sample, event=replace(sample.event, **changed))
    with pytest.raises(ReviewSourcesInvalid):
        _read(sample, scope=HouseholdScope(uuid4()))


def test_other_family_member_has_no_borrowed_coverage_or_original_text(native_review):
    sample = native_review
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        member = connection.execute(
            "INSERT INTO family_members(household_space_id,display_name,internal_alias) "
            "VALUES (%s,'Synthetic Other Member','synthetic-other-member') RETURNING id",
            (sample.scope.household_space_id,),
        ).fetchone()[0]
    event = sample.service.create_medical_event(
        family_member_id=member,
        mode="post_treatment",
        situation="Synthetic event.",
        event_date=sample.event.event_date,
        facts={},
    )
    result = _read(sample, event=event)
    assert not result.index and not result.packets
    assert result.manifest.total_coverage_count == 0


def test_unpublished_private_enrollment_stays_indexed_without_inventing_originals(
    native_review, tmp_path
):
    sample = native_review
    _seed_private_publication(
        sample.url,
        SimpleNamespace(scope_a=sample.scope, member_a=sample.job.family_member_id),
        tmp_path,
    )
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE private_knowledge_rule_import_runs SET state='SUPERSEDED',is_current=false,"
            "superseded_at=clock_timestamp()"
        )
    result = _read(sample)
    private = [c for c in result.index if c.ref.kind == "PRIVATE_KNOWLEDGE_COVERAGE"]
    assert private and all(c.enrollment_decision == "MATCH" for c in private)
    assert all(
        not c.packet_ids and "REVIEW_NATIVE_SOURCE_UNAVAILABLE" in c.reason_codes for c in private
    )
    assert all(p.coverage_ref.kind == "OPERATIONAL_RIDER" for p in result.packets)


@pytest.mark.parametrize("limit", ["packet", "bytes"])
def test_source_limits_keep_index_and_explicit_omission_instead_of_truncating(
    native_review, monkeypatch, limit
):
    monkeypatch.setattr(
        review_sources,
        "MAX_SOURCE_PACKETS" if limit == "packet" else "MAX_PACKET_BYTES",
        0 if limit == "packet" else 64,
    )
    result = _read(native_review)
    assert result.index and not result.packets
    expected = "REVIEW_PACKET_LIMIT" if limit == "packet" else "REVIEW_PACKET_BYTE_LIMIT"
    assert expected in result.manifest.reason_codes
    assert any(expected in c.reason_codes for c in result.index)


def test_index_limit_retains_total_omitted_count_and_revision_digest(
    native_review, monkeypatch, tmp_path
):
    sample = native_review
    _seed_private_publication(
        sample.url,
        SimpleNamespace(scope_a=sample.scope, member_a=sample.job.family_member_id),
        tmp_path,
    )
    full = _read(sample)
    monkeypatch.setattr(review_sources, "MAX_INDEX_COVERAGES", 1)
    bounded = _read(sample)
    assert len(bounded.index) == bounded.manifest.indexed_coverage_count == 1
    assert bounded.manifest.total_coverage_count == full.manifest.total_coverage_count
    assert bounded.manifest.omitted_coverage_count == full.manifest.total_coverage_count - 1
    assert bounded.manifest.inventory_digest_sha256 == full.manifest.inventory_digest_sha256
    assert "REVIEW_INDEX_LIMIT" in bounded.manifest.reason_codes


def test_source_revision_change_does_not_mutate_retained_packet(native_review):
    sample = native_review
    original = _read(sample)
    payload = original.to_payload()
    before = original.to_payload()
    payload["index"].clear()
    assert original.to_payload() == before
    with pytest.raises(FrozenInstanceError):
        original.digest_sha256 = "a" * 64
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE rider_clause_links SET deleted_at=clock_timestamp(),version=version+1 "
            "WHERE id=ANY(%s)",
            (sample.links,),
        )
    changed = _read(sample)
    assert not changed.packets and changed.digest_sha256 != original.digest_sha256
    assert original.to_payload() == before


def test_one_packet_budget_prefers_independent_event_relevance_over_first_link(
    native_review, monkeypatch
):
    sample = native_review
    clause = _clause(
        sample.url,
        sample.job,
        sample.sources["EDITION-A"],
        body="Synthetic unrelated context.",
        label="Article 2",
    )
    assert ClauseSourceProjector(sample.url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        link = _native_link(
            connection,
            sample.scope.household_space_id,
            sample.sources["Sample Rider"],
            clause,
            sample.sources["EDITION-A"],
        )
    RiderClauseLinkRepository(sample.url).confirm(sample.scope, link, expected_version=1)
    monkeypatch.setattr(review_sources, "MAX_SOURCE_PACKETS", 1)
    result = _read(sample)
    assert len(result.packets) == 1 and result.packets[0].clause_id == clause
    text = " ".join(
        c["text"]
        for r in json.loads(result.packets[0].envelope_json)["regions"]
        for c in r["citations"]
    )
    assert "Synthetic unrelated context." in text
    assert "REVIEW_PACKET_LIMIT" in result.manifest.reason_codes

"""Synthetic user-history and concurrency guards for metadata refinements."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.clauses.errors import ClauseVersionConflict
from familycare_api.clauses.repository import TermsEditionRepository
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.policies.errors import PolicyStateConflict
from familycare_worker.document_metadata import REVISION
from psycopg.rows import dict_row

from apps.api.tests import test_metadata_supersession_integration as refinement_fixtures
from apps.api.tests.test_document_metadata_publication import (
    publication_database as publication_database,
)
from apps.api.tests.test_document_metadata_publication import (
    seeded_policy_database as seeded_policy_database,
)
from apps.api.tests.test_document_metadata_publication import (
    structure_database as structure_database,
)
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


def _source_rows(url: str, previous: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        component = connection.execute(
            "SELECT * FROM insurance_document_components WHERE id=%s",
            (previous["component_id"],),
        ).fetchone()
        edition = connection.execute(
            "SELECT * FROM terms_editions WHERE id=%s", (previous["edition_id"],)
        ).fetchone()
        publication = connection.execute(
            "SELECT * FROM document_metadata_publications WHERE id=%s",
            (previous["metadata_publication_id"],),
        ).fetchone()
    assert component is not None and edition is not None and publication is not None
    return component, edition, publication


def _refinement_publications(url: str, job: Any) -> list[dict[str, Any]]:
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT publication.outcome,publication.component_id "
            "FROM document_metadata_publications publication "
            "JOIN document_metadata_proposals proposal ON proposal.id=publication.proposal_id "
            "JOIN document_structure_generations generation "
            "ON generation.id=proposal.generation_id "
            "WHERE generation.batch_item_id=%s AND proposal.revision=%s",
            (job.batch_item_id, REVISION),
        ).fetchall()


def _assert_deferred(
    url: str,
    job: Any,
    previous: dict[str, Any],
    expected_source: tuple[dict[str, Any], ...],
) -> None:
    assert _source_rows(url, previous) == expected_source
    assert expected_source[0]["superseded_by_component_id"] is None
    assert _refinement_publications(url, job) == [{"outcome": "DEFERRED", "component_id": None}]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute(
            "SELECT id FROM insurance_document_components WHERE document_batch_item_id=%s",
            (job.batch_item_id,),
        ).fetchall() == [{"id": previous["component_id"]}]
        assert connection.execute(
            "SELECT id FROM terms_editions WHERE household_space_id=%s AND document_version_id=%s",
            (job.household_space_id, job.document_version_id),
        ).fetchall() == [{"id": previous["edition_id"]}]
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM document_component_supersessions"
            ).fetchone()["n"]
            == 0
        )
    assert DocumentMetadataProjector(url).project_pending() == 0


def _assert_successor(
    url: str,
    job: Any,
    previous: dict[str, Any],
    original_source: tuple[dict[str, Any], ...],
) -> None:
    component, edition, publication = _source_rows(url, previous)
    successor_id = component["superseded_by_component_id"]
    assert successor_id is not None and successor_id != previous["component_id"]
    assert edition == original_source[1]
    assert publication == original_source[2]
    assert edition["source_metadata_json"] == previous["source_metadata_json"]
    bookkeeping = {"superseded_by_component_id", "version", "updated_at"}
    assert {key: value for key, value in component.items() if key not in bookkeeping} == {
        key: value for key, value in original_source[0].items() if key not in bookkeeping
    }
    assert _refinement_publications(url, job) == [
        {"outcome": "APPLIED", "component_id": successor_id}
    ]
    current = TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))
    assert len(current) == 1
    assert current[0].id != previous["edition_id"]
    assert current[0].source_component_id == successor_id
    assert current[0].product_display == "Sample Policy"
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM document_component_supersessions"
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM insurance_document_components "
                "WHERE document_batch_item_id=%s",
                (job.batch_item_id,),
            ).fetchone()["n"]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM terms_editions "
                "WHERE household_space_id=%s AND document_version_id=%s",
                (job.household_space_id, job.document_version_id),
            ).fetchone()["n"]
            == 2
        )


def _new_set(url: str, job: Any) -> tuple[UUID, Any]:
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        actor = connection.execute(
            "SELECT batch.created_by FROM document_batches batch "
            "JOIN document_batch_items item ON item.batch_id=batch.id WHERE item.id=%s",
            (job.batch_item_id,),
        ).fetchone()["created_by"]
    document_set = InsuranceDocumentRepository(url).create_document_set(
        HouseholdScope(job.household_space_id),
        actor_id=actor,
        member_id=job.family_member_id,
        policy_contract_id=None,
        insurer_display=None,
        product_display=None,
        display_label="Sample supersession review",
    )
    assert document_set is not None
    return actor, document_set


def _set_rows(url: str, set_id: UUID) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        document_set = connection.execute(
            "SELECT * FROM insurance_document_sets WHERE id=%s", (set_id,)
        ).fetchone()
        items = connection.execute(
            "SELECT * FROM insurance_document_set_items "
            "WHERE insurance_document_set_id=%s ORDER BY id",
            (set_id,),
        ).fetchall()
    assert document_set is not None
    return document_set, items


def _race(left: Callable[[], Any], right: Callable[[], Any]) -> tuple[Any, Any]:
    start = Barrier(2)

    def run(action: Callable[[], Any]) -> Any:
        start.wait(timeout=10)
        return action()

    with ThreadPoolExecutor(max_workers=2) as executor:
        left_result = executor.submit(run, left)
        right_result = executor.submit(run, right)
        return left_result.result(timeout=60), right_result.result(timeout=60)


@pytest.mark.parametrize("change", ["USER_CONFIRMED", "REJECTED", "deleted", "version"])
def test_component_decisions_and_changed_versions_block_refinement(
    publication_database: Any, change: str
) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    with psycopg.connect(_psycopg_url(url)) as connection:
        if change in {"USER_CONFIRMED", "REJECTED"}:
            connection.execute(
                "UPDATE insurance_document_components SET review_state=%s,version=version+1 "
                "WHERE id=%s",
                (change, previous["component_id"]),
            )
        elif change == "deleted":
            connection.execute(
                "UPDATE insurance_document_components SET deleted_at=clock_timestamp(),"
                "version=version+1 WHERE id=%s",
                (previous["component_id"],),
            )
        else:
            connection.execute(
                "UPDATE insurance_document_components SET version=version+1 WHERE id=%s",
                (previous["component_id"],),
            )
    expected = _source_rows(url, previous)
    assert DocumentMetadataProjector(url).project_pending() == 1
    _assert_deferred(url, job, previous, expected)


@pytest.mark.parametrize("change", ["deleted", "version"])
def test_edition_decisions_and_changed_versions_block_refinement(
    publication_database: Any, change: str
) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    if change == "deleted":
        TermsEditionRepository(url).soft_delete(
            HouseholdScope(job.household_space_id), previous["edition_id"], expected_version=1
        )
    else:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE terms_editions SET version=version+1 WHERE id=%s",
                (previous["edition_id"],),
            )
    expected = _source_rows(url, previous)
    assert DocumentMetadataProjector(url).project_pending() == 1
    _assert_deferred(url, job, previous, expected)


@pytest.mark.parametrize("history", ["USER_CONFIRMED", "REJECTED", "detached", "deleted_set"])
def test_all_set_item_history_blocks_refinement(publication_database: Any, history: str) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    actor, document_set = _new_set(url, job)
    repository = InsuranceDocumentRepository(url)
    scope = HouseholdScope(job.household_space_id)
    item = repository.attach_set_item(
        scope,
        actor_id=actor,
        document_set_id=document_set.id,
        insurance_document_component_id=previous["component_id"],
        match_state="REJECTED" if history == "REJECTED" else "USER_CONFIRMED",
        evidence_id=None,
        expected_set_version=document_set.version,
    )
    assert item is not None
    if history == "detached":
        assert repository.detach_set_item(scope, item_id=item.id, expected_version=item.version)
    elif history == "deleted_set":
        assert repository.soft_delete_document_set(
            scope, document_set_id=document_set.id, expected_version=document_set.version + 1
        )
    expected_source = _source_rows(url, previous)
    expected_set = _set_rows(url, document_set.id)
    assert DocumentMetadataProjector(url).project_pending() == 1
    _assert_deferred(url, job, previous, expected_source)
    assert _set_rows(url, document_set.id) == expected_set


def test_retired_edition_delete_and_component_attach_reject_stale_targets(
    publication_database: Any,
) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    original = _source_rows(url, previous)
    assert DocumentMetadataProjector(url).project_pending() == 1
    _assert_successor(url, job, previous, original)
    actor, document_set = _new_set(url, job)
    expected_set = _set_rows(url, document_set.id)
    scope = HouseholdScope(job.household_space_id)
    with pytest.raises(ClauseVersionConflict):
        TermsEditionRepository(url).soft_delete(scope, previous["edition_id"], expected_version=1)
    with pytest.raises(PolicyStateConflict):
        InsuranceDocumentRepository(url).attach_set_item(
            scope,
            actor_id=actor,
            document_set_id=document_set.id,
            insurance_document_component_id=previous["component_id"],
            match_state="USER_CONFIRMED",
            evidence_id=None,
            expected_set_version=document_set.version,
        )
    _assert_successor(url, job, previous, original)
    assert _set_rows(url, document_set.id) == expected_set


def test_parallel_refinement_publishes_one_successor_and_receipt(publication_database: Any) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    original = _source_rows(url, previous)
    results = _race(
        lambda: DocumentMetadataProjector(url).project_pending(),
        lambda: DocumentMetadataProjector(url).project_pending(),
    )
    assert sum(results) == 1
    assert DocumentMetadataProjector(url).project_pending() == 0
    _assert_successor(url, job, previous, original)


def test_concurrent_edition_delete_is_preserved_or_rejected_as_stale(
    publication_database: Any,
) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    original = _source_rows(url, previous)
    scope = HouseholdScope(job.household_space_id)

    def delete() -> bool:
        try:
            deleted = TermsEditionRepository(url).soft_delete(
                scope, previous["edition_id"], expected_version=1
            )
        except ClauseVersionConflict:
            return False
        assert deleted.deleted_at is not None
        return True

    projected, deleted = _race(lambda: DocumentMetadataProjector(url).project_pending(), delete)
    assert projected in {0, 1}
    # A skipped locked source remains pending until the user transaction commits.
    assert DocumentMetadataProjector(url).project_pending() in {0, 1}
    if deleted:
        retained = _source_rows(url, previous)
        assert retained[0] == original[0]
        assert retained[2] == original[2]
        assert retained[1]["deleted_at"] is not None and retained[1]["version"] == 2
        assert retained[1]["source_metadata_json"] == previous["source_metadata_json"]
        _assert_deferred(url, job, previous, retained)
        assert TermsEditionRepository(url).list(scope) == ()
    else:
        _assert_successor(url, job, previous, original)


def test_concurrent_set_attach_is_preserved_or_rejected_as_stale(publication_database: Any) -> None:
    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    original = _source_rows(url, previous)
    actor, document_set = _new_set(url, job)
    original_set = _set_rows(url, document_set.id)

    def attach() -> bool:
        try:
            item = InsuranceDocumentRepository(url).attach_set_item(
                HouseholdScope(job.household_space_id),
                actor_id=actor,
                document_set_id=document_set.id,
                insurance_document_component_id=previous["component_id"],
                match_state="USER_CONFIRMED",
                evidence_id=None,
                expected_set_version=document_set.version,
            )
        except PolicyStateConflict:
            return False
        assert item is not None
        return True

    projected, attached = _race(lambda: DocumentMetadataProjector(url).project_pending(), attach)
    assert projected in {0, 1}
    assert DocumentMetadataProjector(url).project_pending() in {0, 1}
    if attached:
        _assert_deferred(url, job, previous, original)
        retained_set, items = _set_rows(url, document_set.id)
        assert retained_set["version"] == document_set.version + 1
        assert len(items) == 1
        assert items[0]["insurance_document_component_id"] == previous["component_id"]
        assert items[0]["match_state"] == "USER_CONFIRMED"
        assert items[0]["deleted_at"] is None
        assert items[0]["confirmed_by"] == actor and items[0]["confirmed_at"] is not None
    else:
        _assert_successor(url, job, previous, original)
        assert _set_rows(url, document_set.id) == original_set


def test_attach_waits_for_content_before_locking_component(publication_database: Any) -> None:
    import time

    from familycare_api.common.document_locks import lock_document_content

    url, job, previous = refinement_fixtures._pending_refinement(publication_database)
    actor, document_set = _new_set(url, job)
    repository = InsuranceDocumentRepository(
        url + "?application_name=synthetic-supersession-attach"
    )

    def attach() -> Any:
        return repository.attach_set_item(
            HouseholdScope(job.household_space_id),
            actor_id=actor,
            document_set_id=document_set.id,
            insurance_document_component_id=previous["component_id"],
            match_state="USER_CONFIRMED",
            evidence_id=None,
            expected_set_version=document_set.version,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as publisher:
            lock_document_content(publisher, job.household_space_id, "a" * 64)
            publisher.execute(
                "SELECT id FROM document_batch_items WHERE id=%s FOR UPDATE", (job.batch_item_id,)
            )
            future = executor.submit(attach)
            with psycopg.connect(_psycopg_url(url), autocommit=True) as observer:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    waiting = observer.execute(
                        "SELECT 1 FROM pg_stat_activity "
                        "WHERE application_name='synthetic-supersession-attach' "
                        "AND wait_event_type='Lock'"
                    ).fetchone()
                    if waiting:
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError("synthetic attachment did not reach its lock boundary")
            # A publisher must be able to finish while the attachment waits. Taking
            # a component SHARE lock first creates the item/component deadlock cycle.
            publisher.execute(
                "SELECT id FROM insurance_document_components WHERE id=%s FOR UPDATE NOWAIT",
                (previous["component_id"],),
            )
        assert future.result(timeout=10) is not None

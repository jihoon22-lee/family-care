"""Synthetic source-scoped component publication and retained manual decisions."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.insurance_documents.schemas import MemberInsuranceDocumentInventoryResponse
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workers.analyzer.tests.test_document_metadata_repository import _seed
from workers.analyzer.tests.test_document_structure_repository import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def publication_database(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("structure_database")


def _prepared(database: Any) -> tuple[str, Any, Any]:
    url, job, generation = _seed(database)
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    return url, job, generation


def test_program_components_publish_without_a_user_and_remain_unpaired(
    publication_database: Any,
) -> None:
    url, job, generation = _prepared(publication_database)
    projector = DocumentMetadataProjector(url)
    assert projector.project_pending(limit=1) == 1
    assert projector.project_pending(limit=1) == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM insurance_document_components WHERE document_batch_item_id=%s",
            (job.batch_item_id,),
        ).fetchone()
        assert row["household_space_id"] == job.household_space_id
        assert row["family_member_id"] == job.family_member_id
        assert row["document_version_id"] == job.document_version_id
        assert row["role"] == "terms" and row["page_start"] == row["page_end"] == 1
        assert row["review_state"] == "PROGRAM_VERIFIED" and row["created_by"] is None
        proof = connection.execute("SELECT * FROM document_metadata_publications").fetchone()
        assert proof["component_id"] == row["id"] and proof["outcome"] == "APPLIED"
        assert row["metadata_publication_id"] == proof["id"]
        assert not connection.execute(
            "SELECT id FROM insurance_document_set_items WHERE insurance_document_component_id=%s",
            (row["id"],),
        ).fetchone()
    inventory = InsuranceDocumentRepository(url).get_inventory(
        HouseholdScope(job.household_space_id), job.family_member_id
    )
    assert inventory is not None
    response = MemberInsuranceDocumentInventoryResponse.from_domain(inventory)
    assert any(
        component.review_state == "PROGRAM_VERIFIED" for component in response.unpaired_components
    )
    assert (
        InsuranceDocumentRepository(url).get_inventory(
            HouseholdScope(uuid4()), job.family_member_id
        )
        is None
    )


def test_parallel_component_publication_is_idempotent(publication_database: Any) -> None:
    url, _, _ = _prepared(publication_database)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: DocumentMetadataProjector(url).project_pending(limit=1), range(2)
            )
        )
    assert sum(results) == 1


def test_forged_metadata_is_recorded_as_invalid_without_a_component(
    publication_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker import document_metadata_repository as module

    original = module.metadata_proposal

    def forged(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        result["components"][0]["facts"][0]["value"] = "Synthetic Fabricated Insurer"
        return result

    monkeypatch.setattr(module, "metadata_proposal", forged)
    url, job, _ = _prepared(publication_database)
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT outcome FROM document_metadata_publications").fetchone()[
                "outcome"
            ]
            == "INVALID"
        )
        assert not connection.execute(
            "SELECT id FROM insurance_document_components WHERE document_batch_item_id=%s",
            (job.batch_item_id,),
        ).fetchone()
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 0


def test_program_component_origin_and_publication_history_are_immutable(
    publication_database: Any,
) -> None:
    url, job, _ = _prepared(publication_database)
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        for command in (
            "UPDATE document_metadata_publications SET outcome='INVALID',component_id=NULL",
            "DELETE FROM document_metadata_publications",
            "UPDATE insurance_document_components SET metadata_publication_id=NULL "
            "WHERE metadata_publication_id IS NOT NULL",
            "UPDATE insurance_document_components SET page_end=2 "
            "WHERE metadata_publication_id IS NOT NULL",
            "UPDATE insurance_document_components "
            "SET household_space_id=gen_random_uuid() WHERE metadata_publication_id IS NOT NULL",
        ):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(command)
        connection.execute(
            "UPDATE insurance_document_components SET review_state='USER_CONFIRMED',"
            "version=version+1 WHERE document_batch_item_id=%s",
            (job.batch_item_id,),
        )
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 0


def test_publication_cannot_claim_an_unrelated_manual_component(publication_database: Any) -> None:
    url, job, _ = _prepared(publication_database)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        user_id = connection.execute("SELECT id FROM app_users LIMIT 1").fetchone()["id"]
        component_id = uuid4()
        connection.execute(
            "INSERT INTO insurance_document_components(id,household_space_id,family_member_id,"
            "document_batch_item_id,document_version_id,role,page_start,page_end,"
            "review_state,created_by) "
            "VALUES(%s,%s,%s,%s,%s,'terms',1,1,'USER_CONFIRMED',%s)",
            (
                component_id,
                job.household_space_id,
                job.family_member_id,
                job.batch_item_id,
                job.document_version_id,
                user_id,
            ),
        )
        proposal = connection.execute(
            "SELECT id,proposal_json FROM document_metadata_proposals"
        ).fetchone()
        component = proposal["proposal_json"]["components"][0]
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO document_metadata_publications(proposal_id,component_identity,"
                "validator_revision,outcome,component_id,proof_json) "
                "VALUES(%s,%s,'document-metadata-api-v1','APPLIED',%s,%s)",
                (proposal["id"], component["identity"], component_id, Jsonb(component)),
            )
            connection.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.parametrize(
    "state,deleted", [("USER_CONFIRMED", False), ("REJECTED", False), ("USER_CONFIRMED", True)]
)
def test_manual_component_decisions_prevent_automatic_recreation(
    publication_database: Any, state: str, deleted: bool
) -> None:
    url, job, _ = _prepared(publication_database)
    identifier = uuid4()
    with psycopg.connect(_psycopg_url(url)) as connection:
        user_id = connection.execute("SELECT id FROM app_users LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO insurance_document_components(id,household_space_id,family_member_id,"
            "document_batch_item_id,document_version_id,role,page_start,page_end,review_state,"
            "created_by,deleted_at) VALUES(%s,%s,%s,%s,%s,'terms',1,1,%s,%s,"
            "CASE WHEN %s THEN clock_timestamp() ELSE NULL END)",
            (
                identifier,
                job.household_space_id,
                job.family_member_id,
                job.batch_item_id,
                job.document_version_id,
                state,
                user_id,
                deleted,
            ),
        )
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            "SELECT id,review_state FROM insurance_document_components "
            "WHERE document_batch_item_id=%s",
            (job.batch_item_id,),
        ).fetchall()
        assert rows == [{"id": identifier, "review_state": state}]
        assert (
            connection.execute("SELECT outcome FROM document_metadata_publications").fetchone()[
                "outcome"
            ]
            == "DEFERRED"
        )


@pytest.mark.parametrize("change", ["stale", "deleted_member", "deleted_document", "failed_item"])
def test_ineligible_source_is_not_published(publication_database: Any, change: str) -> None:
    url, job, generation = _prepared(publication_database)
    with psycopg.connect(_psycopg_url(url)) as connection:
        if change == "stale":
            connection.execute(
                "UPDATE document_structure_generations SET is_current=false WHERE id=%s",
                (generation,),
            )
        elif change == "deleted_member":
            connection.execute(
                "UPDATE family_members SET deleted_at=clock_timestamp() WHERE id=%s",
                (job.family_member_id,),
            )
        elif change == "deleted_document":
            connection.execute(
                "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=("
                "SELECT document_id FROM document_versions WHERE id=%s)",
                (job.document_version_id,),
            )
        else:
            connection.execute(
                "UPDATE document_batch_items SET state='cancelled' WHERE id=%s",
                (job.batch_item_id,),
            )
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 0

"""Source calculation upgrades preserve existing compiler publications."""

import psycopg
import pytest
from familycare_api.runtime_schema import SUPPORTED_SCHEMA_REVISION
from familycare_api.terms_knowledge import core
from familycare_api.terms_knowledge import repository as semantic_repository
from psycopg.rows import dict_row

from apps.api.tests.test_metadata_navigation_publication import _migrate
from apps.api.tests.test_terms_knowledge_repository import candidate_from_plan
from apps.api.tests.test_terms_knowledge_repository import semantic_database as semantic_database
from workers.analyzer.tests.test_document_structure_repository import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def historical_metadata_v9(monkeypatch):
    """This older compiler migration must not introduce future metadata history."""
    from familycare_worker import document_metadata, document_metadata_repository

    monkeypatch.setattr(document_metadata, "REVISION", "document-metadata-v9")
    monkeypatch.setattr(document_metadata_repository, "REVISION", "document-metadata-v9")


def test_calculation_upgrade_retains_v2_and_blocks_downgrade_with_v3_history(
    semantic_database, monkeypatch
):
    url, scope, edition = semantic_database
    repository = semantic_repository.TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    with monkeypatch.context() as legacy:
        legacy.setattr(core, "COMPILER_REVISION", "terms-semantic-compiler-v2")
        legacy.setattr(semantic_repository, "COMPILER_REVISION", "terms-semantic-compiler-v2")
        old = repository.publish_candidate(
            scope, edition, graph, expected_input_digest=plan.input_digest
        )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        before = connection.execute(
            "SELECT to_jsonb(p) AS retained FROM terms_semantic_publications p WHERE id=%s",
            (old.publication_id,),
        ).fetchone()["retained"]
    assert _migrate(url, "downgrade", "0070_semantic_activity").returncode == 0
    assert _migrate(url, "upgrade", "head").returncode == 0
    current = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    assert current.publication_id != old.publication_id
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS retained FROM terms_semantic_publications p WHERE id=%s",
                (old.publication_id,),
            ).fetchone()["retained"]
            == before
        )
        assert (
            connection.execute(
                "SELECT compiler_revision FROM terms_semantic_publications WHERE id=%s",
                (current.publication_id,),
            ).fetchone()["compiler_revision"]
            == "terms-semantic-compiler-v3"
        )
    refused = _migrate(url, "downgrade", "0070_semantic_activity")
    assert refused.returncode != 0
    assert "psycopg.errors.CheckViolation" in refused.stderr
    assert "source calculation history prevents downgrade" in refused.stderr
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            SUPPORTED_SCHEMA_REVISION,
        )

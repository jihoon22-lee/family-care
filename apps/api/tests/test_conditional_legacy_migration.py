"""Normally imported conditional publications must survive downgrade refusal."""

import os

import psycopg
import pytest
from familycare_api.private_knowledge.publication_package import load_rule_publication_package
from familycare_api.private_knowledge.publication_repository import (
    PostgresRulePublicationRepository,
)
from psycopg.rows import dict_row

from apps.api.tests.private_knowledge_publication_fixtures import (
    bind_publication_package_to_knowledge,
    mutate_publication_jsonl,
    write_synthetic_rule_publication_package,
)
from apps.api.tests.test_metadata_navigation_publication import _migrate
from apps.api.tests.test_private_knowledge_publication_repository_integration import (
    ACTOR_ID,
    HOUSEHOLD_ID,
    _seed_current_knowledge,
)
from apps.api.tests.test_terms_change_integration import _psycopg_url

pytestmark = pytest.mark.integration


def test_imported_if_calculation_blocks_downgrade_without_semantic_or_event_history(tmp_path):
    url = os.environ["FAMILYCARE_TEST_DATABASE_URL"]
    package_digest, projection_digest = _seed_current_knowledge(tmp_path)
    root = write_synthetic_rule_publication_package(tmp_path / "conditional-publication")

    def conditional(row):
        document = row["calculation_document"]
        document["input_field_paths"] = [
            "MedicalEvent.reduction_applies",
            "Rider.insured_amount",
        ]
        document["calculation"] = {
            "op": "if",
            "args": [
                {"field": "MedicalEvent.reduction_applies"},
                {
                    "op": "multiply",
                    "args": [{"field": "Rider.insured_amount"}, {"value": 0.5}],
                },
                {"field": "Rider.insured_amount"},
            ],
        }

    mutate_publication_jsonl(root, "calculation-publications.jsonl", conditional)
    bind_publication_package_to_knowledge(
        root,
        package_digest_sha256=package_digest,
        projection_digest_sha256=projection_digest,
    )
    package = load_rule_publication_package(root, repository_root=tmp_path / "repository")
    repository = PostgresRulePublicationRepository(_psycopg_url(url))
    report = repository.prepare_dry_run(package, household_space_id=HOUSEHOLD_ID)
    assert report.operation == "CREATE"
    applied = repository.apply(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=report,
    )

    def retained():
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            publications = connection.execute(
                "SELECT to_jsonb(p) AS value FROM private_knowledge_calculation_publications p "
                "WHERE household_space_id=%s AND rule_import_run_id=%s ORDER BY id",
                (HOUSEHOLD_ID, applied.run_id),
            ).fetchall()
            publication_run = connection.execute(
                "SELECT to_jsonb(r) AS value FROM private_knowledge_rule_import_runs r WHERE id=%s",
                (applied.run_id,),
            ).fetchone()
        return publications, publication_run

    before = retained()
    assert len(before[0]) == 1
    assert before[0][0]["value"]["calculation_json"]["calculation"]["op"] == "if"
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM terms_semantic_publications"
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM medical_events").fetchone() == (0,)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0071_source_calculations",
        )
    try:
        refused = _migrate(url, "downgrade", "0070_semantic_activity")
        assert refused.returncode != 0
        assert "psycopg.errors.CheckViolation" in refused.stderr
        assert "conditional calculation history prevents downgrade" in refused.stderr
        with psycopg.connect(_psycopg_url(url)) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0071_source_calculations",
            )
        assert retained() == before
    finally:
        # RED may expose a successful unsafe downgrade; restore the dedicated
        # test schema without changing the retained publication or import run.
        assert _migrate(url, "upgrade", "head").returncode == 0
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "TRUNCATE TABLE household_spaces, documents RESTART IDENTITY CASCADE"
            )

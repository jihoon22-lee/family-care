"""Navigation reprocessing retains prior metadata and registers only original body pages."""

import os
import subprocess
import sys
from typing import Any

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_document_metadata_publication import (
    publication_database as publication_database,
)
from apps.api.tests.test_document_metadata_publication import (
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_document_structure_repository import _prepare, _psycopg_url

pytestmark = pytest.mark.integration


def _migrate(url: str, operation: str, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", operation, revision],
        env={**os.environ, "FAMILYCARE_DATABASE_URL": url, "TMPDIR": "/tmp"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("mode", ["contents", "instruction", "reading_guide"])
def test_navigation_reprocessing_preserves_prior_metadata_and_refuses_loss_of_current_history(
    publication_database: Any,
    mode: str,
) -> None:
    url, job = publication_database
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s", (job.document_version_id,)
        )
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "VALUES(%s,2,600,800,200,1,0,1,'TEXT_SUFFICIENT')",
            (job.extraction_id,),
        )
    first_page = {
        "instruction": "상품설명서 및 보험증권을 참고하여 합성 보장을 확인하십시오.",
        "contents": "목차\n상품설명서 .... 9\n보험약관 .... 2",
        "reading_guide": "약관 이용 가이드\n예시 약관 이해를 돕는 참고 설명이 있습니다.",
    }[mode]
    source = build_document_structure(
        {
            "document_version_id": str(job.document_version_id),
            "content_sha256": "a" * 64,
            "pages": [
                {
                    "page_number": number,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [{"text": text, "reading_order": 0, "bbox": [10, 10, 500, 180]}],
                    "tables": [],
                }
                for number, text in enumerate(
                    (
                        first_page,
                        "보험사: Sample Assurance\n상품명: Sample Policy\n"
                        "제1조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다.",
                    ),
                    1,
                )
            ],
        },
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-navigation-v1",
    )
    generation = _prepare(
        DocumentStructureRepository(url),
        job,
        source,
        plan_structure_chunks(
            source,
            max_content_chars=4096,
            max_context_chars=4096,
            max_chunks=10,
        ),
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
    legacy = metadata_proposal(source, generation, identity)
    prior_revision, prior_schema = {
        "contents": ("document-metadata-v4", "0048_clause_change_pairs"),
        "instruction": ("document-metadata-v5", "0049_metadata_navigation_pages"),
        "reading_guide": ("document-metadata-v6", "0050_metadata_instructions"),
    }[mode]
    legacy.update(revision=prior_revision, components=[], unresolved_pages=[1, 2])
    try:
        down = _migrate(url, "downgrade", prior_schema)
        assert down.returncode == 0, down.stderr
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            previous = connection.execute(
                "INSERT INTO document_metadata_proposals("
                "generation_id,revision,state,attempts,proposal_json) "
                "VALUES(%s,%s,'PREPARED',1,%s) "
                "RETURNING id,to_jsonb(document_metadata_proposals)::text AS snapshot",
                (generation, prior_revision, Jsonb(legacy)),
            ).fetchone()
        up = _migrate(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            starting_revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()["version_num"]
        assert DocumentMetadataRunner(url).run_once("synthetic-navigation-worker")
        assert DocumentMetadataProjector(url).project_pending() == 1
        assert ComponentTermsProjector(url).project_pending() == 1
        assert not DocumentMetadataRunner(url).run_once("synthetic-navigation-worker")
        assert DocumentMetadataProjector(url).project_pending() == 0
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            assert connection.execute(
                "SELECT source_page_start,source_page_end FROM terms_editions"
            ).fetchall() == [{"source_page_start": 2, "source_page_end": 2}]
            assert connection.execute(
                "SELECT validator_revision,outcome FROM document_metadata_publications"
            ).fetchall() == [
                {"validator_revision": "document-metadata-api-v8", "outcome": "APPLIED"}
            ]
            current = connection.execute(
                "SELECT id,to_jsonb(p)::text AS snapshot FROM document_metadata_proposals p "
                "WHERE revision='document-metadata-v8'"
            ).fetchone()
        refused = _migrate(url, "downgrade", prior_schema)
        assert (
            refused.returncode != 0
            and "metadata revision history prevents downgrade" in refused.stderr
        )
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            for original in (previous, current):
                assert (
                    connection.execute(
                        "SELECT to_jsonb(p)::text AS snapshot FROM document_metadata_proposals p "
                        "WHERE id=%s",
                        (original["id"],),
                    ).fetchone()["snapshot"]
                    == original["snapshot"]
                )
            assert (
                connection.execute("SELECT version_num FROM alembic_version").fetchone()[
                    "version_num"
                ]
                == starting_revision
            )
    finally:
        restored = _migrate(url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr

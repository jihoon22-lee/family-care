from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from familycare_api.documents.batch_repository import BatchRepository as ApiBatchRepository
from familycare_api.documents.import_sources import ImportSourceCatalog
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore
from familycare_worker.imports.batch import BatchRunner
from familycare_worker.imports.password_scope import PasswordScope
from familycare_worker.repository import BatchRepository

from workers.analyzer.tests.synthetic_pdf_factory import make_text_pdf

pytestmark = pytest.mark.integration


def _url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def test_batch_runner_persists_extraction_and_archive_atomically(tmp_path: Path) -> None:
    database_url = _url()
    household_id = uuid4()
    user_id = uuid4()
    member_id = uuid4()
    suffix = household_id.hex[:12]
    import_root = tmp_path / "import"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    import_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    make_text_pdf(import_root / "sample-policy.pdf")
    catalog = ImportSourceCatalog(import_root)
    source = catalog.resolve(catalog.list()[0].source_id)

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, %s, 'Synthetic Worker Household')
            """,
            (household_id, f"synthetic-worker-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO app_users (
                id, household_space_id, username, display_name, password_hash
            )
            VALUES (%s, %s, %s, 'Synthetic Worker Admin', '$argon2id$synthetic-hash')
            """,
            (user_id, household_id, f"synthetic-worker-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Family Member A', %s)
            """,
            (member_id, household_id, f"synthetic-worker-member-{suffix}"),
        )

    try:
        created = ApiBatchRepository(database_url).create(
            household_space_id=household_id,
            created_by=user_id,
            family_member_id=member_id,
            sources=(source,),
        )
        assert created is not None
        runner = BatchRunner(
            repository=BatchRepository(database_url),
            document_root=import_root,
            work_root=work_root,
            archive_store=ArchiveStore(archive_root),
            master_key=MasterKey.synthetic(b"s" * 32, key_version="synthetic-v1"),
            password_scope=PasswordScope(
                batch_id=created.batch_id,
                password="synthetic-unused-password",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
        )

        assert runner.run_once("synthetic-worker") is True

        status = ApiBatchRepository(database_url).get(
            household_space_id=household_id,
            batch_id=created.batch_id,
        )
        assert status is not None
        assert status.state == "succeeded", status
        assert status.items[0].state == "succeeded"
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            persisted = connection.execute(
                """
                SELECT document.status, count(archive.id) AS archives,
                       count(extraction.id) AS extractions
                FROM document_batch_items AS item
                JOIN documents AS document ON document.id = item.document_id
                JOIN document_versions AS version ON version.document_id = document.id
                JOIN managed_archives AS archive ON archive.document_version_id = version.id
                JOIN extractions AS extraction ON extraction.document_version_id = version.id
                WHERE item.batch_id = %s
                GROUP BY document.status
                """,
                (created.batch_id,),
            ).fetchone()
        assert persisted == ("ready", 1, 1)
        assert len(list(archive_root.iterdir())) == 1
        assert list(work_root.iterdir()) == []
    finally:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            document_rows = connection.execute(
                "SELECT document_id FROM document_batch_items WHERE batch_id IN "
                "(SELECT id FROM document_batches WHERE household_space_id = %s)",
                (household_id,),
            ).fetchall()
            document_ids = [row[0] for row in document_rows if row[0] is not None]
            connection.execute(
                "DELETE FROM document_batches WHERE household_space_id = %s",
                (household_id,),
            )
            if document_ids:
                connection.execute(
                    "DELETE FROM managed_archives WHERE document_version_id IN "
                    "(SELECT id FROM document_versions WHERE document_id = ANY(%s))",
                    (document_ids,),
                )
                connection.execute("DELETE FROM documents WHERE id = ANY(%s)", (document_ids,))
            connection.execute("DELETE FROM app_users WHERE id = %s", (user_id,))
            connection.execute("DELETE FROM family_members WHERE id = %s", (member_id,))
            connection.execute("DELETE FROM household_spaces WHERE id = %s", (household_id,))

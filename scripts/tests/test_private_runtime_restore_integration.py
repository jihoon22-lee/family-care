"""Restore the real schema, immutable reviewed claim and encrypted synthetic archive."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from apps.api.tests.test_guidance_review_claim_integration import (
    _create,
    _project,
    _stage,
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_review_claim_integration import (
    unreviewed_original as unreviewed_original,
)
from scripts.private_runtime_backup import capture_backup_set, materialize_restore_inputs
from scripts.private_runtime_postgres import PostgresTools
from scripts.private_runtime_state import capture_database_state, require_preserved_state
from scripts.tests.test_private_runtime_backup import SYNTHETIC_PLAINTEXT, _synthetic_sources

pytestmark = pytest.mark.integration


def test_pgcustom_roundtrip_preserves_review_claim_and_decryptable_archive(
    unreviewed_original, tmp_path: Path
) -> None:
    sample = unreviewed_original
    _stage(sample)
    assert _project(sample) == 1
    claim = _create(sample)
    source_url = sample.url.replace("postgresql+psycopg://", "postgresql://", 1)
    container = os.environ["FAMILYCARE_TEST_POSTGRES_CONTAINER"]
    target_name = "familycare_restore_test_" + uuid4().hex
    options = conninfo_to_dict(source_url)
    target_url = make_conninfo(**(options | {"dbname": target_name}))
    _, archive_root, key_file, metadata = _synthetic_sources(tmp_path)
    dump = tmp_path / "source.pgcustom"
    with psycopg.connect(source_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name)))
        try:
            with psycopg.connect(source_url) as source:
                source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                baseline = capture_database_state(source)
                snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
                PostgresTools(container, source_url).dump(dump, snapshot=snapshot)
            capture_backup_set(
                database_dump=dump,
                archive_root=archive_root,
                master_key_file=key_file,
                destination=tmp_path / "backup",
            )
            restored = materialize_restore_inputs(
                backup_root=tmp_path / "backup",
                master_key_file=key_file,
                destination=tmp_path / "restore",
            )
            PostgresTools(container, target_url).restore(restored.database_dump)
            with psycopg.connect(target_url) as target:
                target.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                require_preserved_state(target, baseline, require_same_schema=True)
                assert target.execute(
                    "SELECT snapshot_sha256 FROM claim_case_snapshots WHERE claim_case_id=%s",
                    (claim.id,),
                ).fetchone() == (claim.snapshot.snapshot_sha256,)
            with ArchiveStore(restored.archive_root).open(
                metadata, master_key=MasterKey.from_file(key_file)
            ) as content:
                assert content.read() == SYNTHETIC_PLAINTEXT
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target_name)))

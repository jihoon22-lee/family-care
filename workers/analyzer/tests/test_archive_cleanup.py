"""Regression tests for preserving private import sources during cleanup.

All files in this module are generated synthetic PDFs.  The source directory
models a manually prepared, read-only import mount outside the checkout.
"""

from __future__ import annotations

from pathlib import Path

from familycare_worker.archive.store import ArchiveStore, ArchiveStoreError

from workers.analyzer.tests.test_batch_cleanup import (
    _setup,
    _successful_parser,
)
from workers.analyzer.tests.test_batch_runner import (
    SYNTHETIC_ITEM_ID_A,
    SYNTHETIC_PASSWORD_A,
    _runner,
    _scope,
)


def _source_snapshot(document_root: Path, source_key: str) -> tuple[Path, bytes]:
    source = document_root / source_key
    return source, source.read_bytes()


def test_success_preserves_the_manually_prepared_source(tmp_path: Path) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    item = repository.items[SYNTHETIC_ITEM_ID_A]
    source, original_bytes = _source_snapshot(document_root, item.source_key)
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope(SYNTHETIC_PASSWORD_A),
        _successful_parser,
    )

    assert runner.run_once("worker-a") is True

    assert source.is_file()
    assert source.read_bytes() == original_bytes
    assert repository.items[SYNTHETIC_ITEM_ID_A].state == "succeeded"
    assert list(work_root.iterdir()) == []


def test_archive_failure_preserves_the_manually_prepared_source(
    tmp_path: Path,
) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    item = repository.items[SYNTHETIC_ITEM_ID_A]
    source, original_bytes = _source_snapshot(document_root, item.source_key)
    archive_store = ArchiveStore(archive_root)

    def fail_archive(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ArchiveStoreError("ARCHIVE_WRITE_FAILED")

    archive_store.put = fail_archive  # type: ignore[method-assign]
    runner = _runner(
        repository,
        document_root,
        work_root,
        archive_store,
        _scope(SYNTHETIC_PASSWORD_A),
        _successful_parser,
    )

    assert runner.run_once("worker-a") is True

    assert source.is_file()
    assert source.read_bytes() == original_bytes
    assert repository.items[SYNTHETIC_ITEM_ID_A].error_code == "ARCHIVE_WRITE_FAILED"
    assert list(work_root.iterdir()) == []

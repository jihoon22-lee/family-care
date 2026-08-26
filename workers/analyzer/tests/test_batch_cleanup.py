"""RED tests for encrypted batch cleanup on every runner exit path."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from familycare_worker.archive.store import ArchiveStore, ArchiveStoreError
from familycare_worker.imports.batch import BatchRunner as _BatchRunner
from familycare_worker.imports.secret_channel import BatchPasswordRegistry
from familycare_worker.pdf.isolation import ParseOutcome

from workers.analyzer.tests.synthetic_pdf_factory import make_text_pdf
from workers.analyzer.tests.test_batch_runner import (
    SYNTHETIC_BATCH_ID,
    SYNTHETIC_ITEM_ID_A,
    SYNTHETIC_PASSWORD_A,
    SYNTHETIC_VERSION_ID_A,
    FakeBatchRepository,
    _item,
    _runner,
    _scope,
)

_BATCH_RUNNER_TYPE = _BatchRunner


def _successful_parser(
    source_fd: int,
    settings_json: str,
    **kwargs: object,
) -> ParseOutcome:
    del source_fd, settings_json, kwargs
    return ParseOutcome(success=True, result={"synthetic": "extraction"})


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, FakeBatchRepository]:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_text_pdf(document_root / "synthetic-cleanup.pdf")
    repository = FakeBatchRepository(_item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source))
    return document_root, work_root, archive_root, repository


def test_archive_failure_removes_decrypted_workspace_and_marks_safe_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    archive_store = ArchiveStore(archive_root)

    def fail_archive(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ArchiveStoreError("ARCHIVE_WRITE_FAILED")

    archive_store.put = fail_archive  # type: ignore[method-assign]
    logger = logging.getLogger("familycare.synthetic.batch-cleanup")
    caplog.set_level(logging.ERROR, logger=logger.name)
    runner = _runner(
        repository,
        document_root,
        work_root,
        archive_store,
        _scope(SYNTHETIC_PASSWORD_A),
        _successful_parser,
        logger=logger,
    )

    assert runner.run_once("worker-a") is True
    stored = repository.items[SYNTHETIC_ITEM_ID_A]
    assert stored.state == "permanently_failed"
    assert stored.error_code == "ARCHIVE_WRITE_FAILED"
    assert list(work_root.rglob("*.pdf")) == []
    assert list(work_root.rglob("*.png")) == []
    assert str(document_root) not in caplog.text
    assert SYNTHETIC_PASSWORD_A not in caplog.text


def test_database_failure_removes_durable_archive_orphan_and_workspace(tmp_path: Path) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    repository.fail_persist = True
    archive_store = ArchiveStore(archive_root)
    runner = _runner(
        repository,
        document_root,
        work_root,
        archive_store,
        _scope(SYNTHETIC_PASSWORD_A),
        _successful_parser,
    )

    assert runner.run_once("worker-a") is True
    assert repository.persisted == []
    assert list(archive_root.iterdir()) == []
    assert list(work_root.iterdir()) == []


def test_cancellation_cleans_workspace_without_persisting_item(tmp_path: Path) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    scope = _scope(SYNTHETIC_PASSWORD_A)

    def cancelling_parser(
        source_fd: int,
        settings_json: str,
        **kwargs: object,
    ) -> ParseOutcome:
        del source_fd, settings_json
        on_progress = kwargs.get("on_progress")
        assert callable(on_progress)
        assert on_progress() is False
        return ParseOutcome(success=False, metadata={"cancelled": True})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        scope,
        cancelling_parser,
        stop_requested=lambda: True,
    )

    assert runner.run_once("worker-a") is True
    assert repository.persisted == []
    assert repository.items[SYNTHETIC_ITEM_ID_A].state != "succeeded"
    assert list(work_root.iterdir()) == []
    assert scope.password_for(SYNTHETIC_ITEM_ID_A) is None


def test_cancellation_discards_only_the_current_batch_password(tmp_path: Path) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    other_batch_id = SYNTHETIC_ITEM_ID_A
    other_item_id = SYNTHETIC_VERSION_ID_A
    registry = BatchPasswordRegistry()
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    registry.replace(
        SYNTHETIC_BATCH_ID,
        SYNTHETIC_ITEM_ID_A,
        SYNTHETIC_PASSWORD_A,
        expires_at,
    )
    registry.replace(
        other_batch_id,
        SYNTHETIC_VERSION_ID_A,
        "synthetic-other-batch-password",
        expires_at,
    )

    def cancelling_parser(
        source_fd: int,
        settings_json: str,
        **kwargs: object,
    ) -> ParseOutcome:
        del source_fd, settings_json, kwargs
        return ParseOutcome(success=False, metadata={"cancelled": True})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        registry,
        cancelling_parser,
    )

    assert runner.run_once("worker-a") is True
    assert registry.password_for(SYNTHETIC_BATCH_ID, SYNTHETIC_ITEM_ID_A) is None
    assert registry.password_for(other_batch_id, other_item_id) == (
        "synthetic-other-batch-password"
    )


def test_shutdown_disposes_password_scope_and_is_idempotent(tmp_path: Path) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    scope = _scope(SYNTHETIC_PASSWORD_A)
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        scope,
        _successful_parser,
    )

    runner.shutdown()
    runner.shutdown()

    assert scope.password_for(SYNTHETIC_ITEM_ID_A) is None


def test_cleanup_never_logs_password_or_source_path(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    logger = logging.getLogger("familycare.synthetic.batch-cleanup-contract")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope(SYNTHETIC_PASSWORD_A),
        _successful_parser,
        logger=logger,
    )

    runner.shutdown()
    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert SYNTHETIC_PASSWORD_A not in rendered
    assert str(document_root) not in rendered
    assert str(work_root) not in rendered

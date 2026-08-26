"""RED tests for encrypted batch cleanup on every runner exit path."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

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
    SyntheticItem,
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


def _registry() -> BatchPasswordRegistry:
    registry = BatchPasswordRegistry()
    registry.replace(
        SYNTHETIC_BATCH_ID,
        SYNTHETIC_ITEM_ID_A,
        SYNTHETIC_PASSWORD_A,
        datetime.now(UTC) + timedelta(minutes=5),
    )
    return registry


class SequencedHeartbeatRepository(FakeBatchRepository):
    def __init__(self, *outcomes: bool, item: SyntheticItem) -> None:
        super().__init__(item)
        self.outcomes = list(outcomes)

    def heartbeat(self, item_id: UUID, worker_id: str) -> bool:
        if self.outcomes and not self.outcomes.pop(0):
            return False
        return super().heartbeat(item_id, worker_id)


class AmbiguousCommitRepository(FakeBatchRepository):
    def mark_succeeded(self, *args: object, **kwargs: object) -> None:
        super().mark_succeeded(*args, **kwargs)
        raise SyntheticCommitConnectionError("synthetic connection lost after commit")


class SyntheticCommitConnectionError(RuntimeError):
    pass


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


def test_database_call_failure_preserves_ciphertext_and_cleans_workspace(tmp_path: Path) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    repository.fail_persist = True
    archive_store = ArchiveStore(archive_root)
    scope = _scope(SYNTHETIC_PASSWORD_A)
    runner = _runner(
        repository,
        document_root,
        work_root,
        archive_store,
        scope,
        _successful_parser,
    )

    assert runner.run_once("worker-a") is True
    assert repository.persisted == []
    assert len(list(archive_root.iterdir())) == 1
    assert list(work_root.iterdir()) == []
    assert scope.password_for(SYNTHETIC_ITEM_ID_A) is None


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


def test_lease_loss_after_parse_discards_registry_and_deactivates_batch(tmp_path: Path) -> None:
    document_root, work_root, archive_root, base_repository = _setup(tmp_path)
    item = base_repository.items[SYNTHETIC_ITEM_ID_A]
    repository = SequencedHeartbeatRepository(False, item=item)
    registry = _registry()
    deactivated: list[object] = []
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        registry,
        _successful_parser,
        on_password_discarded=deactivated.append,
    )

    assert runner.run_once("worker-a") is True
    assert registry.password_for(SYNTHETIC_BATCH_ID, SYNTHETIC_ITEM_ID_A) is None
    assert deactivated == [SYNTHETIC_BATCH_ID]
    assert repository.persisted == []
    assert list(archive_root.iterdir()) == []
    assert list(work_root.iterdir()) == []


def test_stop_after_parser_return_discards_registry_without_progress_callback(
    tmp_path: Path,
) -> None:
    document_root, work_root, archive_root, repository = _setup(tmp_path)
    registry = _registry()
    deactivated: list[object] = []
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        registry,
        _successful_parser,
        stop_requested=lambda: True,
        on_password_discarded=deactivated.append,
    )

    assert runner.run_once("worker-a") is True
    assert registry.password_for(SYNTHETIC_BATCH_ID, SYNTHETIC_ITEM_ID_A) is None
    assert deactivated == [SYNTHETIC_BATCH_ID]
    assert repository.persisted == []
    assert list(archive_root.iterdir()) == []


def test_post_archive_lease_loss_removes_definite_orphan_and_discards_secret(
    tmp_path: Path,
) -> None:
    document_root, work_root, archive_root, base_repository = _setup(tmp_path)
    item = base_repository.items[SYNTHETIC_ITEM_ID_A]
    repository = SequencedHeartbeatRepository(True, True, False, item=item)
    registry = _registry()
    deactivated: list[object] = []
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        registry,
        _successful_parser,
        on_password_discarded=deactivated.append,
    )

    assert runner.run_once("worker-a") is True
    assert repository.persisted == []
    assert list(archive_root.iterdir()) == []
    assert registry.password_for(SYNTHETIC_BATCH_ID, SYNTHETIC_ITEM_ID_A) is None
    assert deactivated == [SYNTHETIC_BATCH_ID]


def test_ambiguous_database_commit_preserves_ciphertext_and_discards_secret(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document_root, work_root, archive_root, base_repository = _setup(tmp_path)
    item = base_repository.items[SYNTHETIC_ITEM_ID_A]
    repository = AmbiguousCommitRepository(item)
    registry = _registry()
    deactivated: list[object] = []
    logger = logging.getLogger("familycare.synthetic.ambiguous-commit")
    caplog.set_level(logging.ERROR, logger=logger.name)
    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        registry,
        _successful_parser,
        on_password_discarded=deactivated.append,
        logger=logger,
    )

    assert runner.run_once("worker-a") is True
    assert repository.items[SYNTHETIC_ITEM_ID_A].state == "succeeded"
    archive = repository.persisted[0]["archive"]
    assert (archive_root / archive.object_key).is_file()
    assert registry.password_for(SYNTHETIC_BATCH_ID, SYNTHETIC_ITEM_ID_A) is None
    assert deactivated == [SYNTHETIC_BATCH_ID]
    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert rendered == f"batch_archive_commit_uncertain item_id={SYNTHETIC_ITEM_ID_A}"
    assert archive.object_key not in rendered
    assert SYNTHETIC_PASSWORD_A not in rendered
    assert str(archive_root) not in rendered


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

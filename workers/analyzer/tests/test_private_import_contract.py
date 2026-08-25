"""RED tests for the private-import password/path persistence boundary."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from familycare_worker.archive.store import ArchiveStore
from familycare_worker.imports.batch import BatchRunner as _BatchRunner
from familycare_worker.pdf.isolation import ParseOutcome

from workers.analyzer.tests.synthetic_pdf_factory import make_encrypted_pdf
from workers.analyzer.tests.test_batch_runner import (
    SYNTHETIC_ITEM_ID_A,
    SYNTHETIC_PASSWORD_A,
    SYNTHETIC_VERSION_ID_A,
    FakeBatchRepository,
    _item,
    _runner,
    _scope,
)

_BATCH_RUNNER_TYPE = _BatchRunner


def test_password_free_parser_settings_and_persisted_item_omit_secret_and_path(
    tmp_path: Path,
    caplog,
) -> None:
    document_root = tmp_path / "synthetic-import-root"
    work_root = tmp_path / "synthetic-work-root"
    archive_root = tmp_path / "synthetic-archive-root"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_encrypted_pdf(document_root / "synthetic-private.pdf", SYNTHETIC_PASSWORD_A)
    repository = FakeBatchRepository(_item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source))
    observed_settings: list[str] = []
    logger = logging.getLogger("familycare.synthetic.private-import")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, kwargs
        observed_settings.append(settings_json)
        settings = json.loads(settings_json)
        assert "password" not in settings
        assert "source_key" not in settings
        assert "absolute_path" not in settings
        assert str(document_root) not in settings_json
        assert SYNTHETIC_PASSWORD_A not in settings_json
        return ParseOutcome(success=True, result={"synthetic": "extraction"})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope(SYNTHETIC_PASSWORD_A),
        parser,
        logger=logger,
    )

    assert runner.run_once("worker-a") is True
    persisted = json.dumps(repository.persisted, default=str, sort_keys=True)
    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert observed_settings
    assert SYNTHETIC_PASSWORD_A not in persisted
    assert str(document_root) not in persisted
    assert str(document_root) not in rendered_logs
    assert str(work_root) not in rendered_logs
    assert SYNTHETIC_PASSWORD_A not in rendered_logs


def test_password_required_projection_contains_only_stable_code_and_item_id(
    tmp_path: Path,
) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_encrypted_pdf(document_root / "synthetic-password.pdf", SYNTHETIC_PASSWORD_A)
    repository = FakeBatchRepository(_item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source))

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, settings_json, kwargs
        raise AssertionError("password failure must happen before the parser")

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-invalid-password"),
        parser,
    )

    assert runner.run_once("worker-a") is True
    assert repository.password_required == [SYNTHETIC_ITEM_ID_A]
    assert repository.failed == []
    assert repository.items[SYNTHETIC_ITEM_ID_A].error_code == "PASSWORD_REQUIRED"

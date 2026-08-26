"""RED tests for the encrypted document batch item runner.

All inputs in this module are synthetic.  The repository double deliberately
models only the lease/state boundary consumed by ``BatchRunner``; PostgreSQL
integration coverage for the physical tables remains in the API tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from familycare_worker.archive.crypto import ArchiveMetadata
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore
from familycare_worker.imports.batch import BatchRunner
from familycare_worker.imports.password_scope import PasswordScope
from familycare_worker.imports.secret_channel import BatchPasswordRegistry
from familycare_worker.ocr.models import (
    EnginePageResult,
    OcrExecutionError,
    RawOcrBlock,
    RenderedPage,
)
from familycare_worker.ocr.processor import SelectiveOcrProcessor
from familycare_worker.pdf.isolation import ParseOutcome

from workers.analyzer.tests.synthetic_pdf_factory import (
    make_encrypted_pdf,
    make_low_quality_pdf,
    make_table_pdf,
    make_text_pdf,
)

SYNTHETIC_BATCH_ID = UUID("00000000-0000-4000-8000-000000000501")
SYNTHETIC_ITEM_ID_A = UUID("00000000-0000-4000-8000-000000000511")
SYNTHETIC_ITEM_ID_B = UUID("00000000-0000-4000-8000-000000000512")
SYNTHETIC_VERSION_ID_A = UUID("00000000-0000-4000-8000-000000000521")
SYNTHETIC_VERSION_ID_B = UUID("00000000-0000-4000-8000-000000000522")
SYNTHETIC_PASSWORD_A = "synthetic-policy-password-a"
SYNTHETIC_PASSWORD_B = "synthetic-policy-password-b"
SYNTHETIC_MASTER_KEY = b"synthetic-master-key-00000000000"


@dataclass
class SyntheticItem:
    id: UUID
    source_id: str
    source_key: str
    document_version_id: UUID
    batch_id: UUID = SYNTHETIC_BATCH_ID
    state: str = "queued"
    attempts: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    error_code: str | None = None


class SyntheticDatabaseFailure(RuntimeError):
    """Synthetic database failure used to verify archive orphan cleanup."""


class FakeBatchRepository:
    """Small lease-safe repository double for the runner contract."""

    def __init__(self, *items: SyntheticItem) -> None:
        self.items = {item.id: item for item in items}
        self.archive_root: Path | None = None
        self.claims: list[tuple[UUID, str]] = []
        self.persisted: list[dict[str, Any]] = []
        self.password_required: list[UUID] = []
        self.failed: list[tuple[UUID, str]] = []
        self.ocr_progress: list[tuple[UUID, str, int, tuple[str, ...]]] = []
        self.fail_persist = False

    def get_item(self, item_id: UUID) -> SyntheticItem:
        return self.items[self._item_id(item_id)]

    def claim_next_item(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 180,
    ) -> SyntheticItem | None:
        now = datetime.now(UTC)
        for item in self.items.values():
            if item.state == "running" and item.lease_expires_at is not None:
                if item.lease_expires_at <= now:
                    item.state = "queued"
                    item.lease_owner = None
                    item.lease_expires_at = None
                else:
                    continue
            if item.state not in {"queued", "retryable_failed"}:
                continue
            item.state = "running"
            item.attempts += 1
            item.lease_owner = worker_id
            item.lease_expires_at = now + timedelta(seconds=lease_seconds)
            self.claims.append((item.id, worker_id))
            return item
        return None

    def expire(self, item_id: UUID) -> None:
        item = self.items[self._item_id(item_id)]
        item.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    def heartbeat(self, item_id: UUID, worker_id: str) -> bool:
        item = self.items[self._item_id(item_id)]
        if (
            item.state != "running"
            or item.lease_owner != worker_id
            or item.lease_expires_at is None
            or item.lease_expires_at <= datetime.now(UTC)
        ):
            return False
        item.lease_expires_at = datetime.now(UTC) + timedelta(minutes=3)
        return True

    def mark_password_required(self, item_id: UUID, worker_id: str, **_: object) -> None:
        self._assert_owner(item_id, worker_id)
        item = self.items[self._item_id(item_id)]
        item.state = "password_required"
        item.error_code = "PASSWORD_REQUIRED"
        item.lease_owner = None
        item.lease_expires_at = None
        self.password_required.append(self._item_id(item_id))

    def mark_failed(self, item_id: UUID, worker_id: str, error_code: str, **_: object) -> None:
        self._assert_owner(item_id, worker_id)
        error_code = str(getattr(error_code, "value", error_code))
        item = self.items[self._item_id(item_id)]
        item.state = "permanently_failed"
        item.error_code = error_code
        item.lease_owner = None
        item.lease_expires_at = None
        self.failed.append((self._item_id(item_id), error_code))

    def mark_ocr_progress(
        self,
        item_id: UUID,
        worker_id: str,
        *,
        state: str,
        pages_processed: int,
        warning_codes: tuple[str, ...] = (),
    ) -> bool:
        self._assert_owner(item_id, worker_id)
        self.ocr_progress.append((self._item_id(item_id), state, pages_processed, warning_codes))
        return self.heartbeat(item_id, worker_id)

    def mark_succeeded(
        self,
        item_id: UUID,
        worker_id: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        self._assert_owner(item_id, worker_id)
        if self.fail_persist:
            raise SyntheticDatabaseFailure("synthetic database unavailable")
        archive = kwargs.get("archive") or kwargs.get("archive_metadata")
        extraction = kwargs.get("extraction") or kwargs.get("result")
        ocr = kwargs.get("ocr")
        if archive is None:
            archive = next((value for value in args if isinstance(value, ArchiveMetadata)), None)
        if extraction is None:
            extraction = next((value for value in args if value is not archive), None)
        assert isinstance(archive, ArchiveMetadata)
        if self.archive_root is not None:
            assert (self.archive_root / archive.object_key).is_file()
        item = self.items[self._item_id(item_id)]
        item.state = "succeeded"
        item.error_code = None
        item.lease_owner = None
        item.lease_expires_at = None
        self.persisted.append(
            {
                "item_id": self._item_id(item_id),
                "archive": archive,
                "extraction": extraction,
                "ocr": ocr,
            }
        )

    # Keep the test double compatible with the small naming variants used by
    # repository implementations while retaining one observable state change.
    complete_item = mark_succeeded
    persist_item_success = mark_succeeded
    persist_success = mark_succeeded
    fail_item = mark_failed
    mark_item_failed = mark_failed
    mark_item_password_required = mark_password_required

    def requeue_password_required(self, item_ids: tuple[UUID, ...]) -> None:
        """Explicitly requeue only the requested failed siblings."""

        for item_id in item_ids:
            item = self.items[item_id]
            assert item.state == "password_required"
            item.state = "queued"
            item.error_code = None

    def _assert_owner(self, item_id: UUID, worker_id: str) -> None:
        item = self.items[self._item_id(item_id)]
        assert item.state == "running"
        assert item.lease_owner == worker_id

    @staticmethod
    def _item_id(value: UUID | SyntheticItem) -> UUID:
        return value.id if isinstance(value, SyntheticItem) else value


def _source_id(path: Path) -> str:
    content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    digest.update(path.name.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content_sha256.encode("ascii"))
    return digest.hexdigest()


def _item(
    item_id: UUID,
    version_id: UUID,
    path: Path,
    *,
    source_id: str | None = None,
) -> SyntheticItem:
    return SyntheticItem(
        id=item_id,
        source_id=source_id or _source_id(path),
        source_key=path.name,
        document_version_id=version_id,
    )


def _scope(password: str) -> PasswordScope:
    return PasswordScope(
        batch_id=SYNTHETIC_BATCH_ID,
        password=password,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _runner(
    repository: FakeBatchRepository,
    document_root: Path,
    work_root: Path,
    archive_store: ArchiveStore,
    password_scope: PasswordScope | BatchPasswordRegistry,
    parser: Callable[..., ParseOutcome],
    **kwargs: object,
) -> BatchRunner:
    return BatchRunner(
        repository=repository,
        document_root=document_root,
        work_root=work_root,
        archive_store=archive_store,
        master_key=MasterKey.synthetic(SYNTHETIC_MASTER_KEY, key_version="synthetic-v1"),
        password_scope=password_scope,
        parser_runner=parser,
        **kwargs,
    )


def _native_extraction(settings_json: str, *, classification: str) -> dict[str, object]:
    settings = json.loads(settings_json)
    quality = {
        "rule_version": "quality-v1",
        "classification": classification,
        "non_whitespace_chars": 0 if classification == "OCR_REQUIRED" else 30,
        "alphanumeric_ratio": 0.0 if classification == "OCR_REQUIRED" else 0.8,
        "replacement_character_ratio": 0.0,
        "maximum_repeated_character_run": 0,
    }
    return {
        "schema_version": "1",
        "content_sha256": settings["content_sha256"],
        "extractor_name": "synthetic-native",
        "extractor_version": "1",
        "extractor_config_hash": settings["extractor_config_hash"],
        "quality_rule_version": "quality-v1",
        "pages": [
            {
                "page_number": 1,
                "width_points": 612.0,
                "height_points": 792.0,
                "quality": quality,
                "blocks": [],
                "tables": [],
                "warning_codes": ["OCR_REQUIRED"] if classification == "OCR_REQUIRED" else [],
            }
        ],
        "evidence": [
            {
                "document_version_id": settings["document_version_id"],
                "page_number": 1,
                "content_sha256": settings["content_sha256"],
                "review_state": "candidate",
            }
        ],
    }


class BatchOcrRenderer:
    def __init__(self) -> None:
        self.pages: list[int] = []

    def render(
        self,
        source_fd: int,
        page_number: int,
        output: Any,
        *,
        dpi: int,
    ) -> RenderedPage:
        assert isinstance(source_fd, int)
        self.pages.append(page_number)
        output.write(b"synthetic-image")
        output.flush()
        return RenderedPage(page_number, dpi, 1224, 1584)


class BatchOcrEngine:
    engine_version = "synthetic-engine-1"

    def __init__(self, *, error: OcrExecutionError | None = None) -> None:
        self.error = error
        self.calls = 0

    def recognize(self, image_path: Path, *, languages: tuple[str, ...]) -> EnginePageResult:
        assert image_path.is_file()
        assert languages == ("kor", "eng")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return EnginePageResult(
            engine_name="tesseract",
            engine_version=self.engine_version,
            image_width_pixels=1224,
            image_height_pixels=1584,
            blocks=(
                RawOcrBlock(
                    text="Synthetic OCR Evidence",
                    pixel_bbox=(122, 158, 612, 316),
                    reading_order=0,
                    confidence=96.0,
                ),
            ),
            warning_codes=(),
        )


def test_two_workers_claim_distinct_items_and_recover_expired_lease(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source_a = make_text_pdf(document_root / "synthetic-a.pdf")
    source_b = make_text_pdf(document_root / "synthetic-b.pdf")
    source_c = make_text_pdf(document_root / "synthetic-c.pdf")
    first = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source_a)
    second = _item(SYNTHETIC_ITEM_ID_B, SYNTHETIC_VERSION_ID_B, source_b)
    third = SyntheticItem(
        id=UUID("00000000-0000-4000-8000-000000000513"),
        source_id=_source_id(source_c),
        source_key=source_c.name,
        document_version_id=UUID("00000000-0000-4000-8000-000000000523"),
    )
    repository = FakeBatchRepository(first, second, third)

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, settings_json, kwargs
        return ParseOutcome(success=True, result={"synthetic": "extraction"})

    runner_a = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-lease-password"),
        parser,
    )
    runner_b = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-lease-password"),
        parser,
    )

    assert runner_a.run_once("worker-a") is True
    assert runner_b.run_once("worker-b") is True
    assert {item_id for item_id, _ in repository.claims[:2]} == {
        SYNTHETIC_ITEM_ID_A,
        SYNTHETIC_ITEM_ID_B,
    }

    third.state = "running"
    third.attempts = 1
    third.lease_owner = "worker-old"
    third.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert runner_b.run_once("worker-new") is True
    assert third.attempts == 2
    assert third.lease_owner is None
    assert third.state == "succeeded"
    assert (third.id, "worker-new") in repository.claims


def test_encrypted_pdf_is_decrypted_to_0600_workspace_before_password_free_parser(
    tmp_path: Path,
) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_encrypted_pdf(document_root / "synthetic-encrypted.pdf", SYNTHETIC_PASSWORD_A)
    item = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source)
    repository = FakeBatchRepository(item)
    repository.archive_root = archive_root
    parser_calls: list[tuple[int, dict[str, object]]] = []

    def password_free_parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del kwargs
        assert isinstance(source_fd, int)
        assert not isinstance(source_fd, Path)
        assert stat.S_IMODE(os.fstat(source_fd).st_mode) == 0o600
        os.lseek(source_fd, 0, os.SEEK_SET)
        assert os.read(source_fd, 5) == b"%PDF-"
        settings = json.loads(settings_json)
        parser_calls.append((source_fd, settings))
        assert set(settings) == {
            "content_sha256",
            "document_version_id",
            "extractor_config_hash",
            "quality_rule_version",
            "table_strategy",
        }
        return ParseOutcome(success=True, result={"synthetic": "extraction"})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope(SYNTHETIC_PASSWORD_A),
        password_free_parser,
    )

    assert runner.run_once("worker-a") is True
    assert repository.items[SYNTHETIC_ITEM_ID_A].state == "succeeded"
    assert len(parser_calls) == 1
    assert list(work_root.iterdir()) == []


def test_wrong_password_becomes_password_required_without_automatic_retry(
    tmp_path: Path,
) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_encrypted_pdf(document_root / "synthetic-encrypted.pdf", SYNTHETIC_PASSWORD_A)
    item = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source)
    repository = FakeBatchRepository(item)
    parser_calls = 0

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        nonlocal parser_calls
        del source_fd, settings_json, kwargs
        parser_calls += 1
        return ParseOutcome(success=True, result={"synthetic": "unexpected"})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-wrong-password"),
        parser,
    )

    assert runner.run_once("worker-a") is True
    stored = repository.items[SYNTHETIC_ITEM_ID_A]
    assert stored.state == "password_required"
    assert stored.error_code == "PASSWORD_REQUIRED"
    assert stored.attempts == 1
    assert parser_calls == 0
    assert runner.run_once("worker-a") is False


def test_explicit_password_requeue_runs_only_failed_sibling(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source_a = make_encrypted_pdf(document_root / "synthetic-a.pdf", SYNTHETIC_PASSWORD_A)
    source_b = make_encrypted_pdf(document_root / "synthetic-b.pdf", SYNTHETIC_PASSWORD_B)
    item_a = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source_a)
    item_b = _item(SYNTHETIC_ITEM_ID_B, SYNTHETIC_VERSION_ID_B, source_b)
    repository = FakeBatchRepository(item_a, item_b)
    parsed: list[UUID] = []

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, kwargs
        parsed.append(UUID(json.loads(settings_json)["document_version_id"]))
        return ParseOutcome(success=True, result={"synthetic": "extraction"})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope(SYNTHETIC_PASSWORD_A),
        parser,
    )

    assert runner.run_once("worker-a") is True
    assert repository.items[SYNTHETIC_ITEM_ID_A].state == "succeeded"
    assert runner.run_once("worker-a") is True
    assert repository.items[SYNTHETIC_ITEM_ID_B].state == "password_required"

    repository.requeue_password_required((SYNTHETIC_ITEM_ID_B,))
    runner.password_scope.replace(
        SYNTHETIC_PASSWORD_B,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert runner.run_once("worker-a") is True

    assert repository.items[SYNTHETIC_ITEM_ID_A].attempts == 1
    assert repository.items[SYNTHETIC_ITEM_ID_A].state == "succeeded"
    assert repository.items[SYNTHETIC_ITEM_ID_B].attempts == 2
    assert repository.items[SYNTHETIC_ITEM_ID_B].state == "succeeded"
    assert parsed == [SYNTHETIC_VERSION_ID_A, SYNTHETIC_VERSION_ID_B]


def test_source_replacement_is_rejected_before_parse_or_archive(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_text_pdf(document_root / "synthetic-replaced.pdf")
    original_source_id = _source_id(source)
    item = _item(
        SYNTHETIC_ITEM_ID_A,
        SYNTHETIC_VERSION_ID_A,
        source,
        source_id=original_source_id,
    )
    repository = FakeBatchRepository(item)
    replacement = make_table_pdf(tmp_path / "synthetic-replacement.pdf")
    source.write_bytes(replacement.read_bytes())
    parser_calls = 0

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        nonlocal parser_calls
        del source_fd, settings_json, kwargs
        parser_calls += 1
        return ParseOutcome(success=True, result={"synthetic": "unexpected"})

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-unused-password"),
        parser,
    )

    assert runner.run_once("worker-a") is True
    stored = repository.items[SYNTHETIC_ITEM_ID_A]
    assert stored.state == "permanently_failed"
    assert stored.error_code == "SOURCE_CHANGED"
    assert parser_calls == 0
    assert repository.persisted == []
    assert list(archive_root.iterdir()) == []


def test_batch_runner_processes_ocr_required_before_archive_and_persists_layer(
    tmp_path: Path,
) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_low_quality_pdf(document_root / "synthetic-scanned.pdf")
    item = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source)
    repository = FakeBatchRepository(item)
    renderer = BatchOcrRenderer()
    engine = BatchOcrEngine()

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, kwargs
        return ParseOutcome(
            success=True,
            result=_native_extraction(settings_json, classification="OCR_REQUIRED"),
        )

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-unused-password"),
        parser,
        ocr_processor=SelectiveOcrProcessor(renderer, lambda: engine),
    )

    assert runner.run_once("worker-a") is True

    assert renderer.pages == [1]
    assert engine.calls == 1
    assert repository.ocr_progress == [
        (SYNTHETIC_ITEM_ID_A, "running", 0, ()),
        (SYNTHETIC_ITEM_ID_A, "running", 1, ()),
    ]
    assert len(repository.persisted) == 1
    result = repository.persisted[0]["ocr"]
    assert result is not None
    assert result.pages[0].blocks[0].source_layer == "ocr"
    assert len(list(archive_root.iterdir())) == 1
    assert list(work_root.iterdir()) == []


def test_batch_runner_marks_text_sufficient_document_native_only(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_text_pdf(document_root / "synthetic-native.pdf")
    item = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source)
    repository = FakeBatchRepository(item)

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, kwargs
        return ParseOutcome(
            success=True,
            result=_native_extraction(settings_json, classification="TEXT_SUFFICIENT"),
        )

    def engine_must_not_exist() -> BatchOcrEngine:
        raise AssertionError("TEXT_SUFFICIENT must not create an OCR engine")

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-unused-password"),
        parser,
        ocr_processor=SelectiveOcrProcessor(BatchOcrRenderer(), engine_must_not_exist),
    )

    assert runner.run_once("worker-a") is True
    assert repository.ocr_progress == [(SYNTHETIC_ITEM_ID_A, "native_only", 0, ())]
    assert repository.persisted[0]["ocr"] is None


def test_batch_runner_ocr_timeout_creates_no_archive_or_success(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    document_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    source = make_low_quality_pdf(document_root / "synthetic-timeout.pdf")
    item = _item(SYNTHETIC_ITEM_ID_A, SYNTHETIC_VERSION_ID_A, source)
    repository = FakeBatchRepository(item)

    def parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, kwargs
        return ParseOutcome(
            success=True,
            result=_native_extraction(settings_json, classification="OCR_REQUIRED"),
        )

    runner = _runner(
        repository,
        document_root,
        work_root,
        ArchiveStore(archive_root),
        _scope("synthetic-unused-password"),
        parser,
        ocr_processor=SelectiveOcrProcessor(
            BatchOcrRenderer(),
            lambda: BatchOcrEngine(error=OcrExecutionError("OCR_TIMEOUT")),
        ),
    )

    assert runner.run_once("worker-a") is True

    assert repository.failed == [(SYNTHETIC_ITEM_ID_A, "OCR_TIMEOUT")]
    assert repository.persisted == []
    assert list(archive_root.iterdir()) == []
    assert list(work_root.iterdir()) == []

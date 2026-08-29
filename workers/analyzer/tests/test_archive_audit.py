"""Synthetic tests for the count-only, read-only managed archive audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from familycare_worker.archive import audit
from familycare_worker.archive.audit import (
    ArchiveAuditError,
    ArchiveReference,
    load_archive_references,
    main,
    reconcile_archive,
)

OBJECT_A = "a" * 32
OBJECT_B = "b" * 32
OBJECT_C = "c" * 32
OBJECT_D = "d" * 32
TEMPORARY_OBJECT = ".tmp-" + ("e" * 32)


def _archive_root(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic-archive"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _archive_object(root: Path, name: str, payload: bytes) -> Path:
    path = root / name
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def test_reconcile_reports_only_stable_counts_and_does_not_modify_entries(
    tmp_path: Path,
) -> None:
    root = _archive_root(tmp_path)
    matched = _archive_object(root, OBJECT_A, b"matched")
    mismatched = _archive_object(root, OBJECT_B, b"size-mismatch")
    unreferenced = _archive_object(root, OBJECT_C, b"unreferenced")
    temporary = _archive_object(root, TEMPORARY_OBJECT, b"temporary")
    unexpected = _archive_object(root, "unexpected.txt", b"unexpected")
    before = {path.name: (path.read_bytes(), path.stat().st_mode) for path in root.iterdir()}

    report = reconcile_archive(
        root,
        (
            ArchiveReference(OBJECT_A, matched.stat().st_size),
            ArchiveReference(OBJECT_B, mismatched.stat().st_size + 1),
            ArchiveReference(OBJECT_D, 12),
        ),
    )

    assert report.to_payload() == {
        "archive_object_count": 3,
        "database_reference_count": 3,
        "matched": 1,
        "missing_references": 1,
        "size_mismatches": 1,
        "status": "findings",
        "temporary_entries": 1,
        "unexpected_entries": 1,
        "unreferenced_objects": 1,
    }
    rendered = json.dumps(report.to_payload(), sort_keys=True)
    assert all(
        object_key not in rendered for object_key in (OBJECT_A, OBJECT_B, OBJECT_C, OBJECT_D)
    )
    assert str(root) not in rendered
    after = {path.name: (path.read_bytes(), path.stat().st_mode) for path in root.iterdir()}
    assert after == before
    assert temporary.exists()
    assert unexpected.exists()
    assert unreferenced.exists()


def test_reconcile_clean_archive_has_zero_findings(tmp_path: Path) -> None:
    root = _archive_root(tmp_path)
    first = _archive_object(root, OBJECT_A, b"first")
    second = _archive_object(root, OBJECT_B, b"second")

    report = reconcile_archive(
        root,
        (
            ArchiveReference(OBJECT_A, first.stat().st_size),
            ArchiveReference(OBJECT_B, second.stat().st_size),
        ),
    )

    assert report.is_clean is True
    assert report.to_payload()["status"] == "clean"
    assert (
        sum(
            report.to_payload()[name]
            for name in (
                "missing_references",
                "size_mismatches",
                "temporary_entries",
                "unexpected_entries",
                "unreferenced_objects",
            )
        )
        == 0
    )


@pytest.mark.parametrize(
    "references",
    [
        (ArchiveReference(OBJECT_A, 1), ArchiveReference(OBJECT_A, 1)),
        (ArchiveReference(OBJECT_A, 1), ArchiveReference(OBJECT_A, 2)),
    ],
)
def test_reconcile_rejects_duplicate_database_references(
    tmp_path: Path,
    references: tuple[ArchiveReference, ArchiveReference],
) -> None:
    root = _archive_root(tmp_path)

    with pytest.raises(ArchiveAuditError, match="^ARCHIVE_AUDIT_REFERENCE_INVALID$"):
        reconcile_archive(root, references)


@pytest.mark.parametrize(
    ("object_key", "ciphertext_size"),
    [("not-an-object-key", 1), (OBJECT_A, -1), (OBJECT_A, 128 * 1024 * 1024 + 1)],
)
def test_reference_rejects_invalid_metadata_without_echoing_it(
    object_key: str,
    ciphertext_size: int,
) -> None:
    with pytest.raises(ArchiveAuditError) as raised:
        ArchiveReference(object_key, ciphertext_size)

    assert str(raised.value) == "ARCHIVE_AUDIT_REFERENCE_INVALID"
    assert object_key not in repr(raised.value)


def test_reconcile_counts_symlinks_as_unexpected_without_following_them(tmp_path: Path) -> None:
    root = _archive_root(tmp_path)
    target = _archive_object(tmp_path, "synthetic-target", b"outside")
    link = root / OBJECT_A
    link.symlink_to(target)

    report = reconcile_archive(root, ())

    assert report.archive_object_count == 0
    assert report.unexpected_entries == 1
    assert target.read_bytes() == b"outside"
    assert link.is_symlink()


def test_main_emits_count_only_json_and_exit_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _archive_root(tmp_path)
    item = _archive_object(root, OBJECT_A, b"synthetic")
    observed_database_urls: list[str] = []

    def references(database_url: str) -> tuple[ArchiveReference, ...]:
        observed_database_urls.append(database_url)
        return (ArchiveReference(OBJECT_A, item.stat().st_size),)

    exit_code = main(
        [],
        environ={
            "FAMILYCARE_ARCHIVE_ROOT": str(root),
            "FAMILYCARE_DATABASE_URL": "postgresql://synthetic",
        },
        reference_loader=references,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "clean"
    assert captured.err == ""
    assert observed_database_urls == ["postgresql://synthetic"]
    assert OBJECT_A not in captured.out
    assert str(root) not in captured.out
    assert "postgresql://synthetic" not in captured.out


def test_main_returns_one_for_findings_without_removing_temporary_entry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _archive_root(tmp_path)
    temporary = _archive_object(root, TEMPORARY_OBJECT, b"incomplete")

    exit_code = main(
        [],
        environ={
            "FAMILYCARE_ARCHIVE_ROOT": str(root),
            "FAMILYCARE_DATABASE_URL": "postgresql://synthetic",
        },
        reference_loader=lambda _: (),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "findings"
    assert payload["temporary_entries"] == 1
    assert temporary.exists()


def test_main_sanitizes_configuration_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [],
        environ={"FAMILYCARE_DATABASE_URL": "postgresql://synthetic-sensitive"},
        reference_loader=lambda _: (),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "ARCHIVE_AUDIT_CONFIGURATION_ERROR",
        "status": "error",
    }
    assert "synthetic-sensitive" not in captured.err


def test_database_loader_starts_read_only_transaction_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class Result:
        def fetchall(self) -> list[dict[str, object]]:
            return [{"object_key": OBJECT_A, "ciphertext_size": 9}]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            calls["exited"] = True

        def execute(self, query: str, parameters: object = None) -> Result:
            calls.setdefault("queries", []).append((query, parameters))
            return Result()

        def rollback(self) -> None:
            calls["rolled_back"] = True

    def connect(database_url: str, **kwargs: object) -> Connection:
        calls["database_url"] = database_url
        calls["kwargs"] = kwargs
        return Connection()

    monkeypatch.setattr(audit.psycopg, "connect", connect)

    references = load_archive_references("postgresql+psycopg://synthetic")

    assert references == (ArchiveReference(OBJECT_A, 9),)
    assert calls["database_url"] == "postgresql://synthetic"
    assert calls["rolled_back"] is True
    options = calls["kwargs"]
    assert isinstance(options, dict)
    assert options["options"] == "-c default_transaction_read_only=on"
    queries = calls["queries"]
    assert isinstance(queries, list)
    rendered_queries = " ".join(str(query) for query, _ in queries).upper()
    assert "REPEATABLE READ READ ONLY" in rendered_queries
    assert "SELECT OBJECT_KEY, CIPHERTEXT_SIZE" in rendered_queries
    assert all(keyword not in rendered_queries for keyword in ("DELETE", "INSERT", "UPDATE"))

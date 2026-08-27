"""Migration contract for the 128 MiB managed archive capacity."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0014_private_import_capacity.py"


class RecordingOperations:
    """Record the forward and reverse check-constraint operations."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def drop_constraint(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("drop_constraint", args, kwargs))

    def create_check_constraint(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("create_check_constraint", args, kwargs))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("private_import_capacity", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_is_forward_after_selective_ocr_and_preserves_0012() -> None:
    migration = load_migration()

    assert migration.revision == "0014_private_import_capacity"
    assert migration.down_revision == "0013_selective_ocr"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_raises_ciphertext_size_check_to_128_mib() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.upgrade()

    assert operations.calls == [
        (
            "drop_constraint",
            ("ck_managed_archives_ciphertext_size_limit", "managed_archives"),
            {"type_": "check"},
        ),
        (
            "create_check_constraint",
            (
                "ck_managed_archives_ciphertext_size_limit",
                "managed_archives",
                "ciphertext_size <= 134217728",
            ),
            {},
        ),
    ]


def test_downgrade_restores_historical_64_mib_ciphertext_check() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.calls == [
        (
            "drop_constraint",
            ("ck_managed_archives_ciphertext_size_limit", "managed_archives"),
            {"type_": "check"},
        ),
        (
            "create_check_constraint",
            (
                "ck_managed_archives_ciphertext_size_limit",
                "managed_archives",
                "ciphertext_size <= 67108864",
            ),
            {},
        ),
    ]

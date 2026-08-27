"""RED coverage for explicit private-batch document classification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from familycare_api.documents.batch_repository import (
    BatchItemRecord,
    BatchRecord,
    BatchRepository,
)
from familycare_api.documents.batch_router import BatchCreateRequest, BatchItemResponse
from familycare_api.documents.batch_service import _projection
from familycare_api.documents.import_sources import ResolvedImportSource
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0015_private_batch_document_kind.py"

FAMILY_MEMBER_ID = UUID("00000000-0000-4000-8000-000000000004")
BATCH_ID = UUID("00000000-0000-4000-8000-000000000005")
SOURCE_ID_A = "a" * 64
SOURCE_ID_B = "b" * 64


def _source(source_id: str, document_kind: str) -> dict[str, str]:
    return {"source_id": source_id, "document_kind": document_kind}


def _request(*sources: dict[str, str], **extra: object) -> dict[str, object]:
    return {
        "schema_version": "1",
        "family_member_id": str(FAMILY_MEMBER_ID),
        "sources": list(sources),
        **extra,
    }


def test_private_batch_request_requires_explicit_kind_for_each_source() -> None:
    request = BatchCreateRequest.model_validate(
        _request(_source(SOURCE_ID_A, "policy"), _source(SOURCE_ID_B, "terms"))
    )

    assert [item.source_id for item in request.sources] == [SOURCE_ID_A, SOURCE_ID_B]
    assert [item.document_kind for item in request.sources] == ["policy", "terms"]


def test_private_batch_request_preserves_the_existing_100_source_limit() -> None:
    sources = [_source(f"{index:064x}", "supporting") for index in range(100)]

    request = BatchCreateRequest.model_validate(_request(*sources))

    assert len(request.sources) == 100
    with pytest.raises(ValidationError):
        BatchCreateRequest.model_validate(_request(*sources, _source(f"{100:064x}", "supporting")))


def test_repository_rejects_a_source_without_an_explicit_selection() -> None:
    source = ResolvedImportSource(
        source_id=SOURCE_ID_A,
        source_key="synthetic/Sample Policy.pdf",
        display_label="Sample Policy.pdf",
        size_bytes=256,
        encrypted=False,
    )

    with pytest.raises(TypeError, match="explicit batch source selection required"):
        BatchRepository("postgresql://synthetic.invalid/familycare").create(
            household_space_id=UUID("00000000-0000-4000-8000-000000000001"),
            created_by=UUID("00000000-0000-4000-8000-000000000002"),
            family_member_id=FAMILY_MEMBER_ID,
            sources=cast(Any, (source,)),
        )


@pytest.mark.parametrize(
    "payload",
    [
        _request(_source(SOURCE_ID_A, "policy"), _source(SOURCE_ID_A, "terms")),
        _request(_source(SOURCE_ID_A, "rider")),
        _request(_source("synthetic/import-root/policy.pdf", "policy")),
        _request(_source(SOURCE_ID_A, "policy"), source_ids=[SOURCE_ID_A]),
        _request(_source(SOURCE_ID_A, "policy"), document_kind="policy"),
    ],
)
def test_private_batch_request_rejects_duplicate_unsupported_path_and_mixed_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        BatchCreateRequest.model_validate(payload)


def test_private_batch_status_item_and_projection_include_kind_without_source_key() -> None:
    item = BatchItemResponse(
        source_id=SOURCE_ID_A,
        display_label="Sample Policy.pdf",
        document_kind="policy",
        state="queued",
        error_code=None,
        attempts=0,
        ocr_state="pending",
        ocr_pages_processed=0,
        ocr_warning_codes=[],
    )
    projected = _projection(
        BatchRecord(
            batch_id=BATCH_ID,
            family_member_id=FAMILY_MEMBER_ID,
            state="created",
            items=(
                BatchItemRecord(
                    source_id=SOURCE_ID_A,
                    display_label="Sample Policy.pdf",
                    document_kind="policy",
                    state="queued",
                    error_code=None,
                    attempts=0,
                    ocr_state="pending",
                    ocr_pages_processed=0,
                    ocr_warning_codes=(),
                ),
            ),
        )
    )

    assert item.document_kind == "policy"
    assert projected["items"] == [
        {
            "source_id": SOURCE_ID_A,
            "display_label": "Sample Policy.pdf",
            "document_kind": "policy",
            "state": "queued",
            "error_code": None,
            "attempts": 0,
            "ocr_state": "pending",
            "ocr_pages_processed": 0,
            "ocr_warning_codes": [],
        }
    ]
    assert "source_key" not in str(projected)


class _RecordingOperations:
    def __init__(self) -> None:
        self.added_columns: dict[str, list[sa.Column[Any]]] = {}
        self.check_constraints: dict[str, dict[str, str]] = {}
        self.dropped_columns: list[tuple[str, str]] = []
        self.dropped_constraints: list[tuple[str, str]] = []
        self.altered_columns: list[tuple[str, str, dict[str, object]]] = []

    def add_column(self, table_name: str, column: sa.Column[Any], **_: object) -> None:
        self.added_columns.setdefault(table_name, []).append(column)

    def create_check_constraint(
        self,
        name: str,
        table_name: str,
        condition: str,
        **_: object,
    ) -> None:
        self.check_constraints.setdefault(table_name, {})[name] = condition

    def alter_column(
        self,
        table_name: str,
        column_name: str,
        **kwargs: object,
    ) -> None:
        self.altered_columns.append((table_name, column_name, kwargs))

    def drop_column(self, table_name: str, column_name: str, **_: object) -> None:
        self.dropped_columns.append((table_name, column_name))

    def drop_constraint(self, name: str, table_name: str, **_: object) -> None:
        self.dropped_constraints.append((table_name, name))


def _load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("private_batch_document_kind", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_forward_migration_adds_checked_document_kind_column() -> None:
    migration = cast(Any, _load_migration())
    operations = _RecordingOperations()
    migration.op = operations

    assert migration.revision == "0015_private_batch_document_kind"
    assert migration.down_revision == "0014_private_import_capacity"
    migration.upgrade()

    column = operations.added_columns["document_batch_items"][0]
    assert column.name == "document_kind"
    assert isinstance(column.type, sa.String)
    assert column.type.length == 16
    assert column.nullable is False
    assert str(column.server_default.arg) == "'supporting'"
    assert operations.check_constraints["document_batch_items"] == {
        "ck_document_batch_items_document_kind": (
            "document_kind IN ('policy', 'terms', 'supporting')"
        )
    }
    assert len(operations.altered_columns) == 1
    table_name, column_name, kwargs = operations.altered_columns[0]
    assert (table_name, column_name) == ("document_batch_items", "document_kind")
    assert isinstance(kwargs["existing_type"], sa.String)
    assert cast(sa.String, kwargs["existing_type"]).length == 16
    assert kwargs["existing_nullable"] is False
    assert kwargs["server_default"] is None

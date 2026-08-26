"""Authenticated use cases for private document-import batches."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from familycare_api.documents.batch_repository import (
    BatchRecord,
    BatchRepository,
    BatchRepositoryUnavailable,
)
from familycare_api.documents.import_sources import ImportSourceCatalog, ImportSourceNotFound
from familycare_api.documents.secret_channel import (
    BatchSecretSocketClient,
    SecretChannelError,
    SecretHandoff,
)
from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.context import AuthContext
from familycare_api.identity.router import ReauthenticationRequired


class DocumentBatchNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "DOCUMENT_NOT_FOUND"
    public_message = "document not found"


class DocumentBatchUnavailable(ApiBoundaryError):
    status_code = 503
    error_code = "RESOURCE_LIMIT_EXCEEDED"
    public_message = "document import service unavailable"


def _projection(batch: BatchRecord) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "batch_id": str(batch.batch_id),
        "family_member_id": str(batch.family_member_id),
        "state": batch.state,
        "items": [
            {
                "source_id": item.source_id,
                "display_label": item.display_label,
                "state": item.state,
                "error_code": item.error_code,
                "attempts": item.attempts,
                "ocr_state": item.ocr_state,
                "ocr_pages_processed": item.ocr_pages_processed,
                "ocr_warning_codes": list(item.ocr_warning_codes),
            }
            for item in batch.items
        ],
    }


class BatchService:
    def __init__(
        self,
        repository: BatchRepository,
        catalog: ImportSourceCatalog,
        secret_client: BatchSecretSocketClient,
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        self.secret_client = secret_client

    @classmethod
    def from_environment(cls) -> BatchService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL", "")
        import_root = os.getenv("FAMILYCARE_IMPORT_ROOT", "")
        socket_path = os.getenv("FAMILYCARE_SECRET_SOCKET", "/run/familycare/secret.sock")
        if not database_url or not import_root:
            raise DocumentBatchUnavailable
        try:
            return cls(
                BatchRepository(database_url),
                ImportSourceCatalog(Path(import_root)),
                BatchSecretSocketClient(Path(socket_path)),
            )
        except ValueError:
            raise DocumentBatchUnavailable from None

    def create(
        self,
        context: AuthContext,
        family_member_id: UUID,
        source_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        try:
            sources = tuple(self.catalog.resolve(source_id) for source_id in source_ids)
            batch = self.repository.create(
                household_space_id=context.household_space_id,
                created_by=context.user_id,
                family_member_id=family_member_id,
                sources=sources,
            )
        except ImportSourceNotFound:
            raise DocumentBatchNotFound from None
        except BatchRepositoryUnavailable:
            raise DocumentBatchUnavailable from None
        if batch is None:
            raise DocumentBatchNotFound
        return _projection(batch)

    def get_status(self, context: AuthContext, batch_id: UUID) -> dict[str, Any]:
        try:
            batch = self.repository.get(
                household_space_id=context.household_space_id,
                batch_id=batch_id,
            )
        except BatchRepositoryUnavailable:
            raise DocumentBatchUnavailable from None
        if batch is None:
            raise DocumentBatchNotFound
        return _projection(batch)

    def handoff_password(
        self,
        context: AuthContext,
        batch_id: UUID,
        password: str,
    ) -> dict[str, Any]:
        if context.needs_reauthentication:
            raise ReauthenticationRequired
        try:
            current = self.repository.get(
                household_space_id=context.household_space_id,
                batch_id=batch_id,
            )
            if current is None or not any(
                item.state == "password_required" for item in current.items
            ):
                raise DocumentBatchNotFound
            self.secret_client.send_once(
                SecretHandoff(
                    batch_id=batch_id,
                    handoff_id=uuid4(),
                    password=password,
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
            batch = self.repository.requeue_password_required(
                household_space_id=context.household_space_id,
                batch_id=batch_id,
            )
        except DocumentBatchNotFound:
            raise
        except BatchRepositoryUnavailable, SecretChannelError:
            raise DocumentBatchUnavailable from None
        if batch is None:
            raise DocumentBatchNotFound
        return _projection(batch)

    def cancel(self, context: AuthContext, batch_id: UUID) -> dict[str, Any]:
        try:
            batch = self.repository.cancel(
                household_space_id=context.household_space_id,
                batch_id=batch_id,
            )
        except BatchRepositoryUnavailable:
            raise DocumentBatchUnavailable from None
        if batch is None:
            raise DocumentBatchNotFound
        return _projection(batch)


__all__ = [
    "BatchService",
    "DocumentBatchNotFound",
    "DocumentBatchUnavailable",
]

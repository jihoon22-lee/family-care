"""Sanitized private-knowledge package errors."""

from __future__ import annotations

from enum import StrEnum


class PackageErrorCode(StrEnum):
    ROOT_NOT_ABSOLUTE = "ROOT_NOT_ABSOLUTE"
    ROOT_NOT_DIRECTORY = "ROOT_NOT_DIRECTORY"
    ROOT_INSIDE_REPOSITORY = "ROOT_INSIDE_REPOSITORY"
    ROOT_MODE_INVALID = "ROOT_MODE_INVALID"
    MANIFEST_MISSING = "MANIFEST_MISSING"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    DUPLICATE_MANIFEST_ENTRY = "DUPLICATE_MANIFEST_ENTRY"
    MISSING_REQUIRED_FILE = "MISSING_REQUIRED_FILE"
    UNSUPPORTED_FILE = "UNSUPPORTED_FILE"
    UNEXPECTED_FILE = "UNEXPECTED_FILE"
    FILE_NOT_REGULAR = "FILE_NOT_REGULAR"
    FILE_MODE_INVALID = "FILE_MODE_INVALID"
    FILE_SIZE_LIMIT = "FILE_SIZE_LIMIT"
    TOTAL_SIZE_LIMIT = "TOTAL_SIZE_LIMIT"
    FILE_SIZE_MISMATCH = "FILE_SIZE_MISMATCH"
    FILE_DIGEST_MISMATCH = "FILE_DIGEST_MISMATCH"
    FILE_CHANGED = "FILE_CHANGED"
    INVALID_JSON = "INVALID_JSON"
    INVALID_RECORD = "INVALID_RECORD"
    ROW_LIMIT = "ROW_LIMIT"
    NESTED_VALUE_LIMIT = "NESTED_VALUE_LIMIT"
    DUPLICATE_CANONICAL_KEY = "DUPLICATE_CANONICAL_KEY"
    BROKEN_REFERENCE = "BROKEN_REFERENCE"
    SOURCE_LINEAGE_MISMATCH = "SOURCE_LINEAGE_MISMATCH"
    EXECUTABLE_INPUT = "EXECUTABLE_INPUT"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"


class PrivateKnowledgePackageError(ValueError):
    """Fail-closed error that never contains a private source value."""

    def __init__(
        self,
        code: PackageErrorCode,
        *,
        file_role: str | None = None,
        row_number: int | None = None,
    ) -> None:
        self.code = code
        self.file_role = file_role
        self.row_number = row_number
        parts = [code.value]
        if file_role is not None:
            parts.append(file_role)
        if row_number is not None:
            parts.append(str(row_number))
        super().__init__(":".join(parts))

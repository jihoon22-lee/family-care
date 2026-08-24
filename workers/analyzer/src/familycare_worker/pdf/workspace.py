"""Private per-job workspace lifecycle with strict filesystem permissions."""

from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal, cast

from .errors import IntakeErrorCode, PdfIntakeError
from .limits import WORKSPACE_DIRECTORY_MODE, WORKSPACE_FILE_MODE


class WorkspaceCleanupError(PdfIntakeError):
    """Sanitized cleanup failure; it never contains a path or filename."""

    code = IntakeErrorCode.TEMP_CLEANUP_FAILED
    error_code = IntakeErrorCode.TEMP_CLEANUP_FAILED

    def __init__(self) -> None:
        super().__init__()


class WorkspaceStateError(RuntimeError):
    """Raised when a workspace is used after successful cleanup."""


def _safe_file_name(name: str) -> str:
    if not name or "\x00" in name or name in {".", ".."}:
        raise ValueError("invalid workspace file name")
    if Path(name).is_absolute() or "/" in name or "\\" in name:
        raise ValueError("invalid workspace file name")
    return name


@dataclass
class Workspace:
    """A random mode-0700 directory and exclusive mode-0600 output files."""

    path: Path
    _cleaned: bool = field(default=False, init=False)
    cleanup_error: WorkspaceCleanupError | None = field(default=None, init=False)

    def create_file(self, name: str, mode: str = "w+b") -> BinaryIO:
        """Create one exclusive output file and return its open binary handle."""

        if self._cleaned:
            raise WorkspaceStateError("workspace is closed")
        safe_name = _safe_file_name(name)
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path / safe_name, flags, WORKSPACE_FILE_MODE)
        try:
            os.fchmod(fd, WORKSPACE_FILE_MODE)
            return cast(BinaryIO, os.fdopen(fd, mode))
        except BaseException:
            os.close(fd)
            raise

    def open_output(self, name: str, mode: str = "w+b") -> BinaryIO:
        """Alias for callers that describe generated artifacts as outputs."""

        return self.create_file(name, mode)

    def create_output_file(self, name: str, mode: str = "w+b") -> BinaryIO:
        """Explicit alias for the output-file contract."""

        return self.create_file(name, mode)

    def close_and_cleanup(self, *, raise_on_failure: bool = True) -> bool:
        """Remove the workspace, surfacing cleanup failure without path details."""

        if self._cleaned:
            return True
        try:
            shutil.rmtree(self.path)
        except FileNotFoundError:
            self._cleaned = True
            self.cleanup_error = None
            return True
        except OSError:
            self.cleanup_error = WorkspaceCleanupError()
            if raise_on_failure:
                raise self.cleanup_error from None
            return False
        self._cleaned = True
        self.cleanup_error = None
        return True

    def cleanup(self, *, raise_on_failure: bool = True) -> bool:
        """Alias used by context-manager and runner callers."""

        return self.close_and_cleanup(raise_on_failure=raise_on_failure)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> Literal[False]:
        del exc_type, exc_value, traceback
        self.close_and_cleanup()
        return False


def create_workspace(root: Path) -> Workspace:
    """Create a random neutral job directory below an existing absolute root."""

    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("workspace root must be an absolute directory")

    for _ in range(16):
        path = root / f"job-{secrets.token_hex(16)}"
        try:
            os.mkdir(path, WORKSPACE_DIRECTORY_MODE)
        except FileExistsError:
            continue
        os.chmod(path, WORKSPACE_DIRECTORY_MODE)
        return Workspace(path)
    raise RuntimeError("workspace allocation failed")


__all__ = [
    "Workspace",
    "WorkspaceCleanupError",
    "WorkspaceStateError",
    "create_workspace",
]

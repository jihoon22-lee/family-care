"""Fixed resource and workspace limits for synthetic PDF processing."""

from __future__ import annotations

from typing import Final

try:
    import resource
except ImportError:  # pragma: no cover - Windows is an explicitly unverified boundary.
    resource = None  # type: ignore[assignment]


MAX_INPUT_BYTES: Final = 128 * 1024 * 1024
MAX_PDF_PAGES: Final = 500
PARENT_WALL_TIMEOUT_SECONDS: Final = 120
CHILD_CPU_LIMIT_SECONDS: Final = 90
CHILD_ADDRESS_SPACE_BYTES: Final = 1536 * 1024 * 1024
CHILD_FILE_SIZE_BYTES: Final = 64 * 1024 * 1024
MAX_OUTPUT_BYTES: Final = 64 * 1024 * 1024
MAX_SETTINGS_BYTES: Final = 64 * 1024
CHILD_OPEN_DESCRIPTORS: Final = 64
WORKSPACE_DIRECTORY_MODE: Final = 0o700
WORKSPACE_FILE_MODE: Final = 0o600


class ResourceLimitApplicationError(RuntimeError):
    """Raised when the child cannot apply its fixed resource limits."""

    def __init__(self) -> None:
        super().__init__("RESOURCE_LIMIT_EXCEEDED")


def apply_resource_limits() -> None:
    """Apply the child limits before any parser invocation."""

    if resource is None:  # pragma: no cover - Windows is explicitly unverified.
        raise ResourceLimitApplicationError

    requested = (
        (resource.RLIMIT_CPU, CHILD_CPU_LIMIT_SECONDS),
        (resource.RLIMIT_AS, CHILD_ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_FSIZE, CHILD_FILE_SIZE_BYTES),
        (resource.RLIMIT_NOFILE, CHILD_OPEN_DESCRIPTORS),
    )
    try:
        for limit, value in requested:
            resource.setrlimit(limit, (value, value))
    except (OSError, ValueError) as exc:
        del exc
        raise ResourceLimitApplicationError from None


__all__ = [
    "CHILD_ADDRESS_SPACE_BYTES",
    "CHILD_CPU_LIMIT_SECONDS",
    "CHILD_FILE_SIZE_BYTES",
    "CHILD_OPEN_DESCRIPTORS",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_SETTINGS_BYTES",
    "MAX_PDF_PAGES",
    "PARENT_WALL_TIMEOUT_SECONDS",
    "WORKSPACE_DIRECTORY_MODE",
    "WORKSPACE_FILE_MODE",
    "ResourceLimitApplicationError",
    "apply_resource_limits",
    "resource",
]

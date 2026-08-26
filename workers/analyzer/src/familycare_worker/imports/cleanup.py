"""Sanitized workspace cleanup used by private batch processing."""

from __future__ import annotations

from typing import Protocol


class CleanableWorkspace(Protocol):
    def close_and_cleanup(self, *, raise_on_failure: bool = True) -> bool: ...


def cleanup_workspace(workspace: CleanableWorkspace) -> bool:
    """Return one safe outcome without exposing a workspace path."""

    try:
        return workspace.close_and_cleanup(raise_on_failure=False)
    except Exception:
        return False


__all__ = ["CleanableWorkspace", "cleanup_workspace"]

"""Optimistic-concurrency input validation."""

from __future__ import annotations


class InvalidVersion(ValueError):
    """Raised before a non-positive version reaches persistence."""


def require_expected_version(expected_version: int) -> int:
    """Accept positive integers while rejecting bool's integer subtype."""

    if (
        not isinstance(expected_version, int)
        or isinstance(expected_version, bool)
        or expected_version < 1
    ):
        raise InvalidVersion
    return expected_version


__all__ = ["InvalidVersion", "require_expected_version"]

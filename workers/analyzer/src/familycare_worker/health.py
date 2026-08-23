"""Health contract for the analyzer worker."""

from typing import Literal, TypedDict

from familycare_worker import __version__


class HealthPayload(TypedDict):
    """Stable process health payload."""

    service: Literal["analyzer"]
    status: Literal["ok"]
    version: str


def health_payload() -> HealthPayload:
    """Report that the analyzer package is importable and runnable."""

    return {
        "service": "analyzer",
        "status": "ok",
        "version": __version__,
    }

"""Process health contracts for the FamilyCare API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from familycare_api import __version__


class HealthResponse(BaseModel):
    """Stable public health response."""

    model_config = ConfigDict(frozen=True)

    service: Literal["api"] = "api"
    status: Literal["ok", "ready"]
    version: str = __version__


def liveness() -> HealthResponse:
    """Report that the API process can serve requests."""

    return HealthResponse(status="ok")


def readiness() -> HealthResponse:
    """Report Foundation process readiness before database probing is added."""

    return HealthResponse(status="ready")

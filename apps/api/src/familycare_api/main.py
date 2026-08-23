"""FastAPI application factory."""

from fastapi import FastAPI

from familycare_api.health import HealthResponse, liveness, readiness


def create_app() -> FastAPI:
    """Create the Foundation API without external service dependencies."""

    app = FastAPI(
        title="FamilyCare API",
        version="0.0.0",
        description="Evidence-first family insurance guidance API",
    )
    app.add_api_route(
        "/health/live",
        liveness,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["health"],
    )
    app.add_api_route(
        "/health/ready",
        readiness,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["health"],
    )
    return app


app = create_app()

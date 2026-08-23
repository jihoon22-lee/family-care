"""FastAPI application factory."""

from fastapi import FastAPI, Response, status

from familycare_api.health import (
    HealthResponse,
    ReadinessProbe,
    database_is_ready,
    liveness,
    readiness,
)


def create_app(readiness_probe: ReadinessProbe | None = None) -> FastAPI:
    """Create the Foundation API with an injectable database probe."""

    probe = readiness_probe or database_is_ready
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

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    def readiness_endpoint(response: Response) -> HealthResponse:
        health = readiness(probe)
        if health.status == "unavailable":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return health

    return app


app = create_app()

"""FastAPI application factory."""

import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status

from familycare_api import __version__
from familycare_api.claims.router import medical_event_claim_router
from familycare_api.claims.router import router as claim_router
from familycare_api.clauses.router import router as clause_search_router
from familycare_api.decisions.evidence_router import router as evidence_router
from familycare_api.decisions.router import (
    router as coverage_decision_router,
)
from familycare_api.decisions.router import (
    structuring_job_router,
)
from familycare_api.documents.batch_router import router as document_batch_router
from familycare_api.documents.router import router as document_analysis_router
from familycare_api.errors import install_error_handlers
from familycare_api.health import (
    HealthResponse,
    ReadinessProbe,
    database_is_ready,
    liveness,
    readiness,
)
from familycare_api.identity.router import router as identity_router
from familycare_api.policies.candidate_router import router as policy_candidate_router
from familycare_api.policies.router import router as policy_ledger_router


def _synthetic_ingestion_enabled() -> bool:
    return (
        os.getenv("FAMILYCARE_ENV") == "development"
        and os.getenv("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION") == "true"
    )


def create_app(
    *,
    readiness_probe: ReadinessProbe | None = None,
    enable_synthetic_ingestion: bool | None = None,
) -> FastAPI:
    """Create the Foundation API with an injectable database probe."""

    probe = readiness_probe or database_is_ready
    app = FastAPI(
        title="FamilyCare API",
        version=__version__,
        description="Evidence-first family insurance guidance API",
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def no_store_api_responses(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        raw_session = getattr(request.state, "session_cookie_refresh", None)
        session_cookie_headers = response.headers.getlist("set-cookie")
        if raw_session and not any(
            header.startswith("familycare_session=") for header in session_cookie_headers
        ):
            response.set_cookie(
                "familycare_session",
                raw_session,
                max_age=7 * 24 * 60 * 60,
                secure=True,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return response

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

    enabled = (
        _synthetic_ingestion_enabled()
        if enable_synthetic_ingestion is None
        else enable_synthetic_ingestion
    )
    if enabled:
        app.include_router(document_analysis_router)
    app.include_router(identity_router)
    app.include_router(document_batch_router)
    app.include_router(policy_ledger_router)
    app.include_router(policy_candidate_router)
    app.include_router(clause_search_router)
    app.include_router(coverage_decision_router)
    app.include_router(structuring_job_router)
    app.include_router(evidence_router)
    app.include_router(medical_event_claim_router)
    app.include_router(claim_router)

    return app


app = create_app()

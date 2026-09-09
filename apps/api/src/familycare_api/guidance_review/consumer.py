"""Consume only already-requested review proposals; no AI or ingestion scheduling."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI

from familycare_api.guidance_review.projector import GuidanceReviewProjector
from familycare_api.policies.enrollment_consumer import enrollment_lifespan

LOGGER = logging.getLogger(__name__)


async def _consume(projector: GuidanceReviewProjector, stop: Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(projector.project_pending, limit=2, stop_requested=stop.is_set)
        except Exception:
            LOGGER.warning("guidance_review_projection_unavailable")
        await asyncio.to_thread(stop.wait, 1)


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with enrollment_lifespan(app):
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            yield
            return
        stop = Event()
        task = asyncio.create_task(_consume(GuidanceReviewProjector(database_url), stop))
        try:
            yield
        finally:
            stop.set()
            await task

"""Bounded local projection at API startup, independent of HTTP reads."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI

from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

LOGGER = logging.getLogger(__name__)


async def _consume(projector: RangeEnrollmentProjector, stop: Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(projector.project_pending, limit=25, stop_requested=stop.is_set)
        except Exception:
            # DB/provider payloads and credentials must never appear in exception logs.
            LOGGER.warning("range_enrollment_projection_unavailable")
        await asyncio.to_thread(stop.wait, 2)


@asynccontextmanager
async def enrollment_lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if os.getenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT") != "true" or not database_url:
        yield
        return
    stop = Event()
    task = asyncio.create_task(_consume(RangeEnrollmentProjector(database_url), stop))
    try:
        yield
    finally:
        stop.set()
        await task

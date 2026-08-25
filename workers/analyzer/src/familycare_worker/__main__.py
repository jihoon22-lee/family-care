"""Foundation analyzer process entrypoint."""

import json
import logging
import math
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from threading import Event
from types import FrameType
from typing import Protocol

from familycare_worker.ai.event_structurer import (
    EVENT_STRUCTURER_SCHEMA_NAME,
    event_structurer_schema,
)
from familycare_worker.ai.provider import (
    DEFAULT_STRUCTURER_MODEL,
    OpenAiResponsesAdapter,
)
from familycare_worker.event_jobs import EventStructuringJobQueue
from familycare_worker.health import DatabaseProbe, database_is_ready, health_payload
from familycare_worker.jobs import JobQueue
from familycare_worker.repository import ExtractionRepository
from familycare_worker.runner import AnalysisJobRunner, EventStructuringJobRunner

LOGGER = logging.getLogger("familycare.worker")


class JobRunner(Protocol):
    def run_once(self, worker_id: str) -> bool: ...


class FairJobRunner:
    """Alternate queue priority while processing at most one job per iteration."""

    def __init__(self, *, events: JobRunner, documents: JobRunner) -> None:
        self.events = events
        self.documents = documents
        self._events_first = True

    def run_once(self, worker_id: str) -> bool:
        first, second = (
            (self.events, self.documents) if self._events_first else (self.documents, self.events)
        )
        self._events_first = not self._events_first
        if first.run_once(worker_id):
            return True
        return second.run_once(worker_id)


def run_idle(stop_event: Event, *, interval_seconds: float = 30.0) -> int:
    """Remain idle until shutdown without reading documents or polling services."""

    while not stop_event.wait(interval_seconds):
        continue
    return 0


def run_worker_loop(
    stop_event: Event,
    runner: JobRunner,
    *,
    worker_id: str,
    poll_interval_seconds: float = 1.0,
) -> int:
    """Run one claimed job at a time until a bounded shutdown is requested."""

    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds < 0
    ):
        raise ValueError("poll interval must be non-negative")
    while not stop_event.is_set():
        try:
            processed = runner.run_once(worker_id)
        except Exception:
            LOGGER.error("worker_iteration_failed")
            processed = False
        if not processed and stop_event.wait(poll_interval_seconds):
            break
    return 0


def _runner_from_environment(stop_event: Event) -> JobRunner | None:
    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        return None
    provider = OpenAiResponsesAdapter({EVENT_STRUCTURER_SCHEMA_NAME: event_structurer_schema()})
    event_runner = EventStructuringJobRunner(
        queue=EventStructuringJobQueue(database_url),
        provider=provider,
        structurer_model=os.getenv(
            "FAMILYCARE_AI_STRUCTURER_MODEL",
            DEFAULT_STRUCTURER_MODEL,
        ),
    )
    document_root = os.getenv("FAMILYCARE_DOCUMENT_ROOT")
    work_root = os.getenv("FAMILYCARE_WORK_ROOT")
    if not document_root or not work_root:
        if bool(document_root) != bool(work_root):
            LOGGER.error("document_runner_configuration_incomplete")
        return event_runner
    queue = JobQueue(database_url)
    repository = ExtractionRepository(database_url)
    document_runner = AnalysisJobRunner(
        queue,
        repository,
        document_root=Path(document_root),
        work_root=Path(work_root),
        stop_requested=stop_event.is_set,
    )
    return FairJobRunner(events=event_runner, documents=document_runner)


def install_signal_handlers(stop_event: Event) -> None:
    """Translate container stop signals into a clean idle-loop shutdown."""

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main(
    argv: Sequence[str] | None = None,
    *,
    database_probe: DatabaseProbe | None = None,
    stop_event: Event | None = None,
    job_runner: JobRunner | None = None,
    worker_id: str | None = None,
) -> int:
    """Print process health or perform a database-backed health check."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        event = stop_event or Event()
        if stop_event is None:
            install_signal_handlers(event)
        print(json.dumps(health_payload(), sort_keys=True), flush=True)
        runner = job_runner or _runner_from_environment(event)
        if runner is not None:
            identity = worker_id or f"worker-{os.getpid()}"
            return run_worker_loop(event, runner, worker_id=identity)
        return run_idle(event)
    if arguments == ["--health"]:
        payload = health_payload(database_probe or database_is_ready)
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["status"] == "ready" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

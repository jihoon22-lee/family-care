"""Foundation analyzer process entrypoint."""

import json
import signal
import sys
from collections.abc import Sequence
from threading import Event
from types import FrameType

from familycare_worker.health import DatabaseProbe, database_is_ready, health_payload


def run_idle(stop_event: Event, *, interval_seconds: float = 30.0) -> int:
    """Remain idle until shutdown without reading documents or polling services."""

    while not stop_event.wait(interval_seconds):
        continue
    return 0


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
) -> int:
    """Print process health or perform a database-backed health check."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        event = stop_event or Event()
        if stop_event is None:
            install_signal_handlers(event)
        print(json.dumps(health_payload(), sort_keys=True), flush=True)
        return run_idle(event)
    if arguments == ["--health"]:
        payload = health_payload(database_probe or database_is_ready)
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["status"] == "ready" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Foundation analyzer process entrypoint."""

import json
from collections.abc import Sequence

from familycare_worker.health import DatabaseProbe, database_is_ready, health_payload


def main(
    argv: Sequence[str] | None = None,
    *,
    database_probe: DatabaseProbe | None = None,
) -> int:
    """Print process health or perform a database-backed health check."""

    arguments = list(argv or [])
    if not arguments:
        print(json.dumps(health_payload(), sort_keys=True))
        return 0
    if arguments == ["--health"]:
        payload = health_payload(database_probe or database_is_ready)
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["status"] == "ready" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

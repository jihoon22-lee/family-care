"""Foundation analyzer process entrypoint."""

import json
from collections.abc import Sequence

from familycare_worker.health import health_payload


def main(argv: Sequence[str] | None = None) -> int:
    """Print process health until document polling is introduced."""

    del argv
    print(json.dumps(health_payload(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

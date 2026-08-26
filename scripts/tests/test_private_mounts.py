from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from scripts.private_runtime_policy import validate_runtime_config

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra/compose/compose.yaml"
API_DOCKERFILE = ROOT / "infra/containers/api.Dockerfile"
WORKER_DOCKERFILE = ROOT / "infra/containers/worker.Dockerfile"

IMPORT_TARGET = "/var/lib/familycare/import"
ARCHIVE_TARGET = "/var/lib/familycare/archive"
WORK_TARGET = "/var/lib/familycare/work"
SOCKET_TARGET = "/run/familycare"
KEY_TARGET = "/run/secrets/familycare_archive_master_key"


def _compose() -> dict[str, Any]:
    value = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _mounts(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        mount["target"]: mount for mount in service.get("volumes", []) if isinstance(mount, dict)
    }


def test_import_is_the_only_private_source_shared_by_api_and_worker() -> None:
    services = _compose()["services"]
    api_mounts = _mounts(services["api"])
    worker_mounts = _mounts(services["worker"])

    for mounts in (api_mounts, worker_mounts):
        assert mounts[IMPORT_TARGET] == {
            "type": "bind",
            "source": "${FAMILYCARE_IMPORT_ROOT:?set FAMILYCARE_IMPORT_ROOT outside Git}",
            "target": IMPORT_TARGET,
            "read_only": True,
        }

    assert set(api_mounts) == {IMPORT_TARGET, SOCKET_TARGET}
    assert set(worker_mounts) == {
        IMPORT_TARGET,
        ARCHIVE_TARGET,
        WORK_TARGET,
        SOCKET_TARGET,
        KEY_TARGET,
    }


def test_archive_work_key_and_ai_credentials_are_worker_only() -> None:
    services = _compose()["services"]
    worker = services["worker"]
    worker_mounts = _mounts(worker)

    assert worker_mounts[ARCHIVE_TARGET] == {
        "type": "volume",
        "source": "familycare-archive-data",
        "target": ARCHIVE_TARGET,
    }
    assert worker_mounts[WORK_TARGET] == {
        "type": "volume",
        "source": "familycare-worker-work",
        "target": WORK_TARGET,
    }
    assert worker_mounts[KEY_TARGET] == {
        "type": "bind",
        "source": (
            "${FAMILYCARE_ARCHIVE_MASTER_KEY_FILE:"
            "?set FAMILYCARE_ARCHIVE_MASTER_KEY_FILE outside Git}"
        ),
        "target": KEY_TARGET,
        "read_only": True,
    }
    assert worker["environment"]["OPENAI_API_KEY"] == (
        "${OPENAI_API_KEY:?set OPENAI_API_KEY outside Git}"
    )
    assert worker["environment"]["FAMILYCARE_AI_STRUCTURER_MODEL"] == (
        "${FAMILYCARE_AI_STRUCTURER_MODEL:-gpt-5.6-luna}"
    )
    assert worker["environment"]["FAMILYCARE_AI_VERIFIER_MODEL"] == (
        "${FAMILYCARE_AI_VERIFIER_MODEL:-gpt-5.6-terra}"
    )

    for name in ("api", "db", "web"):
        environment = services[name].get("environment", {})
        assert "OPENAI_API_KEY" not in environment
        assert "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE" not in environment


def test_secret_socket_uses_one_dedicated_api_worker_group() -> None:
    services = _compose()["services"]

    for name in ("api", "worker"):
        assert services[name]["group_add"] == ["10003"]
        assert _mounts(services[name])[SOCKET_TARGET] == {
            "type": "volume",
            "source": "familycare-secret-socket",
            "target": SOCKET_TARGET,
        }
    for name in ("db", "web"):
        assert SOCKET_TARGET not in _mounts(services[name])

    for path in (API_DOCKERFILE, WORKER_DOCKERFILE):
        content = path.read_text(encoding="utf-8")
        assert "--gid 10003 familycare-runtime" in content
        assert "-g 10003 -m 2770 /run/familycare" in content


def test_images_keep_private_material_out_and_preserve_non_root_users() -> None:
    api = API_DOCKERFILE.read_text(encoding="utf-8")
    worker = WORKER_DOCKERFILE.read_text(encoding="utf-8")

    assert "USER 10001:10001" in api
    assert "USER 10002:10002" in worker
    assert "-o 10002 -g 10002 -m 0700 /var/lib/familycare/archive" in worker
    assert "-o 10002 -g 10002 -m 0700 /var/lib/familycare/work" in worker
    for content in (api, worker):
        copy_lines = [line for line in content.splitlines() if line.startswith("COPY ")]
        assert all("master_key" not in line.casefold() for line in copy_lines)
        assert all(".env" not in line.casefold() for line in copy_lines)


def test_repository_policy_accepts_the_concrete_compose_contract() -> None:
    environment = {
        "FAMILYCARE_IMPORT_ROOT": "/synthetic/import",
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE": "/synthetic/master-key",
        "OPENAI_API_KEY": "synthetic-only",
    }

    assert validate_runtime_config(_compose(), environment) == []

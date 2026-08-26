from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.private_runtime_policy import (
    validate_private_roots,
    validate_runtime_config,
    validate_tailscale_inspection_command,
)


def _mount(source: str, target: str, *, read_only: bool = False) -> dict[str, object]:
    return {
        "type": "bind" if source.startswith("${") else "volume",
        "source": source,
        "target": target,
        "read_only": read_only,
    }


def _compose() -> dict[str, object]:
    return {
        "services": {
            "db": {"environment": {}},
            "api": {
                "environment": {},
                "volumes": [
                    _mount(
                        "${FAMILYCARE_IMPORT_ROOT}", "/var/lib/familycare/import", read_only=True
                    ),
                    _mount("familycare-secret-socket", "/run/familycare"),
                ],
            },
            "worker": {
                "environment": {
                    "OPENAI_API_KEY": "${OPENAI_API_KEY}",
                    "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE": (
                        "/run/secrets/familycare_archive_master_key"
                    ),
                },
                "volumes": [
                    _mount(
                        "${FAMILYCARE_IMPORT_ROOT}", "/var/lib/familycare/import", read_only=True
                    ),
                    _mount(
                        "${FAMILYCARE_ARCHIVE_MASTER_KEY_FILE}",
                        "/run/secrets/familycare_archive_master_key",
                        read_only=True,
                    ),
                    _mount("familycare-archive-data", "/var/lib/familycare/archive"),
                    _mount("familycare-worker-work", "/var/lib/familycare/work"),
                    _mount("familycare-secret-socket", "/run/familycare"),
                ],
            },
            "web": {
                "environment": {},
                "ports": ["127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}:8080"],
            },
        },
        "volumes": {
            "familycare-postgres-data": {},
            "familycare-archive-data": {},
            "familycare-worker-work": {},
            "familycare-secret-socket": {},
        },
    }


def _environment() -> dict[str, str]:
    return {
        "FAMILYCARE_IMPORT_ROOT": "/synthetic/import",
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE": "/synthetic/key",
        "OPENAI_API_KEY": "synthetic-secret-that-must-not-be-rendered",
    }


def test_valid_private_runtime_mapping_has_no_policy_errors() -> None:
    assert validate_runtime_config(_compose(), _environment()) == []


@pytest.mark.parametrize("service", ["api", "db", "worker"])
def test_only_web_may_publish_a_host_port(service: str) -> None:
    compose = _compose()
    compose["services"][service]["ports"] = ["127.0.0.1:9999:9999"]  # type: ignore[index]

    assert "host-port" in validate_runtime_config(compose, _environment())


@pytest.mark.parametrize("service", ["api", "web"])
def test_openai_key_is_worker_only(service: str) -> None:
    compose = _compose()
    compose["services"][service]["environment"]["OPENAI_API_KEY"] = "forbidden"  # type: ignore[index]

    assert "worker-secret-scope" in validate_runtime_config(compose, _environment())


def test_service_set_is_exactly_four() -> None:
    compose = _compose()
    compose["services"]["extra"] = {}  # type: ignore[index]

    assert "service-set" in validate_runtime_config(compose, _environment())


def test_import_mounts_are_read_only() -> None:
    compose = _compose()
    compose["services"]["api"]["volumes"][0]["read_only"] = False  # type: ignore[index]

    assert "read-only-mount" in validate_runtime_config(compose, _environment())


def test_worker_requires_a_read_only_master_key_mount() -> None:
    compose = _compose()
    compose["services"]["worker"]["volumes"].pop(1)  # type: ignore[index]

    assert "worker-key-mount" in validate_runtime_config(compose, _environment())


def test_errors_never_render_environment_values() -> None:
    compose = _compose()
    compose["services"]["api"]["environment"]["OPENAI_API_KEY"] = "forbidden"  # type: ignore[index]

    rendered = " ".join(validate_runtime_config(compose, _environment()))

    assert _environment()["OPENAI_API_KEY"] not in rendered


def test_private_roots_are_absolute_distinct_and_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    import_root = tmp_path / "import"
    archive_root = tmp_path / "archive"
    work_root = tmp_path / "work"

    validate_private_roots(repository, import_root, archive_root, work_root)

    with pytest.raises(ValueError, match="root-boundary"):
        validate_private_roots(repository, repository / "private", archive_root, work_root)
    with pytest.raises(ValueError, match="root-boundary"):
        validate_private_roots(repository, Path("relative"), archive_root, work_root)
    with pytest.raises(ValueError, match="root-boundary"):
        validate_private_roots(repository, import_root, import_root / "nested", work_root)


@pytest.mark.parametrize(
    "argv",
    [
        ["tailscale", "status", "--json", "--peers=false"],
        ["tailscale", "ip", "-1"],
        ["tailscale", "serve", "status", "--json"],
    ],
)
def test_read_only_tailscale_inspection_commands_are_allowed(argv: list[str]) -> None:
    validate_tailscale_inspection_command(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["tailscale", "serve", "--bg", "http://127.0.0.1:8080"],
        ["tailscale", "funnel", "8080"],
        ["tailscale", "up"],
        ["tailscale", "serve", "status"],
        ["tailscale", "status", "--json"],
    ],
)
def test_tailscale_mutation_and_unknown_forms_are_rejected(argv: list[str]) -> None:
    with pytest.raises(ValueError, match="tailscale-command-not-read-only"):
        validate_tailscale_inspection_command(argv)


def test_policy_input_is_not_mutated() -> None:
    compose = _compose()
    before = deepcopy(compose)

    validate_runtime_config(compose, _environment())

    assert compose == before

#!/usr/bin/env python3
"""Non-sensitive policy checks for the private local FamilyCare runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimePolicy:
    service_names: frozenset[str]
    host_published_services: frozenset[str]
    worker_secret_names: frozenset[str]
    forbidden_host_bindings: frozenset[str]


POLICY = RuntimePolicy(
    service_names=frozenset({"api", "db", "web", "worker"}),
    host_published_services=frozenset({"web"}),
    worker_secret_names=frozenset({"FAMILYCARE_ARCHIVE_MASTER_KEY_FILE", "OPENAI_API_KEY"}),
    forbidden_host_bindings=frozenset({"api", "db", "worker"}),
)

_IMPORT_TARGET = "/var/lib/familycare/import"
_ARCHIVE_TARGET = "/var/lib/familycare/archive"
_WORK_TARGET = "/var/lib/familycare/work"
_SOCKET_TARGET = "/run/familycare"
_KEY_TARGET = "/run/secrets/familycare_archive_master_key"
_EXPECTED_VOLUMES = frozenset(
    {
        "familycare-archive-data",
        "familycare-postgres-data",
        "familycare-secret-socket",
        "familycare-worker-work",
    }
)
_READ_ONLY_TAILSCALE_COMMANDS = frozenset(
    {
        ("tailscale", "ip", "-1"),
        ("tailscale", "serve", "status"),
        ("tailscale", "status", "--json"),
    }
)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _environment_names(service: Mapping[str, Any]) -> frozenset[str]:
    environment = service.get("environment")
    if isinstance(environment, Mapping):
        return frozenset(str(name) for name in environment)
    if isinstance(environment, Sequence) and not isinstance(environment, str | bytes):
        return frozenset(str(entry).split("=", 1)[0] for entry in environment)
    return frozenset()


def _mounts(service: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    volumes = service.get("volumes")
    if not isinstance(volumes, Sequence) or isinstance(volumes, str | bytes):
        return ()
    return tuple(value for value in volumes if isinstance(value, Mapping))


def _mount_for(service: Mapping[str, Any], target: str) -> Mapping[str, Any] | None:
    return next((mount for mount in _mounts(service) if mount.get("target") == target), None)


def _add(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def validate_runtime_config(
    compose: Mapping[str, Any],
    environment: Mapping[str, str],
) -> list[str]:
    """Return stable policy categories without rendering configuration values."""

    errors: list[str] = []
    services = _mapping(compose.get("services"))
    if frozenset(str(name) for name in services) != POLICY.service_names:
        _add(errors, "service-set")

    service_maps = {name: _mapping(services.get(name)) for name in POLICY.service_names}
    published = frozenset(
        name
        for name, service in service_maps.items()
        if isinstance(service.get("ports"), Sequence)
        and not isinstance(service.get("ports"), str | bytes)
        and bool(service.get("ports"))
    )
    if published != POLICY.host_published_services or any(
        "ports" in service_maps[name] for name in POLICY.forbidden_host_bindings
    ):
        _add(errors, "host-port")

    for name, service in service_maps.items():
        secret_names = _environment_names(service) & POLICY.worker_secret_names
        if name == "worker":
            if secret_names != POLICY.worker_secret_names:
                _add(errors, "worker-secret-scope")
        elif secret_names:
            _add(errors, "worker-secret-scope")

    for name in ("api", "worker"):
        import_mount = _mount_for(service_maps[name], _IMPORT_TARGET)
        if import_mount is None or import_mount.get("read_only") is not True:
            _add(errors, "read-only-mount")

    worker_key = _mount_for(service_maps["worker"], _KEY_TARGET)
    if worker_key is None or worker_key.get("read_only") is not True:
        _add(errors, "worker-key-mount")

    worker_targets = {mount.get("target") for mount in _mounts(service_maps["worker"])}
    if not {_ARCHIVE_TARGET, _WORK_TARGET, _SOCKET_TARGET}.issubset(worker_targets):
        _add(errors, "worker-mount-set")
    api_targets = {mount.get("target") for mount in _mounts(service_maps["api"])}
    if _SOCKET_TARGET not in api_targets:
        _add(errors, "socket-mount")
    if {_ARCHIVE_TARGET, _WORK_TARGET, _KEY_TARGET} & api_targets:
        _add(errors, "worker-mount-scope")

    volumes = _mapping(compose.get("volumes"))
    if frozenset(str(name) for name in volumes) != _EXPECTED_VOLUMES:
        _add(errors, "volume-set")

    required_external = {
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
        "FAMILYCARE_IMPORT_ROOT",
        "OPENAI_API_KEY",
    }
    if not required_external.issubset(environment):
        _add(errors, "runtime-environment")
    return errors


def validate_tailscale_inspection_command(argv: Sequence[str]) -> None:
    """Permit only the three exact read-only Tailscale inspection forms."""

    if tuple(argv) not in _READ_ONLY_TAILSCALE_COMMANDS:
        raise ValueError("tailscale-command-not-read-only")


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or child.is_relative_to(parent)


def validate_private_roots(
    repository_root: Path,
    import_root: Path,
    archive_root: Path,
    worker_work_root: Path,
) -> None:
    """Require absolute, non-overlapping roots outside the repository tree."""

    candidates = tuple(Path(value) for value in (import_root, archive_root, worker_work_root))
    repository = Path(repository_root)
    if not repository.is_absolute() or any(not candidate.is_absolute() for candidate in candidates):
        raise ValueError("root-boundary")
    repository = repository.resolve(strict=False)
    resolved = tuple(candidate.resolve(strict=False) for candidate in candidates)
    if any(
        _contains(repository, candidate) or _contains(candidate, repository)
        for candidate in resolved
    ):
        raise ValueError("root-boundary")
    for index, candidate in enumerate(resolved):
        if any(
            _contains(candidate, other) or _contains(other, candidate)
            for other in resolved[index + 1 :]
        ):
            raise ValueError("root-boundary")


__all__ = [
    "POLICY",
    "RuntimePolicy",
    "validate_private_roots",
    "validate_runtime_config",
    "validate_tailscale_inspection_command",
]

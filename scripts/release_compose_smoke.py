#!/usr/bin/env python3
"""Validate a digest-pinned, gateway-only Compose release configuration.

The module deliberately stops at Compose configuration validation.  It never
starts, stops, recreates, or removes a container.  Temporary files contain
synthetic values only and are removed by :func:`temporary_smoke_files` on
every exit path.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeGuard

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
COMPOSE_PATH: Final[Path] = ROOT / "infra/compose/compose.yaml"
COMPONENTS: Final[tuple[str, str, str]] = ("web", "api", "worker")
IMAGE_COMPONENTS: Final[tuple[str, str, str]] = COMPONENTS
SERVICES: Final[frozenset[str]] = frozenset({"db", *COMPONENTS})
_IMAGE_SEGMENT = r"[a-z0-9](?:[a-z0-9._-]{0,98}[a-z0-9])?"
_DIGEST_REFERENCE: Final[re.Pattern[str]] = re.compile(
    rf"^ghcr\.io/{_IMAGE_SEGMENT}/{_IMAGE_SEGMENT}@sha256:[0-9a-f]{{64}}$"
)
_PROJECT_NAME: Final[re.Pattern[str]] = re.compile(r"^familycare-release-smoke-[0-9a-f]{12}$")
_LOOPBACK_NAMES: Final[frozenset[str]] = frozenset({"localhost", "ip6-localhost"})
_SYNTHETIC_KEY: Final[bytes] = b"0" * 32


@dataclass(frozen=True, slots=True)
class SmokeFinding:
    """A stable category with no values, paths, command output, or body text."""

    code: str


# The release plan uses the more general name for findings.  Keep this alias
# available so callers can consume the smoke checker without an adapter.
type Finding = SmokeFinding
type ReleaseFinding = SmokeFinding


class SmokeValidationError(ValueError):
    """Validation failure whose string form contains finding codes only."""

    def __init__(self, findings: Sequence[SmokeFinding]) -> None:
        normalized = _unique_findings(findings)
        if not normalized:
            normalized = (SmokeFinding("validation"),)
        self.findings: tuple[SmokeFinding, ...] = normalized
        super().__init__(", ".join(finding.code for finding in normalized))


@dataclass(frozen=True, slots=True)
class TemporarySmokeFiles:
    """Paths and safe read-only command construction for one smoke attempt."""

    directory: Path
    environment_path: Path
    key_path: Path
    override_path: Path
    project_name: str
    image_references: tuple[str, str, str]

    def config_check_command(
        self,
        compose_path: Path = COMPOSE_PATH,
    ) -> tuple[str, ...]:
        """Build an argv-only, read-only ``docker compose config`` command."""

        return (
            "docker",
            "compose",
            "-p",
            self.project_name,
            "--env-file",
            str(self.environment_path),
            "-f",
            str(compose_path),
            "-f",
            str(self.override_path),
            "config",
            "--format",
            "json",
        )


type ComposeRunner = Callable[..., subprocess.CompletedProcess[str]]


def _unique_findings(findings: Sequence[SmokeFinding]) -> tuple[SmokeFinding, ...]:
    """Deduplicate findings while retaining deterministic first-seen order."""

    result: list[SmokeFinding] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.code not in seen:
            result.append(SmokeFinding(finding.code))
            seen.add(finding.code)
    return tuple(result)


def _add(findings: list[SmokeFinding], code: str) -> None:
    if code not in {finding.code for finding in findings}:
        findings.append(SmokeFinding(code))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_digest_reference(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or _DIGEST_REFERENCE.fullmatch(value) is None:
        return False
    # A registry port may contain a colon, but a tag in the final image name
    # would make the accepted input mutable-looking even when a digest follows.
    return ":" not in value.split("@", 1)[0].rsplit("/", 1)[-1]


def _reference_image_name(value: str) -> str:
    image_name = value.split("@", 1)[0]
    return image_name.rsplit("/", 1)[-1]


def _component_reference_matches(component: str, value: str) -> bool:
    image_name = _reference_image_name(value)
    return image_name == component or image_name.endswith(f"-{component}")


def validate_image_references(
    image_references: Mapping[str, str],
) -> tuple[SmokeFinding, ...]:
    """Require exactly Web/API/Worker refs, each pinned to a lowercase digest."""

    findings: list[SmokeFinding] = []
    if not isinstance(image_references, Mapping) or set(image_references) != set(COMPONENTS):
        _add(findings, "component-set")
        return tuple(findings)

    for component in COMPONENTS:
        reference = image_references.get(component)
        if not _is_digest_reference(reference):
            _add(findings, "digest-reference")
        elif not _component_reference_matches(component, reference):
            _add(findings, "component-image")
    return tuple(findings)


def _environment_names(value: object) -> frozenset[str]:
    if isinstance(value, Mapping):
        return frozenset(str(name) for name in value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        names: set[str] = set()
        for entry in value:
            if isinstance(entry, str):
                names.add(entry.split("=", 1)[0])
        return frozenset(names)
    return frozenset()


def _ports(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(value)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _port_host(port: object) -> str | None:
    if isinstance(port, Mapping):
        host = port.get("host_ip")
        return host if isinstance(host, str) and host else None
    if not isinstance(port, str):
        return None

    value = port.strip()
    if value.startswith("["):
        closing = value.find("]")
        return value[1:closing] if closing > 1 else None
    fields = value.split(":")
    if len(fields) >= 3:
        return fields[0] or None
    # A short ``published:target`` form has no host restriction and is not
    # safe for this checker.  A bare target is likewise not host-bound.
    return None


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.casefold() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_compose_model(
    compose: object,
    image_references: Mapping[str, str] | None = None,
) -> tuple[SmokeFinding, ...]:
    """Validate a rendered/pure Compose model without executing Docker.

    The model must contain ``db``, ``api``, ``worker``, and ``web``.  Release
    component images are digest-pinned, only Web has one explicit loopback
    host port, and ``OPENAI_API_KEY`` exists only in the Worker environment.
    """

    findings: list[SmokeFinding] = []
    if not isinstance(compose, Mapping):
        return (SmokeFinding("service-set"),)
    if image_references is not None:
        findings.extend(validate_image_references(image_references))

    services = _mapping(compose.get("services"))
    if set(services) != SERVICES:
        _add(findings, "service-set")

    service_maps = {name: _mapping(services.get(name)) for name in SERVICES}

    if any(service.get("network_mode") == "host" for service in service_maps.values()):
        _add(findings, "host-network")

    for component in COMPONENTS:
        service = service_maps[component]
        image = service.get("image")
        if not _is_digest_reference(image):
            _add(findings, "digest-reference")
        elif not _component_reference_matches(component, image):
            _add(findings, "component-image")
        if image_references is not None and image != image_references.get(component):
            _add(findings, "image-reference")
        if service.get("build") is not None:
            _add(findings, "build-enabled")

    for service_name in ("db", "api", "worker"):
        if _ports(service_maps[service_name].get("ports")):
            _add(findings, "internal-host-port")

    web_ports = _ports(service_maps["web"].get("ports"))
    if len(web_ports) != 1:
        _add(findings, "web-port")
    elif not _is_loopback_host(_port_host(web_ports[0])):
        _add(findings, "web-port-loopback")

    for service_name, service in service_maps.items():
        has_provider_key = "OPENAI_API_KEY" in _environment_names(service.get("environment"))
        if service_name == "worker":
            if not has_provider_key:
                _add(findings, "provider-key-scope")
        elif has_provider_key:
            _add(findings, "provider-key-scope")

    return _unique_findings(findings)


def _new_project_name() -> str:
    return f"familycare-release-smoke-{uuid.uuid4().hex[:12]}"


def _validate_project_name(project_name: str) -> tuple[SmokeFinding, ...]:
    if _PROJECT_NAME.fullmatch(project_name) is None:
        return (SmokeFinding("project-name"),)
    return ()


def build_compose_override(
    image_references: Mapping[str, str],
    *,
    project_name: str | None = None,
) -> str:
    """Build a minimal override using Compose's explicit ``!reset`` merge tag."""

    findings = list(validate_image_references(image_references))
    chosen_project_name = project_name or _new_project_name()
    findings.extend(_validate_project_name(chosen_project_name))
    if findings:
        raise SmokeValidationError(findings)

    lines = [f"name: {chosen_project_name}", "services:"]
    for component in COMPONENTS:
        lines.extend(
            (
                f"  {component}:",
                "    build: !reset null",
                f"    image: {image_references[component]}",
            )
        )
    return "\n".join(lines) + "\n"


def _secure_write(path: Path, content: bytes) -> None:
    """Create one new mode-0600 file without following an existing symlink."""

    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "wb") as stream:
            file_descriptor = None
            stream.write(content)
        os.chmod(path, 0o600)
    except OSError, TypeError:
        if file_descriptor is not None:
            os.close(file_descriptor)
        with suppress(OSError):
            path.unlink()
        raise SmokeValidationError((SmokeFinding("temporary-file"),)) from None


def write_compose_override(
    image_references: Mapping[str, str],
    directory: Path,
    *,
    project_name: str | None = None,
) -> Path:
    """Write one mode-0600 Compose override and return its path."""

    content = build_compose_override(image_references, project_name=project_name)
    path = Path(directory) / "compose.override.yaml"
    _secure_write(path, content.encode("utf-8"))
    return path


def _write_synthetic_environment(path: Path, key_path: Path) -> None:
    content = (
        "FAMILYCARE_DATABASE_NAME=synthetic_db\n"
        "FAMILYCARE_DATABASE_USER=synthetic_user\n"
        "FAMILYCARE_DATABASE_PASSWORD=synthetic_password\n"
        "FAMILYCARE_ENV=production\n"
        "FAMILYCARE_IMPORT_ROOT=/synthetic/familycare-import\n"
        f"FAMILYCARE_ARCHIVE_MASTER_KEY_FILE={key_path}\n"
        "OPENAI_API_KEY=synthetic-provider-key\n"
        "FAMILYCARE_AI_STRUCTURER_MODEL=synthetic-structurer\n"
        "FAMILYCARE_AI_VERIFIER_MODEL=synthetic-verifier\n"
        "FAMILYCARE_WEB_PORT=18080\n"
    ).encode()
    _secure_write(path, content)


@contextmanager
def temporary_smoke_files(
    image_references: Mapping[str, str],
    *,
    temp_parent: Path | None = None,
    project_name: str | None = None,
) -> Iterator[TemporarySmokeFiles]:
    """Create synthetic mode-0600 env/key/override files and always remove them."""

    findings = validate_image_references(image_references)
    chosen_project_name = project_name or _new_project_name()
    findings = _unique_findings((*findings, *_validate_project_name(chosen_project_name)))
    if findings:
        raise SmokeValidationError(findings)

    directory: Path | None = None
    try:
        try:
            directory = Path(
                tempfile.mkdtemp(
                    prefix="familycare-release-smoke-",
                    dir=str(temp_parent) if temp_parent is not None else None,
                )
            )
        except OSError:
            raise SmokeValidationError((SmokeFinding("temporary-directory"),)) from None

        key_path = directory / "archive-master.key"
        environment_path = directory / "compose.env"
        override_path = directory / "compose.override.yaml"
        _secure_write(key_path, _SYNTHETIC_KEY)
        _write_synthetic_environment(environment_path, key_path)
        generated_override = write_compose_override(
            image_references,
            directory,
            project_name=chosen_project_name,
        )
        # Keep this assertion internal: callers receive only paths and stable
        # findings, never generated file contents.
        if generated_override != override_path:
            raise SmokeValidationError((SmokeFinding("temporary-file"),))
        yield TemporarySmokeFiles(
            directory=directory,
            environment_path=environment_path,
            key_path=key_path,
            override_path=override_path,
            project_name=chosen_project_name,
            image_references=(
                image_references["web"],
                image_references["api"],
                image_references["worker"],
            ),
        )
    finally:
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError:
                raise SmokeValidationError((SmokeFinding("cleanup"),)) from None


def render_compose_config(
    files: TemporarySmokeFiles,
    *,
    compose_path: Path = COMPOSE_PATH,
    runner: ComposeRunner | None = None,
) -> tuple[SmokeFinding, ...]:
    """Run only ``docker compose config --format json`` and validate its model."""

    try:
        command_runner = runner or subprocess.run
        result = command_runner(
            files.config_check_command(compose_path),
            cwd=ROOT,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=30,
        )
    except OSError, subprocess.SubprocessError:
        return (SmokeFinding("compose-check"),)
    if result.returncode != 0:
        return (SmokeFinding("compose-config"),)
    try:
        model = json.loads(result.stdout)
    except TypeError, ValueError, json.JSONDecodeError:
        return (SmokeFinding("compose-render"),)
    if not isinstance(model, Mapping):
        return (SmokeFinding("compose-render"),)

    image_references = dict(zip(COMPONENTS, files.image_references, strict=True))
    return validate_compose_model(model, image_references)


# Descriptive aliases used by callers that prefer "config check" wording.
validate_image_refs = validate_image_references
validate_rendered_compose = validate_compose_model
generate_compose_override = write_compose_override
run_config_check = render_compose_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument(
        "--config-check",
        action="store_true",
        help="run read-only docker compose config validation; never start containers",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate refs, optionally run the read-only Compose config check."""

    args = _parser().parse_args(argv)
    image_references = {
        "web": args.web_image,
        "api": args.api_image,
        "worker": args.worker_image,
    }
    findings = validate_image_references(image_references)
    if findings:
        print(" ".join(finding.code for finding in findings))
        return 1

    if not args.config_check:
        print("validated")
        return 0

    try:
        with temporary_smoke_files(image_references) as files:
            findings = render_compose_config(files)
    except SmokeValidationError as error:
        findings = error.findings
    if findings:
        print(" ".join(finding.code for finding in findings))
        return 1
    print("config-passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMPONENTS",
    "COMPOSE_PATH",
    "Finding",
    "IMAGE_COMPONENTS",
    "ReleaseFinding",
    "SmokeFinding",
    "SmokeValidationError",
    "TemporarySmokeFiles",
    "build_compose_override",
    "generate_compose_override",
    "main",
    "render_compose_config",
    "run_config_check",
    "temporary_smoke_files",
    "validate_compose_model",
    "validate_image_refs",
    "validate_image_references",
    "validate_rendered_compose",
    "write_compose_override",
]

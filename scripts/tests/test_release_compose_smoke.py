from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import scripts.release_compose_smoke as smoke
from scripts.release_compose_smoke import (
    COMPONENTS,
    SmokeValidationError,
    temporary_smoke_files,
    validate_compose_model,
    validate_image_references,
)


def _digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _image_references() -> dict[str, str]:
    return {
        "web": f"ghcr.io/synthetic/familycare-web@{_digest('a')}",
        "api": f"ghcr.io/synthetic/familycare-api@{_digest('b')}",
        "worker": f"ghcr.io/synthetic/familycare-worker@{_digest('c')}",
    }


def _compose_model(references: dict[str, str] | None = None) -> dict[str, Any]:
    refs = references or _image_references()
    return {
        "services": {
            "db": {"image": "postgres:18.6-alpine"},
            "api": {"image": refs["api"], "environment": {}},
            "worker": {
                "image": refs["worker"],
                "environment": {"OPENAI_API_KEY": "synthetic-provider-key"},
            },
            "web": {
                "image": refs["web"],
                "ports": [{"host_ip": "127.0.0.1", "published": 18080, "target": 8080}],
            },
        }
    }


def test_rejects_tag_pinned_release_references() -> None:
    references = _image_references()
    references["web"] = "ghcr.io/synthetic/familycare-web:0.1.0"

    findings = validate_image_references(references)

    assert {finding.code for finding in findings} == {"digest-reference"}


@pytest.mark.parametrize(
    "reference",
    [
        "ghcr.io/synthetic/familycare-web@sha256:" + "A" * 64,
        "ghcr.io/synthetic/familycare-web@sha256:" + "a" * 63,
        "ghcr.io/synthetic/familycare-web@sha512:" + "a" * 64,
        "ghcr.io/synthetic/familycare-web:stable@sha256:" + "a" * 64,
    ],
)
def test_rejects_non_lowercase_sha256_digest(reference: str) -> None:
    references = _image_references()
    references["web"] = reference

    findings = validate_image_references(references)

    assert tuple(finding.code for finding in findings) == ("digest-reference",)


def test_rejects_wrong_release_component_set() -> None:
    references = _image_references()
    references["postgres"] = references.pop("api")

    findings = validate_image_references(references)

    assert {finding.code for finding in findings} == {"component-set"}
    assert COMPONENTS == ("web", "api", "worker")


def test_rejects_non_loopback_web_port() -> None:
    model = _compose_model()
    model["services"]["web"]["ports"] = [{"host_ip": "0.0.0.0", "published": 18080, "target": 8080}]

    findings = validate_compose_model(model, _image_references())

    assert {finding.code for finding in findings} == {"web-port-loopback"}


@pytest.mark.parametrize("service", ["api", "worker", "db"])
def test_rejects_exposed_internal_service(service: str) -> None:
    model = _compose_model()
    model["services"][service]["ports"] = [
        {"host_ip": "127.0.0.1", "published": 18081, "target": 8000}
    ]

    findings = validate_compose_model(model, _image_references())

    assert {finding.code for finding in findings} == {"internal-host-port"}


def test_rejects_provider_key_on_non_worker_service() -> None:
    model = _compose_model()
    model["services"]["api"]["environment"] = {"OPENAI_API_KEY": "synthetic-provider-key"}

    findings = validate_compose_model(model, _image_references())

    assert {finding.code for finding in findings} == {"provider-key-scope"}


def test_temporary_smoke_files_are_private_and_cleaned_up() -> None:
    with temporary_smoke_files(_image_references()) as files:
        paths = (files.environment_path, files.key_path, files.override_path)
        assert all(path.is_file() for path in paths)
        assert all(os.stat(path).st_mode & 0o777 == 0o600 for path in paths)
        directory = files.directory

    assert not directory.exists()
    assert all(not path.exists() for path in paths)


def test_override_replaces_all_release_builds_with_digest_images() -> None:
    references = _image_references()

    with temporary_smoke_files(references) as files:
        override = files.override_path.read_text(encoding="utf-8")

    assert override.count("build: !reset null") == len(COMPONENTS)
    for component in COMPONENTS:
        assert f"  {component}:\n" in override
        assert f"image: {references[component]}" in override
    assert f"name: {files.project_name}" in override


def test_setup_failure_removes_already_created_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directories: list[Path] = []

    def fail_after_directory(
        _references: dict[str, str],
        directory: Path,
        *,
        project_name: str | None = None,
    ) -> Path:
        directories.append(directory)
        raise SmokeValidationError((smoke.SmokeFinding("synthetic-failure"),))

    monkeypatch.setattr(smoke, "write_compose_override", fail_after_directory)

    with pytest.raises(SmokeValidationError) as caught, temporary_smoke_files(_image_references()):
        pytest.fail("override setup should fail")

    assert tuple(finding.code for finding in caught.value.findings) == ("synthetic-failure",)
    assert len(directories) == 1
    assert not directories[0].exists()


def test_config_check_parses_only_json_model_and_never_returns_process_output() -> None:
    model = _compose_model()
    result = subprocess.CompletedProcess(
        args=("docker", "compose"),
        returncode=0,
        stdout=json.dumps(model),
        stderr="synthetic stderr must not escape",
    )

    with temporary_smoke_files(_image_references()) as files:
        findings = smoke.render_compose_config(
            files,
            runner=lambda *_args, **_kwargs: result,
        )

    assert findings == ()


def test_config_failure_is_a_stable_code_without_output_or_path() -> None:
    result = subprocess.CompletedProcess(
        args=("docker", "compose"),
        returncode=1,
        stdout="synthetic compose body",
        stderr="synthetic provider value",
    )

    with temporary_smoke_files(_image_references()) as files:
        findings = smoke.render_compose_config(
            files,
            runner=lambda *_args, **_kwargs: result,
        )

    assert tuple(finding.code for finding in findings) == ("compose-config",)
    assert "synthetic" not in str(findings)


def test_validation_error_string_contains_codes_only() -> None:
    invalid_reference = "ghcr.io/synthetic/familycare-web:0.1.0"
    references = _image_references()
    references["web"] = invalid_reference

    with pytest.raises(SmokeValidationError) as caught:
        smoke.build_compose_override(references)

    assert str(caught.value) == "digest-reference"
    assert invalid_reference not in str(caught.value)


def test_temporary_smoke_files_clean_up_after_validation_failure() -> None:
    references = _image_references()
    references["worker"] = "ghcr.io/synthetic/familycare-worker:0.1.0"

    with pytest.raises(SmokeValidationError) as caught, temporary_smoke_files(references):
        pytest.fail("invalid references must fail before creating runtime files")

    assert tuple(finding.code for finding in caught.value.findings) == ("digest-reference",)


def test_config_check_command_is_read_only_and_uses_unique_project_name() -> None:
    with temporary_smoke_files(_image_references()) as files:
        command = files.config_check_command()

    assert command[-2:] == ("--format", "json")
    assert "up" not in command
    assert "down" not in command
    assert command[0:2] == ("docker", "compose")
    assert command[command.index("-p") + 1].startswith("familycare-release-smoke-")

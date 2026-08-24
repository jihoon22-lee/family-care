import pytest

from scripts.check_workflows import (
    CI_PATH,
    DEPENDABOT_PATH,
    RELEASE_PATH,
    validate_ci,
    validate_dependabot,
    validate_release,
)


def current_ci() -> str:
    return CI_PATH.read_text(encoding="utf-8")


def current_release() -> str:
    return RELEASE_PATH.read_text(encoding="utf-8")


def current_dependabot() -> str:
    return DEPENDABOT_PATH.read_text(encoding="utf-8")


def test_current_ci_satisfies_workflow_policy() -> None:
    assert validate_ci(current_ci()) == []


def test_workflow_policy_rejects_mutable_action_reference() -> None:
    modified = current_ci().replace(
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@v7",
        1,
    )

    assert any("40-character lowercase SHA" in error for error in validate_ci(modified))


def test_workflow_policy_rejects_repository_secret() -> None:
    modified = current_ci().replace("${{ github.token }}", "${{ secrets.CI_TOKEN }}")

    assert any("repository secrets" in error for error in validate_ci(modified))


def test_workflow_policy_rejects_container_push() -> None:
    modified = current_ci().replace("push: false", "push: true")

    errors = validate_ci(modified)
    assert any("build-only containers" in error for error in errors)
    assert any("must not push" in error for error in errors)


def test_current_release_satisfies_workflow_policy() -> None:
    assert validate_release(current_release()) == []


def test_current_dependabot_satisfies_update_policy() -> None:
    assert validate_dependabot(current_dependabot()) == []


@pytest.mark.parametrize(
    ("dependency", "expected_update_type"),
    [
        ("typescript", "version-update:semver-major"),
        ("postgres", "version-update:semver-major"),
        ("node", "version-update:semver-major"),
        ("python", "version-update:semver-minor"),
    ],
)
def test_dependabot_policy_requires_each_official_update_type(
    dependency: str, expected_update_type: str
) -> None:
    content = current_dependabot()
    marker = f'      - dependency-name: "{dependency}"'
    start = content.index(marker)
    end = content.find("      - dependency-name:", start + len(marker))
    if end == -1:
        end = content.find("    commit-message:", start)
    policy = content[start:end]
    modified_policy = policy.replace(
        expected_update_type, expected_update_type.removeprefix("version-update:")
    )
    modified = content[:start] + modified_policy + content[end:]

    errors = validate_dependabot(modified)
    assert any(dependency in error and expected_update_type in error for error in errors)


def test_dependabot_policy_requires_the_expected_directory() -> None:
    modified = current_dependabot().replace(
        "directory: /infra/compose",
        "directory: /wrong-compose-directory",
        1,
    )

    errors = validate_dependabot(modified)
    assert any("postgres" in error and "/infra/compose" in error for error in errors)


def test_release_policy_rejects_broad_tag_trigger() -> None:
    modified = current_release().replace("v[0-9]+.[0-9]+.[0-9]+", "v*")

    assert any("semantic-version tags" in error for error in validate_release(modified))


def test_release_policy_rejects_production_deployment() -> None:
    modified = current_release() + "\n# gcloud run deploy\n"

    assert any("production deployment" in error for error in validate_release(modified))


def test_release_policy_requires_package_publish_permission() -> None:
    modified = current_release().replace("packages: write", "packages: read")

    assert any("package write permission" in error for error in validate_release(modified))

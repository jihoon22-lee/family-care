from collections.abc import Callable

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


@pytest.mark.parametrize(
    ("content_loader", "validator"),
    [(current_ci, validate_ci), (current_release, validate_release)],
)
def test_database_workflow_requires_a_dedicated_destructive_test_boundary(
    content_loader: Callable[[], str],
    validator: Callable[[str], list[str]],
) -> None:
    content = content_loader()
    modified = content.replace("FAMILYCARE_TEST_DATABASE_URL:", "REMOVED_TEST_DATABASE_URL:")
    errors = validator(modified)

    assert any("dedicated test database URL" in error for error in errors)

    modified = content.replace(
        "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB:",
        "REMOVED_DESTRUCTIVE_TEST_OPT_IN:",
    )
    errors = validator(modified)

    assert any("destructive test opt-in" in error for error in errors)


def test_current_release_satisfies_workflow_policy() -> None:
    assert validate_release(current_release()) == []


def test_current_dependabot_satisfies_update_policy() -> None:
    assert validate_dependabot(current_dependabot()) == []


def test_dependabot_policy_keeps_node_types_on_the_runtime_major() -> None:
    content = current_dependabot()
    marker = '      - dependency-name: "@types/node"'

    assert marker in content

    start = content.index(marker)
    end = content.find("      - dependency-name:", start + len(marker))
    if end == -1:
        end = content.find("    commit-message:", start)
    policy = content[start:end]
    modified = (
        content[:start]
        + policy.replace("version-update:semver-major", "semver-major")
        + content[end:]
    )

    errors = validate_dependabot(modified)
    assert any(
        "@types/node" in error and "version-update:semver-major" in error for error in errors
    )


@pytest.mark.parametrize("ecosystem", ["npm", "pip"])
def test_dependabot_dev_group_keeps_generated_commit_subjects_short(
    ecosystem: str,
) -> None:
    content = current_dependabot()
    marker = f"  - package-ecosystem: {ecosystem}"
    start = content.index(marker)
    end = content.find("  - package-ecosystem:", start + len(marker))
    if end == -1:
        end = len(content)
    package_policy = content[start:end]

    assert "\n      dev:\n" in package_policy

    modified_policy = package_policy.replace(
        "\n      dev:\n", "\n      development-dependencies:\n"
    )
    modified = content[:start] + modified_policy + content[end:]
    errors = validate_dependabot(modified)

    assert any(ecosystem in error and "group 'dev'" in error for error in errors)


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

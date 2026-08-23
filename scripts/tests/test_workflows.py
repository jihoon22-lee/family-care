from scripts.check_workflows import CI_PATH, RELEASE_PATH, validate_ci, validate_release


def current_ci() -> str:
    return CI_PATH.read_text(encoding="utf-8")


def current_release() -> str:
    return RELEASE_PATH.read_text(encoding="utf-8")


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


def test_release_policy_rejects_broad_tag_trigger() -> None:
    modified = current_release().replace("v[0-9]+.[0-9]+.[0-9]+", "v*")

    assert any("semantic-version tags" in error for error in validate_release(modified))


def test_release_policy_rejects_production_deployment() -> None:
    modified = current_release() + "\n# gcloud run deploy\n"

    assert any("production deployment" in error for error in validate_release(modified))


def test_release_policy_requires_package_publish_permission() -> None:
    modified = current_release().replace("packages: write", "packages: read")

    assert any("package write permission" in error for error in validate_release(modified))

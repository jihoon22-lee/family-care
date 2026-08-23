from scripts.check_workflows import CI_PATH, validate_ci


def current_ci() -> str:
    return CI_PATH.read_text(encoding="utf-8")


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

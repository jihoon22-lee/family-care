from scripts.check_workflows import RELEASE_PATH, validate_release


def _current_release() -> str:
    return RELEASE_PATH.read_text(encoding="utf-8")


def _messages(content: str) -> str:
    return "\n".join(validate_release(content))


def test_release_workflow_has_a_post_publish_verification_gate() -> None:
    content = _current_release()

    assert "  verify-publication:" in content
    assert "scripts/release_audit.py" in content
    assert "scripts/release_compose_smoke.py" in content
    assert "scripts/verify_release_images.py" in content
    assert validate_release(content) == []


def test_release_workflow_requires_exact_image_matrix() -> None:
    content = _current_release().replace(
        "          - name: worker\n            dockerfile: infra/containers/worker.Dockerfile\n",
        "          - name: worker\n            dockerfile: infra/containers/worker.Dockerfile\n"
        "          - name: exporter\n            dockerfile: infra/containers/api.Dockerfile\n",
    )

    assert "exact release image matrix" in _messages(content)


def test_release_workflow_rejects_wrong_component_dockerfile() -> None:
    content = _current_release().replace(
        "dockerfile: infra/containers/worker.Dockerfile",
        "dockerfile: infra/containers/api.Dockerfile",
    )

    assert "exact release image matrix" in _messages(content)


def test_release_workflow_rejects_any_latest_tag() -> None:
    content = _current_release().replace("latest=false", "latest=true")

    assert "latest tag is forbidden" in _messages(content)


def test_release_workflow_rejects_package_write_outside_publish() -> None:
    content = _current_release().replace(
        "  verify-publication:\n",
        "  unexpected-writer:\n"
        "    permissions:\n"
        "      packages: write\n"
        "    runs-on: ubuntu-latest\n"
        "  verify-publication:\n",
    )

    assert "packages: write must appear only on the publish job" in _messages(content)


def test_release_workflow_requires_read_only_verification_permission() -> None:
    content = _current_release().replace("packages: read", "packages: write")

    messages = _messages(content)
    assert "verification job requires packages: read" in messages
    assert "packages: write must appear only on the publish job" in messages


def test_release_workflow_requires_publish_before_verification() -> None:
    content = _current_release().replace("needs: publish", "needs: validate-foundation")

    assert "verification must depend on publish" in _messages(content)

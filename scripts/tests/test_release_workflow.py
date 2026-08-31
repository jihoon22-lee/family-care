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


def test_release_workflow_rejects_runner_context_in_job_level_env() -> None:
    content = _current_release().replace(
        "  publish-release:\n"
        "    name: Publish changelog-derived GitHub Release\n"
        "    needs: verify-publication\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: write\n"
        "      packages: read\n"
        "    steps:\n",
        "  publish-release:\n"
        "    name: Publish changelog-derived GitHub Release\n"
        "    needs: verify-publication\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: write\n"
        "      packages: read\n"
        "    env:\n"
        "      RELEASE_NOTES: ${{ runner.temp }}/familycare-release-notes.md\n"
        "    steps:\n",
    )

    assert "job-level env cannot use the runner context" in _messages(content)


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


def test_release_workflow_publishes_changelog_notes_after_verified_images() -> None:
    content = _current_release()

    assert "  publish-release:" in content
    assert "    needs: verify-publication" in content
    assert "          persist-credentials: false" in content
    assert "scripts/verify_release_images.py" in content
    assert '--evidence-output "$RELEASE_EVIDENCE"' in content
    assert "scripts/release_notes.py" in content
    assert '--notes-file "$RELEASE_NOTES"' in content
    assert "gh release create" in content
    assert "gh release edit" in content
    assert "scripts/cleanup_release_files.py" in content
    assert "      if: always()" in content
    assert validate_release(content) == []


def test_release_workflow_requires_release_to_depend_on_verification() -> None:
    content = _current_release().replace(
        "    needs: verify-publication",
        "    needs: publish",
    )

    assert "GitHub Release must depend on image verification" in _messages(content)


def test_release_workflow_limits_contents_write_to_release_job() -> None:
    content = _current_release().replace(
        "  verify-publication:\n",
        "  unexpected-writer:\n"
        "    permissions:\n"
        "      contents: write\n"
        "    runs-on: ubuntu-latest\n"
        "  verify-publication:\n",
    )

    assert "contents: write must appear only on the GitHub Release job" in _messages(content)


def test_release_workflow_requires_registry_read_on_both_verification_jobs() -> None:
    content = _current_release().replace(
        "  publish-release:\n"
        "    name: Publish changelog-derived GitHub Release\n"
        "    needs: verify-publication\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: write\n"
        "      packages: read\n",
        "  publish-release:\n"
        "    name: Publish changelog-derived GitHub Release\n"
        "    needs: verify-publication\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: write\n",
    )

    assert "packages: read must be limited to image verification jobs" in _messages(content)


def test_release_workflow_rejects_inline_release_notes() -> None:
    content = _current_release().replace(
        '--notes-file "$RELEASE_NOTES"',
        '--notes "$RELEASE_NOTES"',
        1,
    )

    messages = _messages(content)
    assert "release publication must use --notes-file" in messages
    assert "inline release notes are forbidden" in messages


def test_release_workflow_requires_exact_temporary_file_cleanup() -> None:
    content = _current_release().replace("      if: always()", "      if: success()")

    assert "release temporary files require always cleanup" in _messages(content)


def test_release_workflow_rejects_cleanup_that_swallows_unlink_failures() -> None:
    content = _current_release().replace(
        "uv run --no-sync python scripts/cleanup_release_files.py",
        'find "$RELEASE_EVIDENCE" -maxdepth 0 -type f -delete 2>/dev/null || true',
    )

    assert "release temporary files require exact no-follow cleanup" in _messages(content)


def test_release_workflow_does_not_persist_write_credentials() -> None:
    content = _current_release().replace("          persist-credentials: false", "")

    assert "GitHub Release checkout must not persist credentials" in _messages(content)

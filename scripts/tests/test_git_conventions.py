import pytest

from scripts.check_git_conventions import validate_branch_name, validate_commit_subject


@pytest.mark.parametrize(
    "branch",
    ["main", "build/project-foundation", "feat/policy-ledger"],
)
def test_branch_convention_accepts_supported_names(branch: str) -> None:
    assert validate_branch_name(branch) == []


@pytest.mark.parametrize(
    "branch",
    ["feature/foo", "Feature/foo", "build/project_foundation"],
)
def test_branch_convention_rejects_unsupported_names(branch: str) -> None:
    assert validate_branch_name(branch)


@pytest.mark.parametrize(
    "subject",
    ["docs: establish project governance", "feat(api): add health endpoint"],
)
def test_commit_convention_accepts_conventional_subjects(subject: str) -> None:
    assert validate_commit_subject(subject) == []


@pytest.mark.parametrize(
    "subject",
    [
        "add health endpoint",
        "Feat: add health endpoint",
        "feat: add health endpoint.",
        "feat: " + "x" * 67,
    ],
)
def test_commit_convention_rejects_invalid_subjects(subject: str) -> None:
    assert validate_commit_subject(subject)

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.cleanup_release_files import main, remove_release_files


def test_cleanup_unlinks_only_the_symlink_not_its_target(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    symlink = tmp_path / "release-notes.md"
    missing = tmp_path / "release-image-evidence.json"
    target.write_text("keep me", encoding="utf-8")
    symlink.symlink_to(target)

    remove_release_files((symlink, missing))

    assert not symlink.exists()
    assert not symlink.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep me"


def test_cleanup_ignores_absent_files_but_reports_real_unlink_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = tmp_path / "not-a-file"
    missing = tmp_path / "missing.md"
    directory.mkdir()

    result = main(["--path", str(directory), "--path", str(missing)])

    assert result == 1
    assert directory.is_dir()
    captured = capsys.readouterr()
    assert captured.err == "release-cleanup-error: temporary file could not be removed\n"


def test_cleanup_cli_requires_exactly_two_distinct_absolute_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    one_path = tmp_path / "one.md"

    result = main(["--path", str(one_path)])

    assert result == 1
    captured = capsys.readouterr()
    assert captured.err == "release-cleanup-error: exactly two distinct paths are required\n"

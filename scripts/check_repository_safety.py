#!/usr/bin/env python3
"""Reject sensitive or unsafe files before they enter the public repository."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2 * 1024 * 1024
FORBIDDEN_SUFFIXES = {
    ".bak",
    ".db",
    ".dump",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sql",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_SEGMENTS = {
    "actual-data",
    "documents",
    "ocr",
    "private",
    "uploads",
}
PDF_ALLOW_ROOT = Path("fixtures/synthetic")
IMAGE_ALLOW_ROOTS = (
    Path("apps/web/public"),
    Path("docs/assets"),
    Path("fixtures/synthetic"),
)
IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
FORBIDDEN_JSON_NAMES = {"credentials.json", "service-account.json", "service_account.json"}


def is_within(relative_path: Path, allowed_root: Path) -> bool:
    """Return whether a relative path is inside an allowed repository root."""

    return relative_path == allowed_root or allowed_root in relative_path.parents


def inspect_path(root: Path, path: Path) -> list[str]:
    """Return public-repository policy violations for one filesystem path."""

    errors: list[str] = []
    lexical_path = path if path.is_absolute() else root / path

    try:
        relative_path = lexical_path.relative_to(root)
    except ValueError:
        return [f"path is outside repository root: {lexical_path.name}"]

    if ".git" in relative_path.parts:
        return []

    resolved_root = root.resolve()
    try:
        lexical_path.resolve().relative_to(resolved_root)
    except ValueError:
        return [f"path resolves outside repository root: {relative_path}"]

    if not lexical_path.is_file():
        return errors

    normalized_parts = {part.casefold() for part in relative_path.parts}
    forbidden_segments = sorted(normalized_parts & FORBIDDEN_SEGMENTS)
    if forbidden_segments:
        errors.append(
            f"forbidden data directory in {relative_path}: {', '.join(forbidden_segments)}"
        )

    name = relative_path.name.casefold()
    suffix = relative_path.suffix.casefold()

    if suffix in FORBIDDEN_SUFFIXES:
        errors.append(f"forbidden file suffix {suffix}: {relative_path}")

    if suffix == ".pdf" and not is_within(relative_path, PDF_ALLOW_ROOT):
        errors.append(f"PDF is allowed only under {PDF_ALLOW_ROOT}: {relative_path}")

    if suffix in IMAGE_SUFFIXES and not any(
        is_within(relative_path, allowed_root) for allowed_root in IMAGE_ALLOW_ROOTS
    ):
        errors.append(f"image is outside approved public asset roots: {relative_path}")

    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        errors.append(f"environment file is not public: {relative_path}")

    if (
        name in FORBIDDEN_JSON_NAMES
        or (name.startswith("client_secret") and name.endswith(".json"))
        or ("service-account" in name and name.endswith(".json"))
        or ("service_account" in name and name.endswith(".json"))
    ):
        errors.append(f"credential-like JSON filename is forbidden: {relative_path}")

    size = lexical_path.stat().st_size
    if size > MAX_FILE_BYTES:
        errors.append(
            f"file size {size} exceeds public limit {MAX_FILE_BYTES}: {relative_path}"
        )

    return errors


def git_visible_paths(root: Path) -> list[Path]:
    """List tracked and non-ignored untracked files without parsing human output."""

    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def stdin_paths(root: Path) -> list[Path]:
    """Read NUL-delimited repository-relative paths from standard input."""

    return [root / Path(item.decode("utf-8")) for item in sys.stdin.buffer.read().split(b"\0") if item]


def walk_paths(root: Path) -> Iterable[Path]:
    """Yield files under a supplied root, excluding Git metadata."""

    return (path for path in root.rglob("*") if ".git" not in path.parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--all-files", action="store_true")
    parser.add_argument("--stdin0", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    if args.stdin0:
        paths = stdin_paths(root)
    elif args.paths:
        paths = [path if path.is_absolute() else root / path for path in args.paths]
    elif args.all_files:
        paths = list(walk_paths(root))
    else:
        paths = git_visible_paths(root)

    errors = [error for path in paths for error in inspect_path(root, path)]
    if errors:
        print("\n".join(sorted(set(errors))))
        return 1

    print(f"repository safety passed ({len(paths)} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

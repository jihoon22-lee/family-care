#!/usr/bin/env python3
"""Remove the two exact temporary files used to publish a GitHub Release."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseCleanupError(ValueError):
    """A stable, path-free release cleanup failure."""


def _validated_paths(paths: Sequence[Path]) -> tuple[Path, Path]:
    if len(paths) != 2 or paths[0] == paths[1]:
        raise ReleaseCleanupError("exactly two distinct paths are required")

    validated: list[Path] = []
    for path in paths:
        if not path.is_absolute():
            raise ReleaseCleanupError("temporary paths must be absolute")
        candidate = path.parent.resolve(strict=False) / path.name
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            pass
        else:
            raise ReleaseCleanupError("temporary paths must be outside the repository")
        validated.append(candidate)
    return validated[0], validated[1]


def remove_release_files(paths: Sequence[Path]) -> None:
    """Unlink exact file or symlink paths, ignoring absence but no other failure."""

    failures = False
    for path in _validated_paths(paths):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            failures = True
    if failures:
        raise ReleaseCleanupError("temporary file could not be removed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", default=[], type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        remove_release_files(tuple(args.path))
    except ReleaseCleanupError as exc:
        print(f"release-cleanup-error: {exc}", file=sys.stderr)
        return 1
    print("release-cleanup-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

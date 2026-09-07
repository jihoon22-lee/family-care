"""Apply an exact, externally prepared source manifest using count-only output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Never
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_reconciliation.source_bindings import (
    KnowledgeSourceBindingError,
    KnowledgeSourceBindingRepository,
    KnowledgeSourceManifest,
)

_MAX_BYTES = 1024 * 1024
_ROOT = Path(__file__).resolve().parents[1]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise KnowledgeSourceBindingError
    return result


def load_manifest(
    path: Path, expected_sha256: str, *, repository_root: Path = _ROOT
) -> KnowledgeSourceManifest:
    """Bind declared source metadata only after exact bounded local file verification."""
    try:
        if not path.is_absolute() or path.resolve().is_relative_to(repository_root.resolve()):
            raise KnowledgeSourceBindingError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BYTES:
                raise KnowledgeSourceBindingError
            raw = source.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise KnowledgeSourceBindingError
        return KnowledgeSourceManifest.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except OSError, ValueError:
        raise KnowledgeSourceBindingError from None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse normally echoes invalid arguments, which may contain private values.
        raise KnowledgeSourceBindingError


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    try:
        parser.parse_args(argv)
        manifest_path = os.environ.get("FAMILYCARE_PRIVATE_KNOWLEDGE_SOURCE_MANIFEST_PATH")
        manifest_sha256 = os.environ.get("FAMILYCARE_PRIVATE_KNOWLEDGE_SOURCE_MANIFEST_SHA256")
        household_id = os.environ.get("FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID")
        if not manifest_path or not manifest_sha256 or not household_id:
            raise KnowledgeSourceBindingError
        scope = HouseholdScope(UUID(household_id))
        manifest = load_manifest(Path(manifest_path), manifest_sha256)
        database_url = os.environ.get("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise KnowledgeSourceBindingError
        ids = KnowledgeSourceBindingRepository(database_url).apply_manifest(scope, manifest)
    except ValueError, OSError:
        print("KNOWLEDGE_SOURCE_BINDING_INVALID", file=sys.stderr)
        return 2
    print(json.dumps({"state": "APPLIED", "binding_count": len(ids)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

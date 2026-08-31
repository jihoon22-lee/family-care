"""Privacy properties for sanitized publication-package failures."""

from __future__ import annotations

from pathlib import Path

import pytest
from familycare_api.private_knowledge.errors import (
    PublicationErrorCode,
    PublicationPackageError,
)
from familycare_api.private_knowledge.publication_package import (
    load_rule_publication_package,
)

from apps.api.tests.private_knowledge_publication_fixtures import (
    mutate_publication_jsonl,
    write_synthetic_rule_publication_package,
)


@pytest.mark.parametrize(
    "private_marker",
    [
        "private phrase marker",
        "synthetic/source/key.pdf",
        "/private/absolute/package/path",
        '{"private":"json-value"}',
        "f" * 64,
        "private event text",
        "123456789.1234",
        "postgresql://private:secret@localhost/private",
        "SELECT private_value FROM private_table",
    ],
)
def test_loader_errors_exclude_all_private_values(
    tmp_path: Path,
    private_marker: str,
) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    mutate_publication_jsonl(
        root,
        "fact-normalizers.jsonl",
        lambda row: row.__setitem__("private_marker", private_marker),
    )

    with pytest.raises(PublicationPackageError) as caught:
        load_rule_publication_package(root, repository_root=tmp_path / "repository")

    assert caught.value.code is PublicationErrorCode.INVALID_RECORD
    assert str(caught.value) == "INVALID_RECORD:fact-normalizers.jsonl:1"
    assert private_marker not in str(caught.value)
    assert private_marker not in repr(caught.value)
    assert str(root) not in str(caught.value)


def test_publication_error_contract_contains_only_stable_coordinates() -> None:
    error = PublicationPackageError(
        PublicationErrorCode.BROKEN_REFERENCE,
        file_role="rule-citations.jsonl",
        row_number=2,
    )

    assert error.code is PublicationErrorCode.BROKEN_REFERENCE
    assert error.file_role == "rule-citations.jsonl"
    assert error.row_number == 2
    assert str(error) == "BROKEN_REFERENCE:rule-citations.jsonl:2"

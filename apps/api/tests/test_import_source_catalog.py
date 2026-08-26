from __future__ import annotations

import os
from pathlib import Path

import pytest
from familycare_api.documents.import_sources import (
    ImportSourceCatalog,
    ImportSourceNotFound,
)
from pypdf import PdfWriter


def _write_pdf(path: Path, *, password: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def test_catalog_lists_only_regular_pdfs_without_exposing_paths(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-inbox"
    plain = root / "nested" / "sample-policy.pdf"
    encrypted = root / "sample-encrypted.pdf"
    _write_pdf(plain)
    _write_pdf(encrypted, password="synthetic-password")
    (root / "ignored.txt").write_text("synthetic", encoding="utf-8")
    os.symlink(plain, root / "linked.pdf")

    catalog = ImportSourceCatalog(root)
    sources = catalog.list()

    assert [source.display_label for source in sources] == [
        "sample-encrypted.pdf",
        "sample-policy.pdf",
    ]
    assert [source.encrypted for source in sources] == [True, False]
    assert all(len(source.source_id) == 64 for source in sources)
    assert str(root) not in repr(sources)
    assert "nested" not in repr(sources)


def test_catalog_resolves_opaque_id_and_detects_replacement(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-inbox"
    source = root / "sample-policy.pdf"
    _write_pdf(source)
    catalog = ImportSourceCatalog(root)
    listed = catalog.list()[0]

    resolved = catalog.resolve(listed.source_id)

    assert resolved.source_key == "sample-policy.pdf"
    assert resolved.source_id == listed.source_id
    _write_pdf(source, password="new-synthetic-password")
    with pytest.raises(ImportSourceNotFound):
        catalog.resolve(listed.source_id)


def test_catalog_rejects_relative_or_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="import root"):
        ImportSourceCatalog(Path("relative"))
    with pytest.raises(ValueError, match="import root"):
        ImportSourceCatalog(tmp_path / "missing")

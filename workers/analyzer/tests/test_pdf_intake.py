from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from familycare_worker.pdf.errors import (
    DocumentNotFound,
    DocumentPathEscape,
    DocumentTooLarge,
    PageLimitExceeded,
    PasswordRequired,
    PdfCorrupt,
    UnsupportedFileType,
)
from familycare_worker.pdf.intake import (
    MAX_INPUT_BYTES,
    MAX_PDF_PAGES,
    OpenedSource,
    open_source,
    stream_sha256,
    validate_pdf,
)
from pypdf import PdfWriter


def write_synthetic_pdf(path: Path, *, pages: int = 1, password: str | None = None) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def test_open_source_accepts_relative_pdf_and_owns_only_descriptor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source_path = root / "nested" / "synthetic.pdf"
    source_path.parent.mkdir()
    write_synthetic_pdf(source_path)

    source = open_source(root, "nested/synthetic.pdf")
    try:
        assert isinstance(source, OpenedSource)
        assert source.source_key == "nested/synthetic.pdf"
        assert source.byte_size == source_path.stat().st_size
        assert not hasattr(source, "path")
        assert os.pread(source.fd, 5, 0) == b"%PDF-"
    finally:
        source.close()


@pytest.mark.parametrize(
    "source_key",
    [
        "/outside/synthetic.pdf",
        "nested/../synthetic.pdf",
        "C:\\synthetic.pdf",
        "\\\\server\\synthetic.pdf",
        "nested/synthetic\x00.pdf",
        "nested/synthetic\\file.pdf",
        "nested/synthetic\nfile.pdf",
    ],
)
def test_open_source_rejects_unsafe_source_keys(tmp_path: Path, source_key: str) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(DocumentPathEscape):
        open_source(root, source_key)


def test_open_source_requires_absolute_directory_root(tmp_path: Path) -> None:
    relative_root = Path("synthetic-root")

    with pytest.raises(DocumentPathEscape):
        open_source(relative_root, "synthetic.pdf")

    missing_root = tmp_path / "missing-root"
    with pytest.raises(DocumentPathEscape):
        open_source(missing_root, "synthetic.pdf")

    with pytest.raises(DocumentPathEscape):
        open_source(tmp_path, "a" * 513)


def test_open_source_distinguishes_missing_document_from_path_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(DocumentNotFound) as raised:
        open_source(root, "missing/synthetic.pdf")

    assert raised.value.__cause__ is None
    assert "missing" not in repr(raised.value)


def test_open_source_rejects_configured_root_symlink_without_error_details(
    tmp_path: Path,
) -> None:
    target = tmp_path / "root-target"
    target.mkdir()
    linked_root = tmp_path / "root-link"
    linked_root.symlink_to(target, target_is_directory=True)

    with pytest.raises(DocumentPathEscape) as raised:
        open_source(linked_root, "synthetic.pdf")

    assert raised.value.__cause__ is None
    assert str(target) not in repr(raised.value)
    assert "synthetic.pdf" not in repr(raised.value)


def test_open_source_rejects_final_and_intermediate_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "synthetic-outside.pdf"
    write_synthetic_pdf(outside)

    (root / "final-link.pdf").symlink_to(outside)
    (root / "intermediate-link").symlink_to(
        tmp_path / "outside-directory", target_is_directory=True
    )
    (tmp_path / "outside-directory").mkdir()
    (tmp_path / "outside-directory" / "synthetic.pdf").write_bytes(outside.read_bytes())

    with pytest.raises(DocumentPathEscape):
        open_source(root, "final-link.pdf")
    with pytest.raises(DocumentPathEscape):
        open_source(root, "intermediate-link/synthetic.pdf")


def test_open_source_rejects_directories_and_fifos(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "directory.pdf").mkdir()

    with pytest.raises(UnsupportedFileType):
        open_source(root, "directory.pdf")

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable on this platform")
    os.mkfifo(root / "pipe.pdf")
    with pytest.raises(UnsupportedFileType):
        open_source(root, "pipe.pdf")


@pytest.mark.parametrize(
    "size, expected", [(MAX_INPUT_BYTES, None), (MAX_INPUT_BYTES + 1, DocumentTooLarge)]
)
def test_open_source_enforces_exact_size_limit(
    tmp_path: Path, size: int, expected: type[DocumentTooLarge] | None
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "synthetic-size.pdf"
    with path.open("wb") as handle:
        handle.write(b"%PDF-")
        handle.truncate(size)

    if expected is None:
        source = open_source(root, path.name)
        source.close()
    else:
        with pytest.raises(expected):
            open_source(root, path.name)


def test_validate_pdf_checks_magic_structure_page_count_and_hash(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "synthetic.pdf"
    write_synthetic_pdf(path, pages=2)

    source = open_source(root, path.name)
    os.lseek(source.fd, 7, os.SEEK_SET)
    original_offset = os.lseek(source.fd, 0, os.SEEK_CUR)
    try:
        result = validate_pdf(source)
    finally:
        assert os.lseek(source.fd, 0, os.SEEK_CUR) == original_offset
        source.close()

    assert result.media_type == "application/pdf"
    assert result.byte_size == path.stat().st_size
    assert result.page_count == 2
    assert result.encrypted is False
    assert result.content_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_validate_pdf_rejects_wrong_magic_and_corrupt_structure(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    wrong_magic = root / "synthetic-wrong.pdf"
    wrong_magic.write_bytes(b"not a PDF")
    corrupt = root / "synthetic-corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.7\nsynthetic-corrupt")

    with pytest.raises(UnsupportedFileType):
        open_source(root, wrong_magic.name)

    corrupt_source = open_source(root, corrupt.name)
    try:
        with pytest.raises(PdfCorrupt):
            validate_pdf(corrupt_source)
    finally:
        corrupt_source.close()


@pytest.mark.parametrize(
    "pages, expected", [(MAX_PDF_PAGES, None), (MAX_PDF_PAGES + 1, PageLimitExceeded)]
)
def test_validate_pdf_enforces_exact_page_limit(
    tmp_path: Path, pages: int, expected: type[PageLimitExceeded] | None
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / f"synthetic-{pages}.pdf"
    write_synthetic_pdf(path, pages=pages)
    source = open_source(root, path.name)
    try:
        if expected is None:
            assert validate_pdf(source).page_count == pages
        else:
            with pytest.raises(expected):
                validate_pdf(source)
    finally:
        source.close()


def test_validate_pdf_rejects_encrypted_pdf_without_password_transport(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "synthetic-encrypted.pdf"
    write_synthetic_pdf(path, password="synthetic-password")
    source = open_source(root, path.name)
    try:
        with pytest.raises(PasswordRequired) as raised:
            validate_pdf(source)
    finally:
        source.close()

    assert "synthetic-encrypted.pdf" not in str(raised.value)
    assert "synthetic-password" not in str(raised.value)


def test_stream_sha256_reports_one_mib_reads_and_resets_handle() -> None:
    import io

    payload = b"synthetic-byte-" * 150000
    observed: list[int] = []
    handle = io.BytesIO(payload)

    digest = stream_sha256(handle, chunk_size=1_048_576, on_read=observed.append)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert observed[:-1] == [1_048_576] * (len(observed) - 1)
    assert 0 < observed[-1] <= 1_048_576
    assert handle.tell() == 0


def test_opened_source_fd_closes_cleanly_and_errors_are_sanitized(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "synthetic.pdf"
    write_synthetic_pdf(path)
    source = open_source(root, path.name)
    fd = source.fd
    source.close()
    source.close()

    with pytest.raises(OSError):
        os.fstat(fd)

    with pytest.raises(DocumentPathEscape) as raised:
        open_source(root, "../synthetic-private.pdf")
    message = str(raised.value)
    assert str(root) not in message
    assert "synthetic-private.pdf" not in message
    assert "synthetic-password" not in message

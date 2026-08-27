"""Descriptor-only pdfplumber extraction for synthetic Phase 1 documents."""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

import pdfplumber
from pdfminer.pdfdocument import (
    PDFEncryptionError,
    PDFPasswordIncorrect,
)
from pdfplumber.utils.exceptions import PdfminerException

from familycare_worker.generated_contracts import (
    Evidence,
    ExtractionCell,
    ExtractionPage,
    ExtractionResult,
    ExtractionTable,
    TextBlock,
)
from familycare_worker.pdf.coordinates import normalize_bbox
from familycare_worker.pdf.errors import (
    InvalidRequest,
    PasswordInvalid,
    PasswordRequired,
    PdfCorrupt,
)
from familycare_worker.pdf.quality import classify_page_quality

EXTRACTOR_NAME = "pdfplumber"
EXTRACTOR_VERSION = "0.11.10"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PDF_MAGIC = b"%PDF-"
_EXPECTED_SETTING_KEYS = frozenset(
    {
        "content_sha256",
        "document_version_id",
        "extractor_config_hash",
        "quality_rule_version",
        "table_strategy",
    }
)

TableStrategy = Literal["auto", "lines", "text"]


@dataclass(frozen=True)
class ExtractionSettings:
    """Post-intake metadata passed to the parser child, never queued by clients."""

    document_version_id: str
    content_sha256: str
    extractor_config_hash: str
    quality_rule_version: Literal["quality-v1"]
    table_strategy: TableStrategy

    def __post_init__(self) -> None:
        try:
            parsed_id = UUID(self.document_version_id)
        except ValueError:
            raise InvalidRequest from None
        if str(parsed_id) != self.document_version_id:
            raise InvalidRequest
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise InvalidRequest
        if not _SHA256_PATTERN.fullmatch(self.extractor_config_hash):
            raise InvalidRequest
        if self.quality_rule_version != "quality-v1":
            raise InvalidRequest
        if self.table_strategy not in {"auto", "lines", "text"}:
            raise InvalidRequest

    def to_json(self) -> str:
        """Return the canonical JSON accepted by the isolated parser."""

        return json.dumps(
            {
                "content_sha256": self.content_sha256,
                "document_version_id": self.document_version_id,
                "extractor_config_hash": self.extractor_config_hash,
                "quality_rule_version": self.quality_rule_version,
                "table_strategy": self.table_strategy,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, settings_json: str) -> ExtractionSettings:
        """Parse only the exact password-free post-intake setting shape."""

        try:
            value = json.loads(settings_json)
            if not isinstance(value, dict) or set(value) != _EXPECTED_SETTING_KEYS:
                raise InvalidRequest
            if not all(isinstance(item, str) for item in value.values()):
                raise InvalidRequest
            return cls(
                document_version_id=value["document_version_id"],
                content_sha256=value["content_sha256"],
                extractor_config_hash=value["extractor_config_hash"],
                quality_rule_version=cast(Literal["quality-v1"], value["quality_rule_version"]),
                table_strategy=cast(TableStrategy, value["table_strategy"]),
            )
        except KeyError, TypeError, ValueError, json.JSONDecodeError:
            raise InvalidRequest from None


def _require_local_read_only_pdf(source_fd: int) -> None:
    if not isinstance(source_fd, int) or source_fd < 0:
        raise InvalidRequest
    try:
        flags = fcntl.fcntl(source_fd, fcntl.F_GETFL)
        metadata = os.fstat(source_fd)
        magic = os.pread(source_fd, len(_PDF_MAGIC), 0)
    except OSError:
        raise PdfCorrupt from None
    if (flags & os.O_ACCMODE) != os.O_RDONLY:
        raise InvalidRequest
    if not stat.S_ISREG(metadata.st_mode) or magic != _PDF_MAGIC:
        raise PdfCorrupt


def _table_settings(strategy: TableStrategy) -> dict[str, str] | None:
    if strategy == "auto":
        return None
    return {
        "vertical_strategy": strategy,
        "horizontal_strategy": strategy,
    }


def _evidence(
    settings: ExtractionSettings,
    page_number: int,
    bbox: list[float] | None = None,
) -> Evidence:
    value = Evidence(
        document_version_id=settings.document_version_id,
        page_number=page_number,
        content_sha256=settings.content_sha256,
        review_state="candidate",
    )
    if bbox is not None:
        value["bbox"] = bbox
    return value


def _extract_blocks(page: Any, page_number: int) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    if not isinstance(words, list):
        raise PdfCorrupt
    for word in words:
        if not isinstance(word, dict):
            raise PdfCorrupt
        text = word.get("text")
        if not isinstance(text, str) or not text:
            continue
        try:
            bbox = normalize_bbox(
                cast(float, word.get("x0")),
                cast(float, word.get("top")),
                cast(float, word.get("x1")),
                cast(float, word.get("bottom")),
                page_width=page.width,
                page_height=page.height,
            )
        except PdfCorrupt:
            continue
        blocks.append(
            TextBlock(
                page_number=page_number,
                text=text,
                bbox=bbox,
                reading_order=len(blocks),
            )
        )
    return blocks


def _extract_tables(page: Any, strategy: TableStrategy) -> list[ExtractionTable]:
    extracted: list[ExtractionTable] = []
    tables = page.find_tables(table_settings=_table_settings(strategy))
    if not isinstance(tables, list):
        raise PdfCorrupt
    for table in tables:
        raw_table_bbox = cast(tuple[float, float, float, float], table.bbox)
        try:
            table_bbox = normalize_bbox(
                raw_table_bbox[0],
                raw_table_bbox[1],
                raw_table_bbox[2],
                raw_table_bbox[3],
                page_width=page.width,
                page_height=page.height,
            )
        except PdfCorrupt:
            continue
        text_rows = table.extract()
        cells: list[ExtractionCell] = []
        for row_index, row in enumerate(table.rows):
            row_text = text_rows[row_index] if row_index < len(text_rows) else []
            for column_index, cell_bbox in enumerate(row.cells):
                if cell_bbox is None:
                    continue
                text = row_text[column_index] if column_index < len(row_text) else ""
                raw_cell_bbox = cast(tuple[float, float, float, float], cell_bbox)
                try:
                    cell_bbox = normalize_bbox(
                        raw_cell_bbox[0],
                        raw_cell_bbox[1],
                        raw_cell_bbox[2],
                        raw_cell_bbox[3],
                        page_width=page.width,
                        page_height=page.height,
                    )
                except PdfCorrupt:
                    continue
                cells.append(
                    ExtractionCell(
                        row_index=row_index,
                        column_index=column_index,
                        text=text or "",
                        bbox=cell_bbox,
                        review_state="candidate",
                    )
                )
        extracted.append(
            ExtractionTable(
                bbox=table_bbox,
                cells=cells,
                review_state="candidate",
            )
        )
    return extracted


def _extract_page(
    page: Any,
    page_number: int,
    settings: ExtractionSettings,
) -> tuple[ExtractionPage, list[Evidence]]:
    width = float(page.width)
    height = float(page.height)
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise PdfCorrupt
    blocks = _extract_blocks(page, page_number)
    tables = _extract_tables(page, settings.table_strategy)
    page_text = " ".join(block["text"] for block in blocks)
    quality = classify_page_quality(page_text, settings.quality_rule_version)
    warnings = ["OCR_REQUIRED"] if quality["classification"] == "OCR_REQUIRED" else []
    page_result = ExtractionPage(
        page_number=page_number,
        width_points=round(width, 3),
        height_points=round(height, 3),
        quality=quality,
        blocks=blocks,
        tables=tables,
        warning_codes=warnings,
    )
    evidence = [_evidence(settings, page_number, block["bbox"]) for block in blocks]
    for table in tables:
        evidence.append(_evidence(settings, page_number, table["bbox"]))
        evidence.extend(_evidence(settings, page_number, cell["bbox"]) for cell in table["cells"])
    if not evidence:
        evidence.append(_evidence(settings, page_number))
    return page_result, evidence


class PdfPlumberExtractor:
    """Extract one local descriptor without reopening a filesystem path."""

    def extract(
        self,
        source_fd: int,
        settings: ExtractionSettings,
        *,
        password: str | None = None,
    ) -> ExtractionResult:
        """Extract pages and evidence; password is direct one-shot input only."""

        _require_local_read_only_pdf(source_fd)
        try:
            original_offset = os.lseek(source_fd, 0, os.SEEK_CUR)
            duplicate_fd = os.dup(source_fd)
        except OSError:
            raise PdfCorrupt from None

        pdf: Any | None = None
        try:
            with os.fdopen(duplicate_fd, "rb", closefd=True) as handle:
                duplicate_fd = -1
                try:
                    pdf = pdfplumber.open(handle, password=password)
                except PdfminerException as error:
                    password_error = any(
                        isinstance(item, (PDFPasswordIncorrect, PDFEncryptionError))
                        for item in error.args
                    )
                    del error
                    if password_error:
                        if password is None:
                            raise PasswordRequired from None
                        raise PasswordInvalid from None
                    raise PdfCorrupt from None

                pages: list[ExtractionPage] = []
                evidence: list[Evidence] = []
                for page_number, page in enumerate(pdf.pages, start=1):
                    try:
                        page_result, page_evidence = _extract_page(page, page_number, settings)
                        pages.append(page_result)
                        evidence.extend(page_evidence)
                    finally:
                        page.close()
                if not pages:
                    raise PdfCorrupt
        except PasswordInvalid, PasswordRequired, PdfCorrupt:
            raise
        except OSError, TypeError, ValueError, KeyError, IndexError:
            raise PdfCorrupt from None
        except Exception:
            raise PdfCorrupt from None
        finally:
            if pdf is not None:
                with suppress(Exception):
                    pdf.close()
            if duplicate_fd >= 0:
                with suppress(OSError):
                    os.close(duplicate_fd)
            with suppress(OSError):
                os.lseek(source_fd, original_offset, os.SEEK_SET)

        return ExtractionResult(
            schema_version="1",
            content_sha256=settings.content_sha256,
            extractor_name=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            extractor_config_hash=settings.extractor_config_hash,
            quality_rule_version=settings.quality_rule_version,
            pages=pages,
            evidence=evidence,
        )


def parse_local_pdf(source_fd: int, settings_json: str) -> ExtractionResult:
    """Isolated password-free parser entrypoint."""

    settings = ExtractionSettings.from_json(settings_json)
    return PdfPlumberExtractor().extract(source_fd, settings)


__all__ = [
    "EXTRACTOR_NAME",
    "EXTRACTOR_VERSION",
    "ExtractionSettings",
    "PdfPlumberExtractor",
    "parse_local_pdf",
]

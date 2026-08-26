"""No-shell, bounded local Tesseract command adapter and strict TSV parser."""

from __future__ import annotations

import csv
import io
import math
import os
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

from familycare_worker.pdf.limits import WORKSPACE_FILE_MODE

from .models import (
    EnginePageResult,
    OcrConfigurationError,
    OcrExecutionError,
    OcrWarningCode,
    RawOcrBlock,
)

TESSERACT_BINARY = "/usr/bin/tesseract"
OCR_LANGUAGES = ("kor", "eng")
OCR_TIMEOUT_SECONDS = 60.0
MAX_TSV_BYTES = 8 * 1024 * 1024
MAX_BLOCKS_PER_PAGE = 10_000
MAX_WORD_CHARACTERS = 512
LOW_CONFIDENCE_THRESHOLD = 50.0
_TSV_FIELDS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)
_SANITIZED_ENV = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "OMP_THREAD_LIMIT": "1",
    "PATH": "/usr/bin:/bin",
}


@dataclass(frozen=True)
class CommandOutput:
    returncode: int
    stdout: bytes


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: list[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandOutput: ...


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_bounded_command(
    argv: list[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> CommandOutput:
    """Capture stdout incrementally with a hard byte and wall-clock bound."""

    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise OcrConfigurationError
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=_SANITIZED_ENV,
            close_fds=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise OcrExecutionError("OCR_UNAVAILABLE") from None
    except OSError:
        raise OcrExecutionError("OCR_FAILED") from None
    if process.stdout is None:
        _kill_process(process)
        raise OcrExecutionError("OCR_FAILED")

    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process(process)
                raise OcrExecutionError("OCR_TIMEOUT")
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_output_bytes:
                    _kill_process(process)
                    raise OcrExecutionError("OCR_OUTPUT_LIMIT_EXCEEDED")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_process(process)
            raise OcrExecutionError("OCR_TIMEOUT")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_process(process)
            raise OcrExecutionError("OCR_TIMEOUT") from None
        return CommandOutput(returncode=returncode, stdout=bytes(output))
    except OcrExecutionError:
        raise
    except OSError:
        _kill_process(process)
        raise OcrExecutionError("OCR_FAILED") from None
    finally:
        selector.close()
        process.stdout.close()


def _secure_image_dimensions(image_path: Path) -> tuple[int, int]:
    try:
        lexical = image_path if image_path.is_absolute() else image_path.absolute()
        link_metadata = os.lstat(lexical)
        metadata = os.stat(lexical, follow_symlinks=False)
        if (
            stat.S_ISLNK(link_metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != WORKSPACE_FILE_MODE
            or metadata.st_size <= 0
            or metadata.st_size > 64 * 1024 * 1024
        ):
            raise OcrConfigurationError
        with Image.open(lexical) as image:
            image.verify()
        with Image.open(lexical) as image:
            width, height = image.size
        if width < 1 or height < 1 or width > 20_000 or height > 20_000:
            raise OcrConfigurationError
        if width * height > 25_000_000:
            raise OcrConfigurationError
        return width, height
    except OcrConfigurationError:
        raise
    except OSError, ValueError:
        raise OcrConfigurationError from None


def _integer(row: dict[str | None, str | list[str] | None], field: str) -> int:
    value = row.get(field)
    if not isinstance(value, str):
        raise OcrExecutionError("OCR_FAILED")
    try:
        parsed = int(value)
    except ValueError:
        raise OcrExecutionError("OCR_FAILED") from None
    return parsed


def _parse_tsv(stdout: bytes, width: int, height: int) -> tuple[RawOcrBlock, ...]:
    try:
        content = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise OcrExecutionError("OCR_FAILED") from None
    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    if tuple(reader.fieldnames or ()) != _TSV_FIELDS:
        raise OcrExecutionError("OCR_FAILED")
    blocks: list[RawOcrBlock] = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise OcrExecutionError("OCR_FAILED")
        if _integer(row, "level") != 5:
            continue
        raw_text = row.get("text")
        if not isinstance(raw_text, str):
            raise OcrExecutionError("OCR_FAILED")
        text = " ".join(raw_text.split())
        if not text:
            continue
        if len(text) > MAX_WORD_CHARACTERS:
            raise OcrExecutionError("OCR_OUTPUT_LIMIT_EXCEEDED")
        left = _integer(row, "left")
        top = _integer(row, "top")
        block_width = _integer(row, "width")
        block_height = _integer(row, "height")
        try:
            confidence = float(str(row["conf"]))
        except TypeError, ValueError:
            raise OcrExecutionError("OCR_FAILED") from None
        right = left + block_width
        bottom = top + block_height
        if (
            left < 0
            or top < 0
            or block_width <= 0
            or block_height <= 0
            or right > width
            or bottom > height
            or not math.isfinite(confidence)
            or confidence < 0
            or confidence > 100
        ):
            raise OcrExecutionError("OCR_FAILED")
        if len(blocks) >= MAX_BLOCKS_PER_PAGE:
            raise OcrExecutionError("OCR_OUTPUT_LIMIT_EXCEEDED")
        blocks.append(
            RawOcrBlock(
                text=text,
                pixel_bbox=(left, top, right, bottom),
                reading_order=len(blocks),
                confidence=round(confidence, 3),
            )
        )
    return tuple(blocks)


class TesseractOcrEngine:
    """Recognize one secure workspace image with a fixed local engine config."""

    def __init__(
        self,
        *,
        runner: CommandRunner = run_bounded_command,
        engine_version: str | None = None,
        binary: str = TESSERACT_BINARY,
    ) -> None:
        if binary != TESSERACT_BINARY:
            raise OcrConfigurationError
        self._runner = runner
        self._binary = binary
        self.engine_version = engine_version or self._read_version()
        if (
            not self.engine_version
            or len(self.engine_version) > 64
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
                for character in self.engine_version
            )
        ):
            raise OcrConfigurationError

    def _read_version(self) -> str:
        output = self._runner(
            [self._binary, "--version"],
            timeout_seconds=5.0,
            max_output_bytes=4096,
        )
        if output.returncode != 0:
            raise OcrExecutionError("OCR_UNAVAILABLE")
        try:
            first_line = output.stdout.decode("utf-8", errors="strict").splitlines()[0]
            name, version = first_line.split(maxsplit=1)
        except IndexError, UnicodeDecodeError, ValueError:
            raise OcrExecutionError("OCR_UNAVAILABLE") from None
        if name.casefold() != "tesseract":
            raise OcrExecutionError("OCR_UNAVAILABLE")
        return version.strip()

    def recognize(
        self,
        image_path: Path,
        *,
        languages: tuple[str, ...],
    ) -> EnginePageResult:
        if languages != OCR_LANGUAGES:
            raise OcrConfigurationError
        width, height = _secure_image_dimensions(Path(image_path))
        try:
            output = self._runner(
                [
                    self._binary,
                    str(image_path),
                    "stdout",
                    "--psm",
                    "6",
                    "-l",
                    "kor+eng",
                    "tsv",
                ],
                timeout_seconds=OCR_TIMEOUT_SECONDS,
                max_output_bytes=MAX_TSV_BYTES,
            )
        except OcrExecutionError:
            raise
        except TimeoutError:
            raise OcrExecutionError("OCR_TIMEOUT") from None
        except OSError:
            raise OcrExecutionError("OCR_UNAVAILABLE") from None
        except Exception:
            raise OcrExecutionError("OCR_FAILED") from None
        if output.returncode != 0:
            raise OcrExecutionError("OCR_FAILED")
        blocks = _parse_tsv(output.stdout, width, height)
        warnings: list[OcrWarningCode] = []
        if not blocks:
            warnings.append("NO_TEXT_DETECTED")
        elif any(block.confidence < LOW_CONFIDENCE_THRESHOLD for block in blocks):
            warnings.append("LOW_CONFIDENCE")
        return EnginePageResult(
            engine_name="tesseract",
            engine_version=self.engine_version,
            image_width_pixels=width,
            image_height_pixels=height,
            blocks=blocks,
            warning_codes=tuple(warnings),
        )


__all__ = [
    "CommandOutput",
    "CommandRunner",
    "MAX_BLOCKS_PER_PAGE",
    "MAX_TSV_BYTES",
    "OCR_LANGUAGES",
    "OCR_TIMEOUT_SECONDS",
    "TESSERACT_BINARY",
    "TesseractOcrEngine",
    "run_bounded_command",
]

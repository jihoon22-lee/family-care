"""Bounded local Tesseract adapter tests without invoking an external binary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from familycare_worker.ocr.engine import CommandOutput, TesseractOcrEngine
from familycare_worker.ocr.models import (
    OcrConfigurationError,
    OcrExecutionError,
)
from PIL import Image

TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)


class FakeRunner:
    def __init__(self, output: CommandOutput | BaseException) -> None:
        self.output = output
        self.calls: list[tuple[list[str], float, int]] = []

    def __call__(
        self, argv: list[str], *, timeout_seconds: float, max_output_bytes: int
    ) -> CommandOutput:
        self.calls.append((argv, timeout_seconds, max_output_bytes))
        if isinstance(self.output, BaseException):
            raise self.output
        return self.output


def _secure_png(path: Path) -> Path:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w+b") as handle:
        Image.new("RGB", (300, 200), "white").save(handle, format="PNG")
    return path


def _success_tsv() -> bytes:
    return (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t10\t20\t40\t15\t97.5\tSynthetic\n"
        + "5\t1\t1\t1\t1\t2\t55\t20\t35\t15\t92.0\tEvidence\n"
    ).encode()


def test_engine_invokes_fixed_no_shell_contract_and_parses_bounded_tsv(tmp_path: Path) -> None:
    image_path = _secure_png(tmp_path / "synthetic.png")
    runner = FakeRunner(CommandOutput(returncode=0, stdout=_success_tsv()))
    engine = TesseractOcrEngine(runner=runner, engine_version="5.3.0")

    result = engine.recognize(image_path, languages=("kor", "eng"))

    assert runner.calls == [
        (
            [
                "/usr/bin/tesseract",
                str(image_path),
                "stdout",
                "--psm",
                "6",
                "-l",
                "kor+eng",
                "tsv",
            ],
            60.0,
            8 * 1024 * 1024,
        )
    ]
    assert result.engine_name == "tesseract"
    assert result.engine_version == "5.3.0"
    assert result.image_width_pixels == 300
    assert result.image_height_pixels == 200
    assert [(block.text, block.pixel_bbox) for block in result.blocks] == [
        ("Synthetic", (10, 20, 50, 35)),
        ("Evidence", (55, 20, 90, 35)),
    ]
    assert [block.reading_order for block in result.blocks] == [0, 1]
    assert result.warning_codes == ()


def test_engine_returns_stable_warning_for_valid_empty_output(tmp_path: Path) -> None:
    image_path = _secure_png(tmp_path / "synthetic.png")
    runner = FakeRunner(CommandOutput(returncode=0, stdout=TSV_HEADER.encode()))

    result = TesseractOcrEngine(runner=runner, engine_version="5.3.0").recognize(
        image_path, languages=("kor", "eng")
    )

    assert result.blocks == ()
    assert result.warning_codes == ("NO_TEXT_DETECTED",)


@pytest.mark.parametrize("languages", [("eng",), ("eng", "kor"), ("kor", "deu")])
def test_engine_rejects_any_runtime_language_variation(
    tmp_path: Path, languages: tuple[str, ...]
) -> None:
    image_path = _secure_png(tmp_path / "synthetic.png")
    runner = FakeRunner(CommandOutput(returncode=0, stdout=_success_tsv()))

    with pytest.raises(OcrConfigurationError):
        TesseractOcrEngine(runner=runner, engine_version="5.3.0").recognize(
            image_path, languages=languages
        )

    assert runner.calls == []


@pytest.mark.parametrize(
    "output",
    [
        CommandOutput(returncode=1, stdout=b""),
        CommandOutput(returncode=0, stdout=b"malformed"),
        TimeoutError(),
    ],
)
def test_engine_maps_process_and_tsv_failures_to_sanitized_errors(
    tmp_path: Path, output: CommandOutput | BaseException
) -> None:
    image_path = _secure_png(tmp_path / "private-looking-name.png")
    runner = FakeRunner(output)

    with pytest.raises(OcrExecutionError) as captured:
        TesseractOcrEngine(runner=runner, engine_version="5.3.0").recognize(
            image_path, languages=("kor", "eng")
        )

    assert captured.value.code in {"OCR_FAILED", "OCR_TIMEOUT"}
    message = str(captured.value)
    assert "private-looking-name" not in message
    assert "malformed" not in message


def test_engine_rejects_out_of_bounds_tsv_coordinates(tmp_path: Path) -> None:
    image_path = _secure_png(tmp_path / "synthetic.png")
    output = (TSV_HEADER + "5\t1\t1\t1\t1\t1\t290\t190\t20\t20\t90\tSynthetic\n").encode()

    with pytest.raises(OcrExecutionError) as captured:
        TesseractOcrEngine(
            runner=FakeRunner(CommandOutput(returncode=0, stdout=output)),
            engine_version="5.3.0",
        ).recognize(image_path, languages=("kor", "eng"))

    assert captured.value.code == "OCR_FAILED"


def test_engine_rejects_more_than_ten_thousand_blocks(tmp_path: Path) -> None:
    image_path = _secure_png(tmp_path / "synthetic.png")
    rows = [f"5\t1\t1\t1\t1\t{index}\t1\t1\t1\t1\t90\tS\n" for index in range(1, 10002)]
    output = (TSV_HEADER + "".join(rows)).encode()

    with pytest.raises(OcrExecutionError) as captured:
        TesseractOcrEngine(
            runner=FakeRunner(CommandOutput(returncode=0, stdout=output)),
            engine_version="5.3.0",
        ).recognize(image_path, languages=("kor", "eng"))

    assert captured.value.code == "OCR_OUTPUT_LIMIT_EXCEEDED"

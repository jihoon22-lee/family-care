"""Direct one-shot password tests that never queue or persist a password."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from familycare_worker.pdf.errors import PasswordInvalid, PasswordRequired
from familycare_worker.pdf.extractor import ExtractionSettings, PdfPlumberExtractor
from familycare_worker.pdf.intake import open_source, validate_pdf


def _load_factory() -> Any:
    path = Path(__file__).with_name("synthetic_pdf_factory.py")
    spec = importlib.util.spec_from_file_location("synthetic_pdf_factory_passwords", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTORY = _load_factory()
SYNTHETIC_PASSWORD = "synthetic-one-shot-password"


def _settings() -> ExtractionSettings:
    return ExtractionSettings(
        document_version_id="00000000-0000-4000-8000-000000000102",
        content_sha256="c" * 64,
        extractor_config_hash="d" * 64,
        quality_rule_version="quality-v1",
        table_strategy="auto",
    )


def test_encrypted_intake_requires_password_without_transporting_one(tmp_path: Path) -> None:
    path = FACTORY.make_encrypted_pdf(tmp_path / "synthetic-encrypted.pdf", SYNTHETIC_PASSWORD)
    with open_source(tmp_path, path.name) as source, pytest.raises(PasswordRequired) as raised:
        validate_pdf(source)

    assert raised.value.__cause__ is None
    assert SYNTHETIC_PASSWORD not in repr(raised.value)
    assert path.name not in repr(raised.value)


def test_direct_wrong_password_maps_to_sanitized_password_invalid(tmp_path: Path) -> None:
    path = FACTORY.make_encrypted_pdf(tmp_path / "synthetic-wrong-password.pdf", SYNTHETIC_PASSWORD)
    with open_source(tmp_path, path.name) as source, pytest.raises(PasswordInvalid) as raised:
        PdfPlumberExtractor().extract(
            source.fd,
            _settings(),
            password="synthetic-wrong-password",
        )

    assert str(raised.value) == "PASSWORD_INVALID"
    assert raised.value.__cause__ is None
    assert SYNTHETIC_PASSWORD not in repr(raised.value)
    assert path.name not in repr(raised.value)


def test_direct_one_shot_password_is_not_stored_in_result(tmp_path: Path) -> None:
    path = FACTORY.make_encrypted_pdf(
        tmp_path / "synthetic-correct-password.pdf", SYNTHETIC_PASSWORD
    )
    with open_source(tmp_path, path.name) as source:
        result = PdfPlumberExtractor().extract(
            source.fd,
            _settings(),
            password=SYNTHETIC_PASSWORD,
        )

    assert result["pages"]
    assert SYNTHETIC_PASSWORD not in repr(result)
    assert "password" not in repr(result).lower()

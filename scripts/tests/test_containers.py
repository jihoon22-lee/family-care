from scripts.check_containers import DOCKERFILES, validate_image_references


def test_image_policy_accepts_supported_fully_pinned_updates() -> None:
    web = """\
FROM node:24.20.0-alpine AS builder
FROM nginxinc/nginx-unprivileged:1.31.2-alpine3.23 AS runtime
"""
    api = """\
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.14.8-slim AS builder
FROM python:3.14.8-slim AS runtime
"""

    assert validate_image_references("web", web) == []
    assert validate_image_references("api", api) == []


def test_image_policy_rejects_moving_and_unapproved_references() -> None:
    moving = """\
FROM node:24-alpine AS builder
FROM nginxinc/nginx-unprivileged:1.31.2-alpine3.23 AS runtime
"""
    unsupported = """\
FROM node:25.0.0-alpine AS builder
FROM nginxinc/nginx-unprivileged:1.31.2-alpine3.23 AS runtime
"""

    moving_errors = validate_image_references("web", moving)
    unsupported_errors = validate_image_references("web", unsupported)

    assert len(moving_errors) == 1
    assert "fully pinned Node 24 Alpine tag" in moving_errors[0]
    assert len(unsupported_errors) == 1
    assert "fully pinned Node 24 Alpine tag" in unsupported_errors[0]


def test_web_runtime_uses_the_approved_nginx_patch() -> None:
    dockerfile_path = DOCKERFILES["web"]
    nginx_image = "1.31.2-alpine3.23"

    assert nginx_image == "1.31.2-alpine3.23"
    assert f"nginxinc/nginx-unprivileged:{nginx_image}" in dockerfile_path.read_text(
        encoding="utf-8"
    )


def test_worker_runtime_includes_local_korean_english_ocr_only() -> None:
    dockerfile_path = DOCKERFILES["worker"]
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "tesseract-ocr" in content
    assert "tesseract-ocr-eng" in content
    assert "tesseract-ocr-kor" in content
    assert "tesseract --list-langs" in content
    assert "ghostscript" not in content.casefold()
    assert "imagemagick" not in content.casefold()
    assert "poppler" not in content.casefold()


def test_ci_smokes_worker_ocr_language_availability() -> None:
    workflow = (DOCKERFILES["worker"].parents[2] / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    assert "load: true" in workflow
    assert "familycare-worker:ci tesseract --list-langs" in workflow

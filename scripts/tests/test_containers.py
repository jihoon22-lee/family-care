from scripts.check_containers import DOCKERFILES


def test_web_runtime_uses_the_approved_nginx_patch() -> None:
    dockerfile_path, _builder_image, nginx_image = DOCKERFILES["web"]

    assert nginx_image == "1.31.2-alpine3.23"
    assert f"nginxinc/nginx-unprivileged:{nginx_image}" in dockerfile_path.read_text(
        encoding="utf-8"
    )


def test_worker_runtime_includes_local_korean_english_ocr_only() -> None:
    dockerfile_path, _python_image, _uv_image = DOCKERFILES["worker"]
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "tesseract-ocr" in content
    assert "tesseract-ocr-eng" in content
    assert "tesseract-ocr-kor" in content
    assert "tesseract --list-langs" in content
    assert "ghostscript" not in content.casefold()
    assert "imagemagick" not in content.casefold()
    assert "poppler" not in content.casefold()


def test_ci_smokes_worker_ocr_language_availability() -> None:
    workflow = (DOCKERFILES["worker"][0].parents[2] / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    assert "load: true" in workflow
    assert "familycare-worker:ci tesseract --list-langs" in workflow

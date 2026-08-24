from scripts.check_containers import DOCKERFILES


def test_web_runtime_uses_the_approved_nginx_patch() -> None:
    dockerfile_path, _builder_image, nginx_image = DOCKERFILES["web"]

    assert nginx_image == "1.31.2-alpine3.23"
    assert f"nginxinc/nginx-unprivileged:{nginx_image}" in dockerfile_path.read_text(
        encoding="utf-8"
    )

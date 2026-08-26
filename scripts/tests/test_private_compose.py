from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra/compose/compose.yaml"
NGINX_PATH = ROOT / "infra/containers/nginx.conf"


def _compose() -> dict[str, Any]:
    value = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_web_is_the_only_host_published_service() -> None:
    services = _compose()["services"]

    assert set(services) == {"api", "db", "web", "worker"}
    assert "ports" not in services["api"]
    assert "ports" not in services["db"]
    assert "ports" not in services["worker"]
    assert services["web"]["ports"] == ["127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}:8080"]


def test_private_runtime_declares_isolated_named_volumes() -> None:
    assert set(_compose()["volumes"]) == {
        "familycare-archive-data",
        "familycare-postgres-data",
        "familycare-secret-socket",
        "familycare-worker-work",
    }


def test_web_gateway_proxies_only_api_and_disables_response_caching() -> None:
    nginx = NGINX_PATH.read_text(encoding="utf-8")

    assert "location /api/ {" in nginx
    assert "proxy_pass http://api:8000;" in nginx
    assert "proxy_http_version 1.1;" in nginx
    assert "proxy_set_header Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in nginx
    assert "proxy_no_cache 1;" in nginx
    assert 'add_header Cache-Control "no-store" always;' in nginx
    assert "proxy_pass http://api:8000/documents" not in nginx
    assert "proxy_pass http://api:8000/archive" not in nginx


def test_web_waits_for_the_internal_api_healthcheck() -> None:
    web = _compose()["services"]["web"]

    assert web["depends_on"] == {"api": {"condition": "service_healthy"}}

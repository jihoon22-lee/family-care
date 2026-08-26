FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.14.7-slim AS builder

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY workers/analyzer/pyproject.toml workers/analyzer/pyproject.toml
COPY apps/api/src apps/api/src
RUN uv sync --frozen --no-dev --package familycare-api --no-editable

FROM python:3.14.7-slim AS runtime

RUN groupadd --system --gid 10003 familycare-runtime \
    && groupadd --system --gid 10001 familycare \
    && useradd --system --uid 10001 --gid 10001 --groups 10003 --no-create-home familycare \
    && install -d -o 10001 -g 10003 -m 2770 /run/familycare
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 apps/api/alembic.ini apps/api/alembic.ini
COPY --chown=10001:10001 apps/api/migrations apps/api/migrations

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"]

CMD ["uvicorn", "familycare_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

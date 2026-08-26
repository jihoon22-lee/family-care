FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.14.7-slim AS builder

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY workers/analyzer/pyproject.toml workers/analyzer/pyproject.toml
COPY workers/analyzer/src workers/analyzer/src
RUN uv sync --frozen --no-dev --package familycare-worker --no-editable

FROM python:3.14.7-slim AS runtime

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-kor \
    && tesseract --list-langs | grep -Fx 'eng' \
    && tesseract --list-langs | grep -Fx 'kor' \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10002 familycare \
    && useradd --system --uid 10002 --gid 10002 --no-create-home familycare
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --from=builder --chown=10002:10002 /app/.venv /app/.venv

USER 10002:10002
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["familycare-worker", "--health"]

CMD ["familycare-worker"]

FAMILYCARE_TMPDIR ?= /tmp
ENV_FILE ?= .env
COMPOSE := docker compose --env-file $(ENV_FILE) -f infra/compose/compose.yaml

.PHONY: setup check build up down

setup:
	test -f .env || cp .env.example .env
	TMPDIR=$(FAMILYCARE_TMPDIR) uv sync --all-packages --group dev
	corepack pnpm install --frozen-lockfile

check:
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run python scripts/check_documentation.py
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run python scripts/check_repository_safety.py
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run python scripts/check_contracts.py
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run python scripts/check_containers.py
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run python scripts/check_workflows.py
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run python scripts/check_git_conventions.py
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run ruff format --check .
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run ruff check .
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run mypy apps/api/src workers/analyzer/src scripts
	TMPDIR=$(FAMILYCARE_TMPDIR) uv run pytest -q
	corepack pnpm web:check

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --detach --wait

down:
	$(COMPOSE) down

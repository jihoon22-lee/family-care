# Project Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the documented, privacy-safe, locally runnable FamilyCare repository foundation with minimal Web/API/Worker services, PostgreSQL development infrastructure, CI, and GHCR tag releases.

**Architecture:** Use a pnpm workspace for the React PWA and a uv workspace for the FastAPI API and analyzer Worker. PostgreSQL is the only initial stateful service; public automation uses synthetic data and no external credentials. The repository enforces privacy boundaries before application validation and publishes three independent container images only from semantic-version tags.

**Tech Stack:** Node.js 24 LTS, pnpm 11.22.0, React 19.2.8, TypeScript 6.0.3, Vite 8.2.2, Python 3.14, uv 0.12.5 or newer 0.12.x, FastAPI 0.141.1, PostgreSQL 18, Docker Compose v2, GitHub Actions, GHCR.

**Spec:** `docs/design/project-foundation.md`

## Global Constraints

- The repository is public and has no `LICENSE`; no reuse permission is granted by default.
- Only fully synthetic fixtures may be committed.
- Real policies, terms, medical documents, extracted text, OCR images, embeddings, databases, logs, credentials, and Drive identifiers are forbidden.
- Web service workers may cache only the application shell; insurance data, API responses, and PDFs must never be cached.
- The decision vocabulary is exactly `MATCH`, `NO_MATCH`, and `UNKNOWN`.
- AI and Google Drive are excluded from Foundation runtime and CI.
- Cloud deployment is excluded; CD ends after publishing Web/API/Worker images to GHCR.
- Node.js stays on major 24, Python stays on major/minor 3.14, and PostgreSQL stays on major 18 for this plan.
- Local verification must run serially because the current WSL environment has limited free memory.
- Every commit must pass the checks introduced by that commit before moving to the next task.
- Implementation work is committed on `build/project-foundation`; the approved design-only root commit seeds the otherwise empty remote `main` so a pull request has a valid base.
- Branches follow `<type>/<kebab-case>` and commit subjects follow Conventional Commits as `<type>(<optional-scope>): <imperative description>`.
- Completion requires a GitHub pull request, successful required checks, a merge commit into `main`, and post-merge remote verification.

---

## File Responsibility Map

### Root governance and tooling

- `README.md`: product purpose, status, privacy boundary, quick start, verification entrypoints.
- `AGENTS.md`: mandatory instructions for human and agent contributors.
- `CHANGELOG.md`: Keep a Changelog entries tied to semantic versions.
- `CONTRIBUTING.md`: contribution workflow, synthetic-only fixture rules, commit and PR expectations.
- `SECURITY.md`: private vulnerability reporting and accidental sensitive-data response.
- `.editorconfig`, `.gitattributes`: cross-platform text consistency.
- `.gitignore`: first-line accidental data and secret exclusion.
- `.env.example`: non-secret variable names and safe local placeholders.
- `Makefile`: memorable orchestration commands that call package-native tools.
- `package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`: JavaScript workspace and locked dependencies.
- `pyproject.toml`, `uv.lock`, `.python-version`, `.node-version`: Python workspace and runtime version policy.

### Documentation

- `docs/architecture.md`: long-lived component, data-flow, trust-boundary, and deployment architecture.
- `docs/guide.md`: local setup, day-to-day commands, safe external-data configuration, release use.
- `docs/glossary.md`: stable Korean and English domain terms.
- `docs/design/data-model.md`: entity boundaries and lifecycle rules.
- `docs/design/pdf-ingestion.md`: future ingestion pipeline and temporary-file rules.
- `docs/design/coverage-decision-engine.md`: tri-state rule and evidence contract.
- `docs/design/security-privacy.md`: threat model, data classification, logging, incident boundaries.
- `docs/design/test-strategy.md`: test layers, fixtures, inaccessible checks, evidence requirements.
- `docs/adr/0001-modular-monolith.md`: modular monolith plus separate Worker.
- `docs/adr/0002-public-repository-data-boundary.md`: public source, private data.
- `docs/adr/0003-postgresql-job-queue.md`: PostgreSQL before a separate broker.
- `docs/adr/0004-evidence-first-tristate-decisions.md`: source-backed tri-state decisions.
- `docs/adr/0005-ghcr-only-continuous-delivery.md`: GHCR publication without production deployment.

### Runtime units

- `apps/web/src/App.tsx`: Foundation application shell only.
- `apps/web/src/main.tsx`: React browser entrypoint.
- `apps/web/vite.config.ts`: PWA and test configuration with static-shell-only caching.
- `apps/web/src/App.test.tsx`: shell behavior and safety-copy test.
- `apps/api/src/familycare_api/main.py`: FastAPI application factory.
- `apps/api/src/familycare_api/health.py`: health response types and probes.
- `apps/api/tests/test_health.py`: API health contract tests.
- `workers/analyzer/src/familycare_worker/__main__.py`: Worker process entrypoint.
- `workers/analyzer/src/familycare_worker/health.py`: Worker health payload and exit status.
- `workers/analyzer/tests/test_health.py`: Worker health contract tests.
- `packages/contracts/openapi/familycare.v1.json`: committed canonical Foundation API contract.
- `packages/contracts/schemas/analysis-job.v1.schema.json`: versioned future queue envelope contract.
- `fixtures/synthetic/README.md`: fixture authorship and provenance rules.
- `fixtures/synthetic/family.json`: entirely invented family identifiers.

### Database and containers

- `apps/api/alembic.ini`, `apps/api/migrations/env.py`: migration runner.
- `apps/api/migrations/versions/0001_foundation.py`: empty schema baseline that creates only Alembic metadata.
- `infra/compose/compose.yaml`: local PostgreSQL, API, Worker, Web services.
- `infra/containers/web.Dockerfile`: PWA build and unprivileged static runtime.
- `infra/containers/api.Dockerfile`: uv-synced unprivileged API runtime.
- `infra/containers/worker.Dockerfile`: uv-synced unprivileged Worker runtime.
- `infra/containers/nginx.conf`: history fallback and explicit no-store rules for sensitive paths.

### Automation and safety

- `scripts/check_documentation.py`: required-document and required-heading checks.
- `scripts/check_repository_safety.py`: forbidden path, extension, size, and tracked-file checks.
- `scripts/check_contracts.py`: generated OpenAPI drift and JSON Schema example checks.
- `scripts/check_workflows.py`: immutable action pins, permissions, event, and image-name checks.
- `scripts/tests/test_repository_safety.py`: isolated repository-safety unit tests.
- `.gitleaks.toml`: generic secret detection configuration without real secret allowlists.
- `.github/workflows/ci.yml`: public, secret-free validation.
- `.github/workflows/release.yml`: semantic tag validation and GHCR publication.
- `.github/dependabot.yml`: weekly npm, pip, Docker, and Actions updates.
- `.github/PULL_REQUEST_TEMPLATE.md`: privacy and verification checklist.

---

### Task 1: Documentation governance and architectural references

**Files:**
- Create: `scripts/check_documentation.py`
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `CHANGELOG.md`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `docs/architecture.md`
- Create: `docs/guide.md`
- Create: `docs/glossary.md`
- Create: `docs/design/data-model.md`
- Create: `docs/design/pdf-ingestion.md`
- Create: `docs/design/coverage-decision-engine.md`
- Create: `docs/design/security-privacy.md`
- Create: `docs/design/test-strategy.md`
- Create: `docs/adr/0001-modular-monolith.md`
- Create: `docs/adr/0002-public-repository-data-boundary.md`
- Create: `docs/adr/0003-postgresql-job-queue.md`
- Create: `docs/adr/0004-evidence-first-tristate-decisions.md`
- Create: `docs/adr/0005-ghcr-only-continuous-delivery.md`

**Interfaces:**
- Consumes: `docs/design/project-foundation.md` and `docs/plan/000-project-roadmap.md`.
- Produces: the document paths and mandatory headings enforced by `check_documentation.py`; later CI invokes `python3 scripts/check_documentation.py`.

- [ ] **Step 1: Write the documentation contract checker**

Create a standard-library script with this manifest and behavior:

```python
REQUIRED_HEADINGS = {
    "README.md": ["# FamilyCare", "## Privacy boundary", "## Quick start"],
    "AGENTS.md": ["# FamilyCare development instructions", "## Non-negotiable privacy rules", "## Required verification"],
    "CHANGELOG.md": ["# Changelog", "## [Unreleased]"],
    "docs/architecture.md": ["# FamilyCare architecture", "## Trust boundaries", "## Runtime components"],
    "docs/guide.md": ["# FamilyCare guide", "## Local development", "## Safe data handling"],
}

def validate_document(path: Path, headings: list[str]) -> list[str]:
    if not path.is_file():
        return [f"missing required document: {path}"]
    text = path.read_text(encoding="utf-8")
    return [f"{path}: missing heading {heading}" for heading in headings if heading not in text]
```

The `main()` function iterates the manifest, prints one error per line, and returns `1` on any error or `0` otherwise.
It also rejects unfinished-work markers built as `("T" + "BD", "T" + "ODO", "FIX" + "ME")` so the plan and documentation cannot silently defer required content.

- [ ] **Step 2: Run the checker and confirm the expected failure**

Run: `python3 scripts/check_documentation.py`

Expected: exit `1` with at least `missing required document: README.md`.

- [ ] **Step 3: Write the top-level documents**

Use these exact policy outcomes:

- README identifies the tool as decision support, not a guarantee of payment.
- README states the repository is public but real documents and derived data are private and forbidden.
- AGENTS requires status inspection before edits, synthetic-only tests, source-to-sink review for security claims, serial local verification under memory pressure, explicit reporting of unexecuted checks, and mandatory branch/commit conventions.
- AGENTS forbids moving actual data into the repository and forbids placing real identifiers in deny lists or examples.
- CHANGELOG uses Keep a Changelog sections `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`; the Unreleased entry records Foundation work.
- CONTRIBUTING requires conventional commit intent, focused PRs, and the privacy checklist.
- SECURITY directs vulnerability and accidental-data reports to GitHub private vulnerability reporting when enabled and says not to open a public issue containing sensitive data.
- The absence of a license is stated without claiming an open-source license.

- [ ] **Step 4: Write architecture, guide, glossary, feature designs, and ADRs**

Each design document must define scope, inputs, outputs, invariants, failure behavior, security considerations, tests, and deferred decisions. ADRs use `Accepted` status and sections `Context`, `Decision`, `Alternatives`, `Consequences`.

The guide must keep real-data setup disabled by default and show only `/absolute/path/outside/repository` as an example. The glossary must define at least `AppUser`, `FamilyMember`, `PolicyParty`, `PolicyContract`, `Rider`, `Clause`, `Evidence`, `MedicalEvent`, `ClaimCandidate`, `MATCH`, `NO_MATCH`, and `UNKNOWN`.

- [ ] **Step 5: Verify the documentation contract and formatting**

Run:

```bash
python3 scripts/check_documentation.py
git diff --check
```

Expected: checker exit `0`, including its unfinished-work-marker scan, and `git diff --check` exit `0`.

- [ ] **Step 6: Commit the documentation set**

```bash
git add README.md AGENTS.md CHANGELOG.md CONTRIBUTING.md SECURITY.md docs scripts/check_documentation.py
git commit -m "docs: establish project governance"
```

---

### Task 2: Repository privacy and safety guardrails

**Files:**
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `.editorconfig`
- Create: `.env.example`
- Create: `.gitleaks.toml`
- Create: `scripts/check_repository_safety.py`
- Create: `scripts/tests/__init__.py`
- Create: `scripts/tests/test_repository_safety.py`

**Interfaces:**
- Consumes: the repository root or an explicit list of paths.
- Produces: `inspect_path(root: Path, path: Path) -> list[str]` and CLI exit `0` for safe files, `1` for violations.

- [ ] **Step 1: Write failing unit tests for safety rules**

Use `unittest.TemporaryDirectory` to verify these exact cases:

```python
def test_rejects_pdf_outside_synthetic_fixtures(self): ...
def test_allows_pdf_inside_synthetic_fixtures(self): ...
def test_rejects_database_dump(self): ...
def test_rejects_private_key(self): ...
def test_rejects_forbidden_data_directory(self): ...
def test_rejects_file_larger_than_two_mib(self): ...
def test_allows_generated_web_icon(self): ...
```

The synthetic PDF test writes only `b"%PDF-1.4\nsynthetic fixture\n"`; it must not copy a real document.

- [ ] **Step 2: Run tests and confirm the expected import failure**

Run: `python3 -m unittest scripts.tests.test_repository_safety -v`

Expected: failure because `scripts.check_repository_safety` does not exist.

- [ ] **Step 3: Implement the repository safety scanner**

Implement these constants and rules:

```python
MAX_FILE_BYTES = 2 * 1024 * 1024
FORBIDDEN_SUFFIXES = {
    ".db", ".dump", ".key", ".log", ".p12", ".pem", ".pfx",
    ".sqlite", ".sqlite3",
}
FORBIDDEN_SEGMENTS = {
    "actual-data", "documents", "ocr", "private", "uploads",
}
PDF_ALLOW_ROOT = Path("fixtures/synthetic")
IMAGE_ALLOW_ROOTS = (Path("apps/web/public"), Path("docs/assets"), Path("fixtures/synthetic"))
```

Reject `.pdf` outside `fixtures/synthetic`, reject image suffixes outside approved roots, reject service-account-like JSON filenames, and inspect only regular files. CLI default input comes from `git ls-files --cached --others --exclude-standard -z`; `--all-files` may walk a supplied temporary root for tests.

- [ ] **Step 4: Add ignore, environment, and text policies**

`.gitignore` must cover environment files except `.env.example`, Python and Node caches, build outputs, coverage, databases, dumps, logs, PDFs except `fixtures/synthetic/**/*.pdf`, OCR/output/private directories, keys, and local container volumes.

`.env.example` contains only these variable names with safe non-secret placeholders:

```dotenv
FAMILYCARE_ENV=development
FAMILYCARE_DATABASE_HOST=127.0.0.1
FAMILYCARE_DATABASE_PORT=5432
FAMILYCARE_DATABASE_NAME=familycare
FAMILYCARE_DATABASE_USER=familycare
FAMILYCARE_DATABASE_PASSWORD=replace-for-local-development
FAMILYCARE_DOCUMENT_ROOT=/absolute/path/outside/repository
FAMILYCARE_WORK_ROOT=/absolute/path/outside/repository/work
```

`.gitleaks.toml` extends default rules and contains no real-value allowlist. `.gitattributes` enforces LF for source and keeps binary image/PDF types binary. `.editorconfig` uses UTF-8, LF, final newline, two spaces for web/YAML/JSON, and four spaces for Python.

- [ ] **Step 5: Run positive and negative safety verification**

Run:

```bash
python3 -m unittest scripts.tests.test_repository_safety -v
python3 scripts/check_repository_safety.py
git diff --check
```

Expected: seven unit tests pass, repository scan exits `0`, diff check exits `0`.

- [ ] **Step 6: Commit safety guardrails**

```bash
git add .gitignore .gitattributes .editorconfig .env.example .gitleaks.toml scripts
git commit -m "chore: guard public repository data boundary"
```

---

### Task 3: pnpm workspace and minimal PWA shell

**Files:**
- Create: `.node-version`
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `pnpm-lock.yaml`
- Create: `apps/web/package.json`
- Create: `apps/web/index.html`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/tsconfig.app.json`
- Create: `apps/web/tsconfig.node.json`
- Create: `apps/web/eslint.config.js`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/App.tsx`
- Create: `apps/web/src/App.test.tsx`
- Create: `apps/web/src/test/setup.ts`
- Create: `apps/web/src/styles.css`
- Create: `apps/web/public/icon.svg`

**Interfaces:**
- Consumes: no backend and no environment secrets.
- Produces: `@familycare/web` scripts `format:check`, `lint`, `typecheck`, `test`, and `build`; a static PWA shell that displays Foundation status and privacy copy.

- [ ] **Step 1: Create root workspace manifests**

Root `package.json` uses this contract:

```json
{
  "name": "family-care",
  "private": true,
  "packageManager": "pnpm@11.22.0",
  "engines": { "node": ">=24 <25" },
  "scripts": {
    "web:format": "pnpm --filter @familycare/web format:check",
    "web:lint": "pnpm --filter @familycare/web lint",
    "web:typecheck": "pnpm --filter @familycare/web typecheck",
    "web:test": "pnpm --filter @familycare/web test",
    "web:build": "pnpm --filter @familycare/web build",
    "web:check": "pnpm web:format && pnpm web:lint && pnpm web:typecheck && pnpm web:test && pnpm web:build"
  }
}
```

Set `.node-version` to `24.19.0`. `pnpm-workspace.yaml` includes `apps/*` and `packages/*`.

- [ ] **Step 2: Create Web package configuration and a failing UI test**

Pin these direct dependencies in `apps/web/package.json`:

- runtime: `react 19.2.8`, `react-dom 19.2.8`
- dev: `@eslint/js 10.0.1`, `@testing-library/jest-dom 7.0.1`, `@testing-library/react 16.3.2`, `@types/react 19.2.18`, `@types/react-dom 19.2.4`, `@vitejs/plugin-react 6.1.0`, `eslint 10.9.0`, `jsdom 30.0.1`, `prettier 3.9.6`, `typescript 6.0.3`, `typescript-eslint 8.67.0`, `vite 8.2.2`, `vite-plugin-pwa 1.3.0`, `vitest 4.1.11`.

Test exact visible behavior:

```tsx
render(<App />)
expect(screen.getByRole('heading', { name: 'FamilyCare' })).toBeInTheDocument()
expect(screen.getByText(/보험금 지급을 보장하지 않습니다/)).toBeInTheDocument()
expect(screen.getByText(/Foundation/)).toBeInTheDocument()
```

- [ ] **Step 3: Install dependencies and confirm the expected failing test**

Run:

```bash
corepack pnpm@11.22.0 install
corepack pnpm@11.22.0 --filter @familycare/web test
```

Expected: dependency installation creates `pnpm-lock.yaml`; test fails because `App.tsx` does not yet export the required UI.

- [ ] **Step 4: Implement the minimal application shell**

`App.tsx` must render only product name, Foundation status, and the decision-support disclaimer. Do not add forms, sample policies, real names, authentication stubs, or network calls.

Configure `VitePWA` with `registerType: "prompt"`, no `runtimeCaching`, a manifest containing only FamilyCare identity, and Workbox `globPatterns` limited to built HTML/CSS/JS/icons. Create a geometric compass-style SVG icon with no external asset or text derived from an insurance document. Add a comment stating that API responses and documents require an approved cache-policy change.

- [ ] **Step 5: Run the full Web validation**

Run serially:

```bash
corepack pnpm@11.22.0 web:format
corepack pnpm@11.22.0 web:lint
corepack pnpm@11.22.0 web:typecheck
corepack pnpm@11.22.0 web:test
corepack pnpm@11.22.0 web:build
```

Expected: every command exits `0`; Vitest reports the shell test passing; `apps/web/dist` contains a manifest and service worker.

- [ ] **Step 6: Commit the Web workspace**

```bash
git add .node-version package.json pnpm-workspace.yaml pnpm-lock.yaml apps/web
git commit -m "feat: add minimal FamilyCare PWA shell"
```

---

### Task 4: uv workspace, FastAPI health API, and Worker health command

**Files:**
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/familycare_api/__init__.py`
- Create: `apps/api/src/familycare_api/health.py`
- Create: `apps/api/src/familycare_api/main.py`
- Create: `apps/api/tests/test_health.py`
- Create: `workers/analyzer/pyproject.toml`
- Create: `workers/analyzer/src/familycare_worker/__init__.py`
- Create: `workers/analyzer/src/familycare_worker/__main__.py`
- Create: `workers/analyzer/src/familycare_worker/health.py`
- Create: `workers/analyzer/tests/test_health.py`

**Interfaces:**
- Consumes: `FAMILYCARE_ENV` only; no database is required for liveness.
- Produces: `create_app() -> FastAPI`, GET `/health/live`, GET `/health/ready`, `health_payload() -> dict[str, str]`, and console command `familycare-worker`.

- [ ] **Step 1: Define the uv workspace and package metadata**

Root `pyproject.toml` declares Python `>=3.14,<3.15`, workspace members `apps/api` and `workers/analyzer`, and dev dependencies `httpx==0.28.1`, `mypy==2.3.1`, `pytest==9.1.1`, `ruff==0.16.4`. Configure Ruff for Python 3.14 with line length 100 and rules `E`, `F`, `I`, `UP`, `B`, `SIM`; configure mypy strict mode for both `src` trees.

API runtime dependencies are `fastapi==0.141.1`, `pydantic==2.13.4`, and `uvicorn[standard]==0.52.4`. Worker initially has no third-party runtime dependency. Both packages use a pinned Hatchling `>=1.28,<2` build backend and `src` layouts.

- [ ] **Step 2: Write failing API and Worker tests**

API assertions:

```python
response = client.get("/health/live")
assert response.status_code == 200
assert response.json() == {"service": "api", "status": "ok", "version": "0.0.0"}
assert client.get("/health/ready").json()["status"] == "ready"
```

Worker assertions:

```python
assert health_payload() == {"service": "analyzer", "status": "ok", "version": "0.0.0"}
assert main([]) == 0
```

- [ ] **Step 3: Lock dependencies and confirm the expected import failures**

Run:

```bash
uv lock
uv sync --all-packages --group dev
uv run pytest apps/api/tests workers/analyzer/tests -q
```

Expected: tests fail because `familycare_api.main` and `familycare_worker.health` are absent.

- [ ] **Step 4: Implement minimal health contracts**

Use a frozen Pydantic model for API responses:

```python
class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    service: Literal["api"] = "api"
    status: Literal["ok", "ready"]
    version: str = "0.0.0"
```

`create_app()` sets docs at `/docs`, disables no security controls beyond framework defaults, and registers only health routes. Worker `main(argv: Sequence[str] | None = None) -> int` prints sorted JSON from `health_payload()` and exits `0`; it performs no polling or document access in Foundation.

- [ ] **Step 5: Run Python format, lint, type, and unit checks**

Run serially:

```bash
uv run ruff format --check apps/api workers/analyzer
uv run ruff check apps/api workers/analyzer
uv run mypy apps/api/src workers/analyzer/src
uv run pytest apps/api/tests workers/analyzer/tests -q
```

Expected: all commands exit `0`, with four health endpoint/command tests passing.

- [ ] **Step 6: Commit the Python workspace**

```bash
git add .python-version pyproject.toml uv.lock apps/api workers/analyzer
git commit -m "feat: add API and analyzer service shells"
```

---

### Task 5: Contracts, synthetic fixtures, and migration baseline

**Files:**
- Create: `packages/contracts/README.md`
- Create: `packages/contracts/openapi/familycare.v1.json`
- Create: `packages/contracts/schemas/analysis-job.v1.schema.json`
- Create: `packages/contracts/examples/analysis-job.v1.json`
- Create: `fixtures/synthetic/README.md`
- Create: `fixtures/synthetic/family.json`
- Create: `scripts/check_contracts.py`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/migrations/env.py`
- Create: `apps/api/migrations/script.py.mako`
- Create: `apps/api/migrations/versions/0001_foundation.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/src/familycare_api/health.py`
- Modify: `apps/api/src/familycare_api/main.py`
- Modify: `apps/api/tests/test_health.py`
- Modify: `workers/analyzer/src/familycare_worker/health.py`
- Modify: `workers/analyzer/tests/test_health.py`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `familycare_api.main.create_app`, `FAMILYCARE_DATABASE_URL`, JSON Schema draft 2020-12.
- Produces: deterministic OpenAPI JSON, `analysis-job.v1` envelope, `alembic upgrade head` entrypoint.

- [ ] **Step 1: Add contract checks before contract artifacts**

`scripts/check_contracts.py` must:

1. serialize `create_app().openapi()` with `indent=2`, `sort_keys=True`, and a final newline;
2. compare it byte-for-byte with `packages/contracts/openapi/familycare.v1.json`;
3. load the job schema and example;
4. assert required keys, fixed `schema_version == "1"`, UUID-shaped `job_id`, and `document_id` beginning with `synthetic-` without adding a JSON Schema runtime dependency.

- [ ] **Step 2: Run the checker and confirm missing-artifact failure**

Run: `uv run python scripts/check_contracts.py`

Expected: exit `1` naming missing `packages/contracts/openapi/familycare.v1.json`.

- [ ] **Step 3: Add deterministic OpenAPI and synthetic contracts**

Generate OpenAPI from the app, then create an analysis job schema with required properties:

```json
{
  "schema_version": "1",
  "job_id": "00000000-0000-4000-8000-000000000001",
  "document_id": "synthetic-policy-001",
  "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

`fixtures/synthetic/family.json` uses only `family-member-a` and `family-member-b` IDs and display names `Family Member A` and `Family Member B`. Its README states the fixture was authored from scratch and is not a redaction of a real policy.

- [ ] **Step 4: Add the Alembic baseline**

Add `alembic==1.19.1`, `psycopg[binary]==3.3.4`, and `sqlalchemy==2.0.52` to the API, and `psycopg[binary]==3.3.4` to the Worker. `env.py` reads only `FAMILYCARE_DATABASE_URL`; if absent, it exits with `FAMILYCARE_DATABASE_URL is required for migrations`. Revision `0001_foundation` has no domain tables; applying it creates Alembic's version metadata and establishes an intentional pre-domain baseline. Its downgrade is also empty.

Extend API readiness with an injected `Callable[[], bool]`: the default probe executes `SELECT 1`, readiness returns `200` with `ready` when true and `503` with `unavailable` when false. Extend Worker health with the same true/false probe behavior and exit `1` on unavailable. Unit tests inject probes; integration checks use PostgreSQL 18.

- [ ] **Step 5: Validate contracts and migration against PostgreSQL 18**

Run:

```bash
uv lock
uv sync --all-packages --group dev
uv run python scripts/check_contracts.py
docker run --rm -d --name familycare-plan-postgres -e POSTGRES_PASSWORD=ci-only-password -p 55432:5432 postgres:18.6-alpine
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres uv run alembic -c apps/api/alembic.ini upgrade head
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres uv run alembic -c apps/api/alembic.ini current
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres uv run pytest apps/api/tests workers/analyzer/tests -m integration -q
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres uv run alembic -c apps/api/alembic.ini downgrade base
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres uv run alembic -c apps/api/alembic.ini upgrade head
docker stop familycare-plan-postgres
```

Expected: contract checker exits `0`; Alembic reports revision `0001_foundation (head)`. If the fixed port is occupied, use a validated unused port and record it in the verification report rather than stopping another process.

- [ ] **Step 6: Commit contracts and migration baseline**

```bash
git add packages/contracts fixtures/synthetic scripts/check_contracts.py apps/api/alembic.ini apps/api/migrations apps/api/pyproject.toml uv.lock
git commit -m "feat: add contracts and migration baseline"
```

---

### Task 6: Local Compose environment and unprivileged containers

**Files:**
- Create: `Makefile`
- Create: `infra/compose/compose.yaml`
- Create: `infra/containers/web.Dockerfile`
- Create: `infra/containers/api.Dockerfile`
- Create: `infra/containers/worker.Dockerfile`
- Create: `infra/containers/nginx.conf`
- Create: `scripts/check_containers.py`
- Modify: `workers/analyzer/src/familycare_worker/__main__.py`
- Modify: `workers/analyzer/tests/test_health.py`

**Interfaces:**
- Consumes: `.env`, locked pnpm/uv dependencies, local source trees.
- Produces: services `db`, `api`, `worker`, `web`; images `familycare-web`, `familycare-api`, `familycare-worker`; Make targets `setup`, `check`, `up`, `down`, `build`.

- [ ] **Step 1: Write container-definition checks first**

`scripts/check_containers.py` must inspect Dockerfiles as text and fail unless each final stage contains a non-root `USER`, exact runtime major versions, and no `COPY . .`. It must run `docker compose --env-file .env.example -f infra/compose/compose.yaml config --quiet` and require the four service names.

- [ ] **Step 2: Run the checker and confirm missing Dockerfile failure**

Run: `uv run python scripts/check_containers.py`

Expected: exit `1` identifying `infra/containers/web.Dockerfile` as missing.

- [ ] **Step 3: Create Web, API, and Worker Dockerfiles**

- Web builder uses `node:24.19.0-alpine`, Corepack pnpm 11.22.0, frozen lockfile, and `pnpm web:build`; runtime uses an exact `nginxinc/nginx-unprivileged` 1.29 Alpine tag and `USER 101`.
- API builder and runtime use `python:3.14.7-slim`; copy uv 0.12.5 from the official uv image; sync only API runtime dependencies; runtime uses a numeric non-root UID and starts Uvicorn on port 8000.
- Worker uses the same Python and uv versions, installs only Worker runtime dependencies, uses a numeric non-root UID, and runs a Foundation idle process with signal-aware shutdown plus a separate `--health` command.
- No Dockerfile copies `.env`, fixture PDFs, `.git`, or the repository root wholesale.

- [ ] **Step 4: Extend Worker tests for process modes**

Add tests that `main(["--health"])` prints one health JSON line and returns `0`, while an injected stop event makes `run_idle(stop_event, interval_seconds=0)` return without accessing the filesystem or network.

- [ ] **Step 5: Create Compose and Make orchestration**

Compose requirements:

- PostgreSQL `18.6-alpine`, named development volume, healthcheck `pg_isready`.
- API waits for healthy DB and `/health/ready` verifies `SELECT 1`.
- Worker waits for healthy DB, its health command verifies `SELECT 1`, and its main process remains idle without processing documents.
- Web waits for API and exposes port 8080.
- Password comes from `.env`; compose has no committed production default.
- Only the DB volume persists; no document directory is mounted by default.

Make commands call package-native commands and do not install global tools.

- [ ] **Step 6: Validate and build serially**

Run:

```bash
uv run pytest workers/analyzer/tests -q
uv run python scripts/check_containers.py
docker compose --env-file .env.example -f infra/compose/compose.yaml build web
docker compose --env-file .env.example -f infra/compose/compose.yaml build api
docker compose --env-file .env.example -f infra/compose/compose.yaml build worker
```

Expected: tests and definition checks pass; all three builds exit `0`. Build one image at a time to avoid WSL memory pressure.

- [ ] **Step 7: Run and inspect non-root containers**

Run each image with `--entrypoint id` and assert UID is not `0`. Then start Compose, request Web and API health endpoints, run Worker health, and stop with `docker compose ... down` without `--volumes` so local state is recoverable.

- [ ] **Step 8: Commit local infrastructure**

```bash
git add Makefile infra scripts/check_containers.py workers/analyzer
git commit -m "build: add local containers and compose"
```

---

### Task 7: CI, dependency updates, and pull-request policy

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/dependabot.yml`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `scripts/check_workflows.py`
- Create: `scripts/check_git_conventions.py`
- Create: `scripts/tests/test_git_conventions.py`

**Interfaces:**
- Consumes: all local check commands and immutable GitHub Action SHAs.
- Produces: PR/main jobs `repository-safety`, `web`, `python`, `integration`, `containers`; `check_workflows.py` and `check_git_conventions.py` validation entrypoints.

- [ ] **Step 1: Write workflow policy checks before workflow files**

`check_workflows.py` must fail unless:

- each `uses:` value ends in a 40-character lowercase SHA and has a release comment;
- workflow top-level permissions are `contents: read`;
- CI runs on `pull_request` and pushes to `main`;
- no `pull_request_target` trigger exists;
- CI does not reference `${{ secrets.* }}`;
- container jobs use build-only mode and never `push: true`.

Write `test_git_conventions.py` first with accepted branch names `main`, `build/project-foundation`, and `feat/policy-ledger`, plus rejected names `feature/foo`, `Feature/foo`, and `build/project_foundation`. Accept commit subjects `docs: establish project governance` and `feat(api): add health endpoint`; reject missing types, title-case types, trailing periods, and subjects longer than 72 characters.

- [ ] **Step 2: Run the checker and confirm missing-workflow failure**

Run: `uv run python scripts/check_workflows.py`

Expected: exit `1` identifying `.github/workflows/ci.yml` as missing.

- [ ] **Step 3: Create least-privilege CI**

Pin these actions exactly:

```text
actions/checkout v7.0.1 3d3c42e5aac5ba805825da76410c181273ba90b1
actions/setup-node v7.0.0 820762786026740c76f36085b0efc47a31fe5020
astral-sh/setup-uv v10.0.1 20cfd1bf945f4377ade1205e4dbc17946fc9a30d
pnpm/action-setup v6.0.10 0977fd99725f1db4007ccb2928dbb4e90d06cc86
docker/setup-buildx-action v4.3.0 37fe631027851001ddb9b187196cc803df7f5f0e
docker/build-push-action v7.3.0 53b7df96c91f9c12dcc8a07bcb9ccacbed38856a
gitleaks/gitleaks-action v3.0.0 e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e
```

CI behavior:

- safety job runs documentation, repository safety, gitleaks, workflow policy, and `git diff --exit-code` after generators;
- Web job installs pnpm 11.22.0 with frozen lockfile and runs all five Web checks;
- Python job syncs frozen uv lock and runs format, lint, mypy, and pytest;
- integration job uses a PostgreSQL 18 service and runs Alembic plus contract checks;
- containers job builds each Dockerfile with `push: false` in a sequential matrix (`max-parallel: 1`).
- safety job validates `<type>/<kebab-case>` for PR branches and every PR commit subject against Conventional Commits.

- [ ] **Step 4: Add Dependabot and PR privacy checklist**

Dependabot runs weekly for npm at `/`, pip at `/`, Docker for each Dockerfile directory, and GitHub Actions at `/`. Group development dependencies separately from runtime updates.

PR checklist requires confirmation that fixtures are synthetic, no real document or derived text is present, no secret or identifier is present, logs are sanitized, tests are listed, and inaccessible external checks are reported as unverified.

- [ ] **Step 5: Validate local equivalence and workflow policy**

Run:

```bash
uv run python scripts/check_documentation.py
uv run python scripts/check_repository_safety.py
uv run python scripts/check_contracts.py
uv run python scripts/check_containers.py
uv run python scripts/check_workflows.py
uv run python scripts/check_git_conventions.py --branch build/project-foundation --range main..HEAD
uv run pytest scripts/tests/test_git_conventions.py -q
git diff --check
```

Expected: all commands exit `0` and workflow policy reports both action pinning and least-privilege checks successful.

- [ ] **Step 6: Commit CI and repository metadata**

```bash
git add .github scripts/check_workflows.py scripts/check_git_conventions.py scripts/tests/test_git_conventions.py
git commit -m "ci: validate project foundation"
```

---

### Task 8: GHCR semantic-tag release workflow

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `scripts/check_workflows.py`
- Modify: `README.md`
- Modify: `docs/guide.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: a `vMAJOR.MINOR.PATCH` tag whose commit passes CI and the three Dockerfiles.
- Produces: `ghcr.io/${github.repository}-web`, `-api`, and `-worker` images tagged with semantic version and commit SHA.

- [ ] **Step 1: Extend policy tests for release boundaries**

Require release workflow to:

- trigger only on `v[0-9]+.[0-9]+.[0-9]+` tags;
- define top-level `contents: read` and job-only `packages: write`;
- use no repository secrets other than the automatic `GITHUB_TOKEN` context;
- validate before login or push;
- publish exactly `web`, `api`, and `worker` image suffixes;
- contain no Cloud Run, `gcloud`, SSH, Kubernetes, or production deployment command.

- [ ] **Step 2: Run policy checks and confirm release workflow failure**

Run: `uv run python scripts/check_workflows.py`

Expected: exit `1` because `.github/workflows/release.yml` is missing.

- [ ] **Step 3: Create release validation and publish jobs**

Use the immutable action pins from Task 7 plus:

```text
docker/login-action v4.6.0 dbcb813823bdd20940b903addbd779551569679f
docker/metadata-action v6.2.0 dc802804100637a589fabce1cb79ff13a1411302
```

Workflow order:

1. `validate-tag` parses `${GITHUB_REF_NAME}` with `^v[0-9]+\.[0-9]+\.[0-9]+$`.
2. `validate-foundation` repeats safety, Web, Python, contracts, migration, and build-only checks.
3. `publish` uses a matrix with explicit Dockerfile and image suffix for Web/API/Worker, logs into `ghcr.io` with `github.actor` and `GITHUB_TOKEN`, and pushes semantic and `sha-<12 chars>` tags.
4. No `latest` tag is created before version `1.0.0`; later policy change requires an ADR.

- [ ] **Step 4: Document release use and limitations**

README and guide must state that a Git tag is irreversible public release metadata, tag creation is a deliberate user action, GHCR success is not deployment success, and Cloud Run remains out of scope. CHANGELOG records the release automation under Unreleased/Added.

- [ ] **Step 5: Validate the release workflow without pushing a tag**

Run:

```bash
uv run python scripts/check_workflows.py
git diff --check
git status --short
```

Expected: workflow policy exits `0`; no tag is created and no image is pushed during local validation.

- [ ] **Step 6: Commit GHCR release automation**

```bash
git add .github/workflows/release.yml scripts/check_workflows.py README.md docs/guide.md CHANGELOG.md
git commit -m "ci: publish tagged images to GHCR"
```

---

### Task 9: Full Foundation verification and handoff

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/plan/001-project-foundation.md`
- Modify only if verification finds a documented discrepancy: affected implementation or documentation files.

**Interfaces:**
- Consumes: all Foundation files and local toolchains.
- Produces: fresh evidence for repository safety, tests, builds, container execution, Git cleanliness, and documented external verification gaps.

- [ ] **Step 1: Run repository and documentation checks**

```bash
python3 scripts/check_documentation.py
python3 -m unittest scripts.tests.test_repository_safety -v
python3 scripts/check_repository_safety.py
uv run python scripts/check_contracts.py
uv run python scripts/check_containers.py
uv run python scripts/check_workflows.py
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 2: Run Web checks serially**

```bash
corepack pnpm@11.22.0 web:format
corepack pnpm@11.22.0 web:lint
corepack pnpm@11.22.0 web:typecheck
corepack pnpm@11.22.0 web:test
corepack pnpm@11.22.0 web:build
```

Expected: all commands exit `0` with no skipped test suite.

- [ ] **Step 3: Run Python checks serially**

```bash
uv run ruff format --check apps/api workers/analyzer scripts
uv run ruff check apps/api workers/analyzer scripts
uv run mypy apps/api/src workers/analyzer/src scripts
uv run pytest apps/api/tests workers/analyzer/tests -q
```

Expected: all commands exit `0` and all collected tests pass.

- [ ] **Step 4: Run migration and containers serially**

Start only repository-owned services after confirming names and ports are unused. Apply the migration, build each image one at a time, start Compose, verify API/Web/Worker health, inspect runtime UIDs, then stop Compose without deleting the named database volume.

Expected: migration head is `0001_foundation`, HTTP health checks return `200`, Worker health exits `0`, and all runtime UIDs are non-zero.

- [ ] **Step 5: Audit Git contents and history**

Run:

```bash
git status --short
git ls-files -z | python3 scripts/check_repository_safety.py --stdin0
git log --oneline --decorate --stat
```

Expected: no uncommitted files except the plan status update being prepared, safety scan exits `0`, and the history contains only synthetic/public artifacts.

- [ ] **Step 6: Record actual verification boundaries**

Update the Foundation plan status to complete only for checks actually run. Record these as explicitly unverified unless separately executed by the user or GitHub:

- GitHub-hosted CI run
- GHCR push from a real semantic-version tag
- Windows browser/PWA installation
- actual insurance document ingestion
- Google Drive, external AI, authentication, and Cloud Run

- [ ] **Step 7: Commit verification record if documentation changed**

```bash
git add CHANGELOG.md docs/plan/001-project-foundation.md
git commit -m "docs: record foundation verification"
```

Do not create or push a Git tag. The user authorized branch pushes, PR creation, CI follow-up, and merge for this Foundation work; that authorization does not include a release tag.

---

### Task 10: Pull request, GitHub Actions verification, and merge

**Files:**
- Modify only when CI findings require a scoped correction: files implicated by the failing check.
- No release tag or production configuration is created.

**Interfaces:**
- Consumes: clean `build/project-foundation`, configured `origin`, authenticated GitHub CLI, and all fresh local verification evidence.
- Produces: remote base branch `main`, remote feature branch, one reviewed pull request, successful GitHub Actions checks, and a merged remote `main`.

- [ ] **Step 1: Confirm exact refs and GitHub authority before remote writes**

Run:

```bash
git status --short --branch
git log --oneline --decorate --graph --all
git remote -v
git ls-remote --heads origin main build/project-foundation
gh auth status
```

Expected: local working tree is clean, `origin` is `https://github.com/jihoon22-lee/family-care.git`, no conflicting remote feature branch exists, and GitHub CLI is authenticated with repository write access.

- [ ] **Step 2: Seed the empty remote base branch with the approved design commit**

Only when `git ls-remote --heads origin main` is empty, verify local `main` points to the reviewed design-only root commit and push that ref:

```bash
git show --stat --oneline main
git diff --exit-code main -- docs/design/project-foundation.md
git push origin main:main
```

Expected: remote `main` is created with only `docs/design/project-foundation.md`. If a remote `main` appears before this step, fetch it and reconcile without force-pushing.

- [ ] **Step 3: Push the feature branch and create the pull request**

```bash
git push -u origin build/project-foundation
gh pr create \
  --base main \
  --head build/project-foundation \
  --title "build: establish FamilyCare project foundation" \
  --body-file /tmp/familycare-pr-body.md
```

The PR body records purpose, major files, runtime/API contracts, privacy boundary, exact local checks and results, unverified GHCR tag publishing, and the explicit absence of real insurance data.

- [ ] **Step 4: Watch GitHub Actions through a terminal result**

Run:

```bash
gh pr checks --watch --interval 10
gh pr view --json number,url,state,mergeable,statusCheckRollup
```

Expected: every required check reaches `SUCCESS`; the PR is open and mergeable. A pending, skipped-required, cancelled, timed-out, or missing check is not a pass.

- [ ] **Step 5: Fix any CI-only failure with evidence**

For each failure, obtain its numeric database ID with `gh run list --branch build/project-foundation --limit 10`, inspect it with `gh run view RUN_DATABASE_ID --log-failed` after replacing `RUN_DATABASE_ID` with that returned integer, reproduce the failing command locally when possible, add or adjust a regression check, rerun the full affected validation group, commit the scoped fix, push, and repeat Step 4. Do not weaken privacy checks, remove tests, or mark a failed check optional merely to make the PR green.

- [ ] **Step 6: Merge only after all checks succeed**

Run:

```bash
gh pr merge --merge --delete-branch
```

Expected: GitHub reports the PR merged through a merge commit. If branch protection or repository policy blocks merge, report the exact policy instead of bypassing it with an administrator override.

- [ ] **Step 7: Verify post-merge remote state**

Run:

```bash
gh pr view --json number,url,state,mergedAt,mergeCommit
git fetch origin main --prune
git log --oneline --decorate -5 origin/main
git ls-remote --heads origin build/project-foundation
```

Expected: PR state is `MERGED`, `mergedAt` and `mergeCommit` are populated, `origin/main` contains the Foundation history, and the remote feature branch is absent. Report the PR URL, merge commit, CI run result, and all intentionally unexecuted checks.

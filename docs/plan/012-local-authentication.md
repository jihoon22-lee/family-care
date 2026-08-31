# Local Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Complete — PR #23 merged as `135a569d4360d0c6cd3e6bc7894d64394601fbf3`; private
Tailscale HTTPS login/navigation/logout acceptance passed, while Windows/mobile remain unverified.

**Goal:** Replace the unauthenticated private-use boundary with two equal local administrators, server-side sessions, CSRF protection, and safe administrative provisioning.

**Architecture:** The API owns `AppUser`, `AppSession`, `HouseholdSpace` scope resolution, and the authentication routes. Passwords are verified with Argon2id, opaque cookies carry only a random session token, and PostgreSQL stores only hashes and lifecycle metadata. The Web app keeps no bearer or session token in persistent browser storage; all business routes receive the server-derived `AuthContext`.

**Tech Stack:** Python 3.14, FastAPI 0.141.1, Pydantic 2.13.4, psycopg 3.3.4, PostgreSQL 18, Alembic 1.19.1, `argon2-cffi` pinned in `apps/api/pyproject.toml`, React 19, TypeScript 6, Vitest 4, and the existing OpenAPI generator.

**Spec:** `docs/design/authentication.md`, `docs/design/data-model.md`, `docs/design/security-privacy.md`, `docs/design/v0.1-product.md`, `docs/plan/003-v0.1-implementation-index.md`

## Global Constraints

- Migration `0011_local_authentication.py` has `down_revision = "0010_claim_workflow"` and does not rewrite any policy, event, decision, calculation, claim, or Phase 1 table.
- Actual insurance, medical, identity, password, session, archive, OCR, provider, and local-path values never enter Git, fixtures, logs, responses, or CI artifacts.
- Public tests use only from-scratch synthetic values such as `Admin A`, `Family Member A`, and `synthetic-policy-001`.
- Public CI never calls OpenAI, Google Drive, Tailscale, or a real private path.
- AI remains non-authoritative; only deterministic domain code may produce `MATCH`, `NO_MATCH`, `UNKNOWN`, or money.
- Missing facts, Evidence, contract state, renewal state, or rule support remain `UNKNOWN`, never `NO_MATCH`, zero, or an exception.
- The existing Phase 1 v1 JSON Schemas, eight ingestion tables, synthetic route gate, password-free `AnalysisJob` payload, and generated document contracts remain compatible.
- The existing synthetic document routes stay default-disabled; this plan adds authenticated v0.1 routes without globally wrapping the historical test-only router.
- Every state-changing authenticated request checks same-origin and a CSRF token; every authenticated response uses `Cache-Control: no-store`.
- Passwords and session token originals never enter arguments, environment variables, database rows, logs, browser storage, or Git.
- The Root PR gate in `docs/plan/003-v0.1-implementation-index.md` is run once on the complete branch immediately before push, followed by focused post-merge verification.

---

## File Responsibility Map

~~~text
apps/api/migrations/versions/0011_local_authentication.py
  PostgreSQL tables and constraints for AppUser and AppSession.

apps/api/src/familycare_api/identity/__init__.py
apps/api/src/familycare_api/identity/password.py
  Argon2id hashing, verification, and parameter-upgrade decisions.
apps/api/src/familycare_api/identity/sessions.py
  Opaque token creation, hash lookup, expiry, rotation, and revocation.
apps/api/src/familycare_api/identity/csrf.py
  Same-origin and per-session CSRF token checks.
apps/api/src/familycare_api/identity/context.py
  Server-derived AuthContext and HouseholdSpace scope dependency.
apps/api/src/familycare_api/identity/rate_limit.py
  Bounded in-process login and password verification throttling.
apps/api/src/familycare_api/identity/router.py
  Login, logout, current-user, CSRF, reauthentication, and device sessions.
apps/api/src/familycare_api/identity/cli.py
  TTY/stdin-only account provisioning and deactivation.

apps/api/tests/test_identity_password.py
apps/api/tests/test_identity_sessions.py
apps/api/tests/test_auth_routes.py
apps/api/tests/test_auth_scope.py
apps/api/tests/test_admin_cli.py
apps/api/tests/test_auth_database.py
  Unit, HTTP, scope, CLI, and PostgreSQL integration evidence.

apps/web/src/features/identity/authApi.ts
apps/web/src/features/identity/authStore.ts
apps/web/src/features/identity/LoginPage.tsx
apps/web/src/features/identity/SessionPage.tsx
  Cookie-based login UI and in-memory session state.
apps/web/src/features/identity/*.test.tsx
  Browser-storage, expiry, keyboard, and logout tests.

packages/contracts/openapi/familycare.v1.json
scripts/check_contracts.py
  Generated authenticated route contract and drift checks.
~~~

## Domain and HTTP Interfaces

The identity module exposes these exact Python shapes to later business modules:

~~~python
@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    household_space_id: UUID
    session_id: UUID
    needs_reauthentication: bool


@dataclass(frozen=True)
class IssuedSession:
    session_id: UUID
    raw_token: str
    csrf_token: str
    expires_at: datetime


class SessionService(Protocol):
    def issue(self, user_id: UUID, device_label: str, now: datetime) -> IssuedSession: ...
    def resolve(self, raw_token: str, now: datetime) -> AuthContext | None: ...
    def rotate(self, session_id: UUID, now: datetime) -> IssuedSession: ...
    def revoke(self, session_id: UUID, *, actor_id: UUID, now: datetime) -> None: ...


class CsrfService(Protocol):
    def issue(self, session_id: UUID) -> str: ...
    def validate(self, session_id: UUID, token: str) -> None: ...
~~~

The public route set is deliberately narrow:

~~~text
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
GET  /api/v1/auth/csrf
POST /api/v1/auth/reauthenticate
POST /api/v1/auth/password
GET  /api/v1/auth/sessions
POST /api/v1/auth/sessions/{session_id}/revoke
~~~

The session cookie is host-only, `Secure`, `HttpOnly`, `SameSite=Strict`, and named `familycare_session`. Login failure responses do not distinguish an unknown username from an inactive account or a wrong password.

## Task 1: Create the identity schema and Argon2id provisioning boundary

**Files:**

- Create: `apps/api/migrations/versions/0011_local_authentication.py`
- Create: `apps/api/src/familycare_api/identity/__init__.py`
- Create: `apps/api/src/familycare_api/identity/password.py`
- Create: `apps/api/src/familycare_api/identity/cli.py`
- Modify: `apps/api/pyproject.toml`
- Test: `apps/api/tests/test_identity_password.py`
- Test: `apps/api/tests/test_admin_cli.py`

**Interfaces:**

- Consumes: the existing `household_spaces` table created by the merged policy-ledger migration and the API database URL.
- Produces: `PasswordHasher.hash`, `PasswordHasher.verify`, `AdminProvisioner.initialize`, `AdminProvisioner.create`, `AdminProvisioner.set_password`, `AdminProvisioner.disable`, and the `familycare-admin init|create|set-password|disable` commands. The post-merge first-run correction makes `init` the only CLI path that atomically creates the unseeded sole HouseholdSpace and first administrator; `create` remains the optional second-admin path.
- Database tables: `app_users` with UUID, household FK, normalized username, display name, Argon2id hash, active flag, timestamps, and deactivated timestamp; `app_sessions` is created in this migration with its full shape for later tasks.
- The two-admin limit is enforced by locking the single HouseholdSpace row, counting active users, and rejecting a third active account with `ADMIN_LIMIT_REACHED`.

- [x] **Step 1: Write the failing password and provisioning tests**

~~~python
def test_password_hash_is_argon2id_and_never_round_trips() -> None:
    hasher = PasswordHasher()
    encoded = hasher.hash("synthetic-admin-password")

    assert encoded.startswith("$argon2id$")
    assert hasher.verify(encoded, "synthetic-admin-password") is True
    assert "synthetic-admin-password" not in encoded


def test_third_active_admin_is_rejected_without_persisting_password(
    provisioner: AdminProvisioner,
) -> None:
    provisioner.create("admin-a", "synthetic-password-a", "Admin A")
    provisioner.create("admin-b", "synthetic-password-b", "Admin B")

    with pytest.raises(AdminProvisioningError, match="ADMIN_LIMIT_REACHED"):
        provisioner.create("admin-c", "synthetic-password-c", "Admin C")

    assert provisioner.raw_database_rows() == 2
    assert all("synthetic-password" not in row.password_hash for row in provisioner.rows())
~~~

- [x] **Step 2: Run the RED tests and record the expected missing module failure**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_identity_password.py \
  apps/api/tests/test_admin_cli.py -q
~~~

Expected: FAIL because `familycare_api.identity` and the `familycare-admin` entrypoint do not yet exist.

- [x] **Step 3: Add the migration, Argon2id service, and safe CLI**

Use explicit Argon2id parameters and never accept a password option:

~~~python
_HASHER = argon2.PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
    type=argon2.Type.ID,
)


def create_admin_from_tty(database_url: str, username: str) -> None:
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not hmac.compare_digest(password, confirmation):
        raise AdminProvisioningError("PASSWORD_MISMATCH")
    AdminProvisioner(database_url).create(username, password, display_name=username)
    del password, confirmation
~~~

`set-password` uses the same double `getpass` flow, accepts only the username as a non-secret argument, replaces the hash transactionally, and revokes every existing session for that user. `disable` also revokes all sessions and never deletes HouseholdSpace business data. The migration must lock the HouseholdSpace row during the active-account count and must not create a public bootstrap endpoint. Add `[project.scripts] familycare-admin = "familycare_api.identity.cli:main"` and pin `argon2-cffi` in the API package.

Fresh migrations intentionally contain no HouseholdSpace row. The private-runtime acceptance correction adds `familycare-admin init --space-key ... --household-name ... --username ... --display-name ...`; its password still comes only from TTY/stdin. A transaction-scoped PostgreSQL advisory lock protects the empty-table concurrency case, any existing row including a soft-deleted row rejects re-initialization, and household plus first-admin inserts commit or roll back together. This is administrative CLI initialization, not a public Web bootstrap endpoint.

- [x] **Step 4: Run the GREEN unit tests and migration checks**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_identity_password.py \
  apps/api/tests/test_admin_cli.py -q
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini current
~~~

Expected: password/provisioning tests pass, the new revision is head, and no raw password appears in captured CLI output or fake rows.

- [x] **Step 5: Commit the schema and provisioning slice**

~~~bash
git add apps/api/migrations/versions/0011_local_authentication.py \
  apps/api/src/familycare_api/identity/__init__.py \
  apps/api/src/familycare_api/identity/password.py \
  apps/api/src/familycare_api/identity/cli.py \
  apps/api/pyproject.toml \
  apps/api/tests/test_identity_password.py \
  apps/api/tests/test_admin_cli.py
git commit -m "feat(auth): add local admin provisioning"
~~~

## Task 2: Implement hashed sessions, CSRF, expiry, and protected API routes

**Files:**

- Create: `apps/api/src/familycare_api/identity/sessions.py`
- Create: `apps/api/src/familycare_api/identity/csrf.py`
- Create: `apps/api/src/familycare_api/identity/context.py`
- Create: `apps/api/src/familycare_api/identity/rate_limit.py`
- Create: `apps/api/src/familycare_api/identity/router.py`
- Modify: `apps/api/src/familycare_api/main.py`
- Modify: `apps/api/src/familycare_api/errors.py`
- Test: `apps/api/tests/test_identity_sessions.py`
- Test: `apps/api/tests/test_auth_routes.py`
- Test: `apps/api/tests/test_auth_scope.py`

**Interfaces:**

- Consumes: `app_users`, `app_sessions`, and the `AuthContext` from Task 1.
- Produces: `resolve_auth_context(request)`, `require_household_context`, and the eight `/api/v1/auth/*` routes.
- Session expiry is `min(last_seen_at + 7 days, created_at + 30 days)`; expiry and revocation return the same unauthenticated response.
- Login success rotates to a new opaque session. Logout and revocation invalidate the selected session; password change invalidates every session for that user. Password change and revoking another device require `reauthenticated_at` within the configured recent window.
- Every Phase 2–6 business router is registered with `Depends(require_household_context)` and reads the resulting server scope. Health routes and the default-disabled synthetic Phase 1 router remain outside this authenticated business-router group.

- [x] **Step 1: Write expiry, fixation, CSRF, and object-scope tests**

~~~python
def test_session_expires_at_inactivity_boundary(session_service, clock) -> None:
    issued = session_service.issue(USER_ID, "synthetic-device", clock.at("2026-01-01T00:00:00Z"))

    assert session_service.resolve(issued.raw_token, clock.at("2026-01-07T00:00:00Z")) is not None
    assert session_service.resolve(issued.raw_token, clock.at("2026-01-07T00:00:00.001Z")) is None


def test_client_household_id_is_ignored(authenticated_client) -> None:
    response = authenticated_client.get(
        "/api/v1/family-members",
        params={"household_space_id": "00000000-0000-4000-8000-000000000099"},
    )

    assert response.status_code == 200
    assert response.json()["household_space_id"] == SYNTHETIC_HOUSEHOLD_ID


def test_state_change_without_csrf_is_rejected(authenticated_client) -> None:
    response = authenticated_client.post(
        "/api/v1/auth/sessions/00000000-0000-4000-8000-000000000002/revoke"
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "CSRF_REQUIRED"


def test_every_business_route_requires_a_server_session(app) -> None:
    assert_business_routes_are_protected(
        app,
        exempt_prefixes=("/health/", "/api/v1/auth/"),
    )
~~~

- [x] **Step 2: Run the RED route tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_identity_sessions.py \
  apps/api/tests/test_auth_routes.py \
  apps/api/tests/test_auth_scope.py -q
~~~

Expected: FAIL because the session dependency, CSRF check, and auth router are absent.

- [x] **Step 3: Implement hash-only sessions and protected routes**

~~~python
def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def resolve_auth_context(request: Request) -> AuthContext:
    raw = request.cookies.get("familycare_session")
    context = session_service.resolve(raw or "", utc_now())
    if context is None:
        raise Unauthenticated
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        same_origin.validate(request)
        csrf.validate(context.session_id, request.headers.get("X-CSRF-Token", ""))
    return context
~~~

Set the cookie with `secure=True`, `httponly=True`, `samesite="strict"`, `path="/"`, and no domain. Use `response.headers["Cache-Control"] = "no-store"` for every auth response. Add a bounded per-username/IP limiter that returns one stable `AUTH_RATE_LIMITED` code without revealing account existence.

Register policy, review, clause, rule, decision, receipt, result, and claim routers through one explicit business-router list with the auth dependency. Implement `POST /auth/password` with current-session reauthentication, Argon2id policy validation, hash replacement in one transaction, and revocation of all of the user's sessions. Do not add email reset, recovery questions, or a public bootstrap route.

- [x] **Step 4: Run the GREEN HTTP and static checks**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_identity_sessions.py \
  apps/api/tests/test_auth_routes.py \
  apps/api/tests/test_auth_scope.py -q
TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi
TMPDIR=/tmp uv run python scripts/check_contracts.py
~~~

Expected: login, logout, expiry, CSRF, reauthentication, session revoke, no-store, and server-derived scope tests pass; OpenAPI is regenerated from the authenticated app contract.

- [x] **Step 5: Commit the session and API slice**

~~~bash
git add apps/api/src/familycare_api/identity \
  apps/api/src/familycare_api/main.py \
  apps/api/src/familycare_api/errors.py \
  apps/api/tests/test_identity_sessions.py \
  apps/api/tests/test_auth_routes.py \
  apps/api/tests/test_auth_scope.py \
  packages/contracts/openapi/familycare.v1.json \
  scripts/check_contracts.py
git commit -m "feat(auth): add secure local sessions"
~~~

## Task 3: Add the Web login and device-session boundary

**Files:**

- Create: `apps/web/src/features/identity/authApi.ts`
- Create: `apps/web/src/features/identity/authStore.ts`
- Create: `apps/web/src/features/identity/LoginPage.tsx`
- Create: `apps/web/src/features/identity/SessionPage.tsx`
- Create: `apps/web/src/features/identity/ReauthenticateDialog.tsx`
- Create: `apps/web/src/features/identity/ChangePasswordDialog.tsx`
- Create: `apps/web/src/features/identity/authApi.test.ts`
- Create: `apps/web/src/features/identity/LoginPage.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/styles.css`
- Modify: `apps/web/src/test/setup.ts`

**Interfaces:**

- Consumes: cookie-based auth routes and the in-memory `AuthContext` projection.
- Produces: `login(username, password)`, `logout()`, `loadCurrentUser()`, `loadCsrfToken()`, `reauthenticate(password)`, `changePassword(newPassword)`, `listSessions()`, and `revokeSession(id)`.
- The client uses `credentials: "include"`, `Cache-Control: no-store`, and a module-scoped CSRF token. It never writes session, bearer, password, medical, policy, or Evidence data to localStorage, sessionStorage, IndexedDB, or a service-worker cache.

- [x] **Step 1: Write browser-storage and login-flow tests**

~~~tsx
test("login uses cookies and does not persist credentials", async () => {
  render(<LoginPage />)
  await userEvent.type(screen.getByLabelText("사용자 이름"), "admin-a")
  await userEvent.type(screen.getByLabelText("비밀번호"), "synthetic-password")
  await userEvent.click(screen.getByRole("button", { name: "로그인" }))

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/auth/login",
    expect.objectContaining({ credentials: "include" }),
  )
  expect(window.localStorage.length).toBe(0)
  expect(window.sessionStorage.length).toBe(0)
  expect(document.cookie).toBe("")
})
~~~

- [x] **Step 2: Run the RED Web tests**

Run:

~~~bash
corepack pnpm@11.22.0 --filter @familycare/web test -- \
  apps/web/src/features/identity/authApi.test.ts \
  apps/web/src/features/identity/LoginPage.test.tsx
~~~

Expected: FAIL because the identity feature files and authenticated shell do not exist.

- [x] **Step 3: Implement the no-storage auth client and accessible screens**

~~~ts
let csrfToken: string | null = null;

export async function loadCsrfToken(): Promise<string> {
  const response = await fetch("/api/v1/auth/csrf", {
    credentials: "include",
    cache: "no-store",
  });
  csrfToken = (await response.json()).csrf_token;
  return csrfToken;
}

export function authHeaders(): HeadersInit {
  return csrfToken === null ? {} : { "X-CSRF-Token": csrfToken };
}

export function clearAuthState(): void {
  csrfToken = null;
  authStore.clear();
}
~~~

Keep password values only in controlled component memory and clear them after success, failure, close, unmount, logout, or session expiry. Revoking another device and changing a password first open the keyboard-safe reauthentication dialog. A successful password change revokes all sessions, clears every in-memory business/auth cache, and returns to login. Use labels, focus restoration, and an explicit unauthenticated loading state.

- [x] **Step 4: Run Web GREEN checks and storage assertions**

Run:

~~~bash
corepack pnpm@11.22.0 --filter @familycare/web test -- \
  apps/web/src/features/identity/authApi.test.ts \
  apps/web/src/features/identity/LoginPage.test.tsx
corepack pnpm@11.22.0 web:check
~~~

Expected: identity tests and the complete Web check pass, with no persistent credential storage.

- [x] **Step 5: Commit the Web slice**

~~~bash
git add apps/web/src/App.tsx apps/web/src/styles.css \
  apps/web/src/features/identity apps/web/src/test/setup.ts
git commit -m "feat(web): add local authentication screens"
~~~

## Task 4: Verify PostgreSQL integration, privacy, and PR readiness

**Files:**

- Test: `apps/api/tests/test_auth_database.py`
- Test: `scripts/tests/test_repository_safety.py`
- Modify: `scripts/check_repository_safety.py` only if the new CLI or identity source needs a narrow code-path exception
- Modify: `docs/guide.md`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes: all identity tables, routes, Web screens, and generated OpenAPI.
- Produces: repeatable migration upgrade/downgrade evidence and documentation for `familycare-admin`, login, session expiry, and the fact that Tailscale does not replace app login.

- [x] **Step 1: Write PostgreSQL and privacy regression tests**

~~~python
@pytest.mark.integration
def test_raw_password_and_session_token_are_absent_from_rows(db) -> None:
    admin = provision_synthetic_admin(db, "admin-a", "synthetic-password")
    issued = login_synthetic_admin(db, admin.username, "synthetic-password")
    rows = fetch_identity_rows(db)

    assert issued.raw_token not in json.dumps(rows, default=str)
    assert "synthetic-password" not in json.dumps(rows, default=str)
    assert all(row["token_hash"] != issued.raw_token for row in rows.sessions)
~~~

- [x] **Step 2: Run the RED integration test against a clean PostgreSQL schema**

Run:

~~~bash
FAMILYCARE_DATABASE_URL=postgresql+psycopg://synthetic_ci:synthetic_only@127.0.0.1:5432/synthetic_ci \
TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_auth_database.py -q
~~~

Expected: FAIL until the migration, row mapping, and session transaction boundaries are present.

- [x] **Step 3: Add guide/changelog evidence without sensitive examples**

Document only synthetic CLI examples and external path shapes, for example:

~~~bash
docker compose run --rm api familycare-admin create --username admin-a
~~~

State that the password is prompted through TTY/stdin, that signup/reset/invite are absent, and that actual private-device acceptance is reported separately.

- [x] **Step 4: Run the complete focused auth gate**

Run serially:

~~~bash
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini downgrade base
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
FAMILYCARE_DATABASE_URL=postgresql+psycopg://synthetic_ci:synthetic_only@127.0.0.1:5432/synthetic_ci \
TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_auth_database.py -q
TMPDIR=/tmp uv run python scripts/check_documentation.py
TMPDIR=/tmp uv run python scripts/check_repository_safety.py
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run ruff format --check .
TMPDIR=/tmp uv run ruff check .
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
git diff --check
~~~

Expected: all checks pass; no password, cookie, session token, actual path, or private document value appears in tracked files or test output.

- [x] **Step 5: Commit the integration/documentation slice and invoke the Root PR gate**

~~~bash
git add apps/api/tests/test_auth_database.py \
  scripts/tests/test_repository_safety.py \
  scripts/check_repository_safety.py docs/guide.md CHANGELOG.md
git commit -m "test(auth): verify local session boundaries"
~~~

Before push, execute the complete Root PR gate from `docs/plan/003-v0.1-implementation-index.md`, review the full diff once, open the PR, wait for all required checks, merge with a merge commit, and run the focused auth tests on post-merge `main`. Record Windows browser, mobile PWA, real private documents, and Tailscale device checks as unverified until separately performed.

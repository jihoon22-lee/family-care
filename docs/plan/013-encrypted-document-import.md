# Encrypted Document Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add an authenticated Web flow for one-FamilyMember document batches that decrypt each PDF only in Worker memory/workspace, archive successful plaintext through per-document AES-GCM with AES-KW-wrapped keys, and never persist a PDF password.

**Architecture:** The authenticated Web lists bounded import-inbox entries as opaque source IDs, creates a one-member batch, prompts once for a password, and shows per-file progress/retry without uploads or browser persistence. The API resolves opaque IDs beneath the configured import root, creates a household-scoped batch, and sends a password through a one-time Unix-domain socket handoff to the Worker. The Worker owns the in-memory batch secret, opens source files through the existing descriptor-safe intake, decrypts encrypted PDFs into a mode-0700/mode-0600 workspace, extracts them, writes an application-encrypted managed archive, and removes every plaintext intermediate before reporting success. PostgreSQL stores batch/item state and archive metadata but never stores the password, archive master key, plaintext PDF, or source absolute path.

**Tech Stack:** Python 3.14, FastAPI 0.141.1, psycopg 3.3.4, SQLAlchemy 2.0.52, PostgreSQL 18, Alembic 1.19.1, cryptography==50.0.0 as a direct Worker dependency, existing pypdf/pdfplumber intake, Unix-domain sockets, Docker Compose volumes, and the generated JSON contract tooling.

**Spec:** docs/design/private-data-runtime.md, docs/design/pdf-ingestion.md, docs/design/authentication.md, docs/design/data-model.md, docs/design/security-privacy.md, docs/design/v0.1-product.md, docs/plan/003-v0.1-implementation-index.md

## Global Constraints

- Migration `0012_encrypted_document_import.py` has `down_revision = "0011_local_authentication"` and leaves every prior policy, event, claim, identity, and Phase 1 contract intact.
- Actual insurance, medical, identity, password, archive key, source path, extracted text, OCR result, and provider values never enter Git, fixtures, logs, responses, or CI artifacts.
- Public tests use only from-scratch synthetic PDFs and values such as Family Member A, Sample Policy, and synthetic/policy-001.pdf.
- Public CI never calls OpenAI, Google Drive, Tailscale, or a real private path.
- AI remains non-authoritative; only deterministic domain code may produce MATCH, NO_MATCH, UNKNOWN, or money.
- Missing facts, Evidence, contract state, renewal state, or rule support remain UNKNOWN, never NO_MATCH, zero, or an exception.
- Existing Phase 1 v1 contracts, eight ingestion tables, descriptor-only intake, parser limits, job states, password-free AnalysisJob payload, and synthetic route gate remain compatible.
- The batch accepts exactly one server-derived FamilyMember; a client-supplied household or member identifier never broadens scope.
- A PDF password exists only in the API request memory and the Worker’s ephemeral IPC/memory scope; it is absent from DB, queued job JSON, archive metadata, response, and logs.
- The archive master key is read from an external mode-0600 file; it is never an environment value, image layer, database value, response, or log field.
- A successful batch item is not ready until decrypted extraction and archive encryption both succeed.
- The Root PR gate in docs/plan/003-v0.1-implementation-index.md is run once on the complete branch immediately before push, followed by focused post-merge verification.
- Actual private-data acceptance is separate from CI and is blocked until the private Compose runtime in docs/plan/015-private-local-runtime.md is merged.

---

## File Responsibility Map

~~~text
apps/api/migrations/versions/0012_encrypted_document_import.py
  Batch, batch-item, and managed-archive metadata tables. No secret or PDF body columns.

packages/contracts/schemas/document-batch.v1.schema.json
packages/contracts/schemas/document-batch-status.v1.schema.json
packages/contracts/examples/document-batch.v1.json
packages/contracts/examples/document-batch-status.v1.json
scripts/generate_batch_contract_types.py
scripts/check_batch_contracts.py
  Strict household-scoped batch request/status contracts.

apps/api/src/familycare_api/documents/batch_router.py
apps/api/src/familycare_api/documents/batch_service.py
apps/api/src/familycare_api/documents/batch_repository.py
apps/api/src/familycare_api/documents/secret_channel.py
  Authenticated batch creation, scope checks, and one-time Worker socket handoff.

apps/web/src/api/document-imports.ts
apps/web/src/features/documents/ImportPage.tsx
apps/web/src/features/documents/ImportSourcePicker.tsx
apps/web/src/features/documents/BatchPasswordDialog.tsx
apps/web/src/features/documents/BatchProgress.tsx
apps/web/src/features/documents/document-import.test.tsx
apps/web/e2e/document-import.spec.ts
  Generated-contract client, inbox selection, memory-only password prompt,
  per-file progress, failed-only retry, and synthetic browser acceptance.

workers/analyzer/src/familycare_worker/archive/__init__.py
workers/analyzer/src/familycare_worker/archive/keys.py
workers/analyzer/src/familycare_worker/archive/crypto.py
workers/analyzer/src/familycare_worker/archive/store.py
workers/analyzer/src/familycare_worker/archive/rotation.py
  Master-key validation, envelope encryption, atomic archive writes, and key rotation.

workers/analyzer/src/familycare_worker/imports/__init__.py
workers/analyzer/src/familycare_worker/imports/batch.py
workers/analyzer/src/familycare_worker/imports/password_scope.py
workers/analyzer/src/familycare_worker/imports/cleanup.py
  Per-file import lifecycle and password disposal.

apps/api/tests/test_document_batch_api.py
apps/api/tests/test_document_batch_scope.py
workers/analyzer/tests/test_archive_crypto.py
workers/analyzer/tests/test_archive_rotation.py
workers/analyzer/tests/test_batch_password_scope.py
workers/analyzer/tests/test_batch_cleanup.py
workers/analyzer/tests/test_private_import_contract.py
  Contract, encryption, scope, memory, and cleanup regression tests.
~~~

## Domain and IPC Interfaces

The API/Worker process boundary cannot use an in-memory broker because the services run in separate Compose containers. The selected handoff is a shared Unix-domain socket mounted on a short-lived runtime volume. The Worker creates the socket with mode 0660 in a dedicated group, the API sends one framed one-time message, and the Worker stores only the decoded secret in a process-memory batch scope. The socket payload is never written to PostgreSQL or a file.

~~~python
@dataclass(frozen=True)
class SecretHandoff:
    batch_id: UUID
    handoff_id: UUID
    password: str
    expires_at: datetime


class BatchSecretSocketClient(Protocol):
    def send_once(self, handoff: SecretHandoff) -> None: ...


class BatchSecretSocketServer(Protocol):
    def receive_once(self) -> tuple[UUID, UUID, str, datetime]: ...
    def take(self, batch_id: UUID, handoff_id: UUID, now: datetime) -> str | None: ...
    def discard(self, batch_id: UUID) -> None: ...


class PasswordScope(Protocol):
    def password_for(self, item_id: UUID) -> str | None: ...
    def replace(self, password: str, *, expires_at: datetime) -> None: ...
    def dispose(self) -> None: ...
~~~

The container-internal socket location is /run/familycare/secret.sock, not a source-data path. The handoff frame contains a UUID pair, an expiry timestamp, and the password; it has no source key, filename, document text, or family display name. The Worker rejects a reused handoff ID, an expired frame, and a batch ID without an active in-memory scope.

~~~python
@dataclass(frozen=True)
class ArchiveMetadata:
    archive_id: UUID
    document_version_id: UUID
    object_key: str
    scheme: str
    key_version: str
    nonce: bytes
    wrapped_data_key: bytes
    ciphertext_size: int
    auth_tag: bytes


class ArchiveStore(Protocol):
    def put(
        self,
        document_version_id: UUID,
        source: BinaryIO,
        *,
        master_key: MasterKey,
    ) -> ArchiveMetadata: ...

    def open(
        self,
        metadata: ArchiveMetadata,
        *,
        master_key: MasterKey,
    ) -> BinaryIO: ...

    def rewrap_all(self, old_key: MasterKey, new_key: MasterKey) -> int: ...
~~~

The archive scheme is aes-256-gcm+aes-kw-v1. Each document gets a random 32-byte data key. AES-GCM uses a random 12-byte nonce and authenticated data containing only the archive schema version and DocumentVersion UUID. AES-KW wraps the data key with the 32-byte runtime master key. Archive objects use opaque UUID keys and are written atomically with mode 0600.

## Task 1: Add strict batch and archive metadata contracts

**Files:**

- Create: apps/api/migrations/versions/0012_encrypted_document_import.py
- Create: packages/contracts/schemas/document-batch.v1.schema.json
- Create: packages/contracts/schemas/document-batch-status.v1.schema.json
- Create: packages/contracts/examples/document-batch.v1.json
- Create: packages/contracts/examples/document-batch-status.v1.json
- Create: scripts/generate_batch_contract_types.py
- Create: scripts/check_batch_contracts.py
- Create: apps/api/src/familycare_api/documents/generated_batch_contracts.py
- Create: workers/analyzer/src/familycare_worker/generated_batch_contracts.py
- Create: apps/api/tests/test_document_batch_contracts.py
- Modify: scripts/check_contracts.py
- Modify: packages/contracts/README.md
- Test: apps/api/tests/test_document_batch_database.py

**Interfaces:**

- Consumes: HouseholdSpace, FamilyMember, Document, DocumentVersion, and AnalysisJob from previous merged plans.
- Produces: strict batch request/status types and these physical tables: document_batches, document_batch_items, and managed_archives.
- document_batches contains id, household_space_id, family_member_id, created_by, state, timestamps, and completion metadata.
- document_batch_items contains id, batch_id, optional document_id, relative source_key, state, stable error_code, attempts, and timestamps. It has no password, path, PDF bytes, or arbitrary metadata column.
- managed_archives contains id, document_version_id, opaque object_key, scheme, key version, nonce, wrapped data key, ciphertext size, auth tag, timestamps, and retired timestamp. It has no key or plaintext column.
- A batch is valid only when every item resolves beneath the configured import root and all items share one FamilyMember.

- [x] **Step 1: Write schema and migration tests before creating implementation files**

~~~python
def test_batch_request_has_one_family_member_and_no_secret_fields() -> None:
    request = load_example("document-batch.v1.json")

    assert request["schema_version"] == "1"
    assert request["family_member_id"] == SYNTHETIC_FAMILY_MEMBER_ID
    assert request["source_ids"] == [
        SYNTHETIC_SOURCE_ID_A,
        SYNTHETIC_SOURCE_ID_B,
    ]
    forbidden = {"password", "absolute_path", "raw_pdf", "archive_master_key"}
    assert forbidden.isdisjoint(request)
    assert forbidden.isdisjoint(json.dumps(request))


def test_archive_table_has_wrapped_key_metadata_only(migration_sql: str) -> None:
    assert "managed_archives" in migration_sql
    assert "wrapped_data_key" in migration_sql
    assert "archive_master_key" not in migration_sql
    assert "plaintext" not in migration_sql
~~~

- [x] **Step 2: Run the RED contract and migration tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_document_batch_contracts.py \
  apps/api/tests/test_document_batch_database.py -q
~~~

Expected: FAIL because the batch schemas, generated consumers, and migration 0012_encrypted_document_import do not exist.

- [x] **Step 3: Add schemas, generated types, and PostgreSQL constraints**

Use strict request fields:

~~~json
{
  "schema_version": "1",
  "family_member_id": "00000000-0000-4000-8000-000000000004",
  "source_ids": [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  ]
}
~~~

Use enum states created, running, partial, succeeded, failed, and cancelled for batches; use queued, running, succeeded, password_required, retryable_failed, permanently_failed, and cancelled for items. Enforce 64-character lowercase-hex source IDs at the public boundary and retain the Phase 1 relative source-key validation only inside the server/Worker boundary. Status responses return source ID and bounded display label, never the internal relative source key. Generate API/Worker TypedDict modules without hand edits. Add foreign keys and indexes, and keep existing eight tables and their constraints unchanged.

- [x] **Step 4: Run the GREEN contract and PostgreSQL migration checks**

Run:

~~~bash
TMPDIR=/tmp uv run python scripts/check_batch_contracts.py
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_document_batch_contracts.py \
  apps/api/tests/test_document_batch_database.py -q
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini current
~~~

Expected: all contract examples and migration assertions pass; no Phase 1 schema file or generated document type drifts.

- [x] **Step 5: Commit the contract slice**

~~~bash
git add apps/api/migrations/versions/0012_encrypted_document_import.py \
  packages/contracts/schemas/document-batch.v1.schema.json \
  packages/contracts/schemas/document-batch-status.v1.schema.json \
  packages/contracts/examples/document-batch.v1.json \
  packages/contracts/examples/document-batch-status.v1.json \
  scripts/generate_batch_contract_types.py scripts/check_batch_contracts.py \
  apps/api/tests/test_document_batch_contracts.py \
  apps/api/tests/test_document_batch_database.py \
  scripts/check_contracts.py packages/contracts/README.md
git commit -m "feat(documents): define encrypted batch contracts"
~~~

## Task 2: Implement the Unix-domain secret handoff and archive cryptography

**Files:**

- Create: apps/api/src/familycare_api/documents/secret_channel.py
- Create: workers/analyzer/src/familycare_worker/imports/secret_channel.py
- Create: workers/analyzer/src/familycare_worker/imports/password_scope.py
- Create: workers/analyzer/src/familycare_worker/archive/keys.py
- Create: workers/analyzer/src/familycare_worker/archive/crypto.py
- Create: workers/analyzer/src/familycare_worker/archive/store.py
- Create: workers/analyzer/src/familycare_worker/archive/rotation.py
- Modify: apps/api/pyproject.toml only for test/runtime support required by the channel client
- Modify: workers/analyzer/pyproject.toml to add cryptography==50.0.0
- Modify: uv.lock
- Modify: THIRD_PARTY_NOTICES.md
- Test: apps/api/tests/test_batch_secret_channel.py
- Test: workers/analyzer/tests/test_batch_password_scope.py
- Test: workers/analyzer/tests/test_archive_crypto.py
- Test: workers/analyzer/tests/test_archive_rotation.py

**Interfaces:**

- Consumes: Task 1 batch IDs and archive metadata plus a shared socket at /run/familycare/secret.sock.
- Produces: one-use handoff frames, memory-only PasswordScope, MasterKey.from_file, encrypt_document, decrypt_document, ArchiveStore.put/open, and rewrap_all.
- MasterKey.from_file(path) requires an absolute external path, regular file, mode 0600, and exactly 32 bytes; errors are stable and never include the path or key bytes.
- PasswordScope.dispose() removes all string references it owns and is called on success, failure, cancellation, worker shutdown, and socket error.
- Archive writes use an opaque UUID object key and a temporary mode-0600 file followed by an atomic rename; a database row is inserted only after the ciphertext is durable.

- [x] **Step 1: Write failing IPC, key, round-trip, tamper, and rotation tests**

~~~python
def test_reused_handoff_id_is_rejected(socket_server) -> None:
    frame = SecretHandoff(
        batch_id=SYNTHETIC_BATCH_ID,
        handoff_id=SYNTHETIC_HANDOFF_ID,
        password="synthetic-password",
        expires_at=UTC_NOW + timedelta(seconds=30),
    )
    socket_server.receive(frame)
    assert socket_server.take(frame.batch_id, frame.handoff_id, UTC_NOW) == "synthetic-password"
    assert socket_server.take(frame.batch_id, frame.handoff_id, UTC_NOW) is None


def test_archive_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    key = MasterKey.synthetic(b"k" * 32)
    metadata, ciphertext = encrypt_document(
        b"synthetic-decrypted-pdf",
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=key,
    )
    assert decrypt_document(metadata, ciphertext, master_key=key) == b"synthetic-decrypted-pdf"

    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(ArchiveIntegrityError):
        decrypt_document(metadata, tampered, master_key=key)
~~~

- [x] **Step 2: Run the RED cryptography tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_batch_secret_channel.py \
  workers/analyzer/tests/test_batch_password_scope.py \
  workers/analyzer/tests/test_archive_crypto.py \
  workers/analyzer/tests/test_archive_rotation.py -q
~~~

Expected: FAIL because the socket server, password scope, master-key loader, and archive implementation do not exist.

- [x] **Step 3: Implement one-time socket framing and AES-GCM/AES-KW**

Use a bounded length-prefixed JSON frame for UUIDs and expiry, then send the password bytes only in the socket frame. The Worker consumes the frame once and keeps the password only in a batch-local object:

~~~python
def take(self, batch_id: UUID, handoff_id: UUID, now: datetime) -> str | None:
    entry = self._entries.get(batch_id)
    if entry is None or entry.handoff_id != handoff_id or entry.expires_at <= now:
        return None
    return entry.password
~~~

Use AESGCM(data_key).encrypt(nonce, plaintext, aad) and aes_key_wrap(master_key, data_key). Split the final 16-byte GCM tag into auth_tag metadata and ciphertext bytes for the archive object. Use hmac.compare_digest for token-like frame IDs, reject frames above the fixed 64 KiB control limit, set socket mode 0660, and discard stale entries on every receive/take operation. Never use pickle, shell commands, or a path-based secret handoff.

- [x] **Step 4: Run the GREEN crypto and dependency checks**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_batch_secret_channel.py \
  workers/analyzer/tests/test_batch_password_scope.py \
  workers/analyzer/tests/test_archive_crypto.py \
  workers/analyzer/tests/test_archive_rotation.py -q
TMPDIR=/tmp uv run ruff format --check workers/analyzer apps/api
TMPDIR=/tmp uv run ruff check workers/analyzer apps/api
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src
~~~

Expected: all tests pass for round-trip, tamper, wrong key, missing key, expiry, reuse, disposal, atomic object writes, and key rewrap; the lockfile contains the pinned direct cryptography dependency and notices name its license boundary.

- [x] **Step 5: Commit the IPC and archive slice**

~~~bash
git add apps/api/src/familycare_api/documents/secret_channel.py \
  workers/analyzer/src/familycare_worker/imports/password_scope.py \
  workers/analyzer/src/familycare_worker/archive \
  apps/api/tests/test_batch_secret_channel.py \
  workers/analyzer/tests/test_batch_password_scope.py \
  workers/analyzer/tests/test_archive_crypto.py \
  workers/analyzer/tests/test_archive_rotation.py \
  apps/api/pyproject.toml workers/analyzer/pyproject.toml uv.lock \
  THIRD_PARTY_NOTICES.md
git commit -m "feat(archive): add ephemeral password handoff"
~~~

## Task 3: Add the authenticated batch API and per-file Worker lifecycle

**Files:**

- Create: apps/api/src/familycare_api/documents/batch_repository.py
- Create: apps/api/src/familycare_api/documents/batch_service.py
- Create: apps/api/src/familycare_api/documents/batch_router.py
- Create: apps/api/src/familycare_api/documents/import_sources.py
- Create: workers/analyzer/src/familycare_worker/imports/batch.py
- Create: workers/analyzer/src/familycare_worker/imports/cleanup.py
- Modify: workers/analyzer/src/familycare_worker/__main__.py
- Modify: workers/analyzer/src/familycare_worker/repository.py
- Modify: apps/api/src/familycare_api/main.py
- Test: apps/api/tests/test_document_batch_api.py
- Test: apps/api/tests/test_document_batch_scope.py
- Test: workers/analyzer/tests/test_batch_runner.py
- Test: workers/analyzer/tests/test_batch_cleanup.py
- Test: workers/analyzer/tests/test_private_import_contract.py
- Test: apps/api/tests/test_document_batch_repository.py
- Test: workers/analyzer/tests/test_batch_database.py
- Test: workers/analyzer/tests/test_batch_secret_runtime.py

**Interfaces:**

- Consumes: authenticated AuthContext, one-family batch contract, socket handoff, descriptor-safe intake, native extraction, and ArchiveStore.
- Produces: `GET /api/v1/document-import-sources`, `POST /api/v1/document-batches`, `GET /api/v1/document-batches/{batch_id}`, `POST /api/v1/document-batches/{batch_id}/password`, and `POST /api/v1/document-batches/{batch_id}/cancel`.
- `ImportSourceCatalog.list(context) -> tuple[ImportSource, ...]` scans only the configured import root, returns an opaque SHA-256 source ID plus bounded display name/size/encrypted hint, and never returns an absolute path. It resolves a selected ID by rescanning and applying the existing descriptor-safe root/symlink checks.
- `BatchService.create(context, family_member_id, source_ids) -> BatchCreated` verifies the family member belongs to `context.household_space_id`, resolves every source server-side, and never accepts a client household scope or client path.
- BatchService.handoff_password(context, batch_id, password) -> None sends one socket handoff and returns no secret projection.
- BatchRunner.run_item(item_id) -> ItemResult uses PasswordScope, creates a workspace, decrypts an encrypted PDF to a mode-0600 temporary file, runs the existing extraction pipeline, archives the decrypted bytes, commits item success, and executes cleanup in finally.
- Password failure changes only that item to password_required; successful sibling items continue. A wrong password never triggers automatic retries.

- [x] **Step 1: Write failing HTTP, scope, partial-failure, and cleanup tests**

~~~python
def test_batch_rejects_mixed_family_members(authenticated_client) -> None:
    response = authenticated_client.post(
        "/api/v1/document-batches",
        json={
            "schema_version": "1",
            "family_member_id": str(SYNTHETIC_FAMILY_MEMBER_ID),
            "source_ids": [SYNTHETIC_SOURCE_ID, "not-a-valid-source-id"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"


def test_password_is_not_in_status_or_item_payload(authenticated_client, fake_socket) -> None:
    response = authenticated_client.post(
        f"/api/v1/document-batches/{SYNTHETIC_BATCH_ID}/password",
        json={"password": "synthetic-password"},
    )

    assert response.status_code == 202
    assert "synthetic-password" not in response.text
    assert fake_socket.last_frame.password == "synthetic-password"
    assert "password" not in persisted_batch_item_json()
~~~

- [x] **Step 2: Run the RED batch tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_document_batch_api.py \
  apps/api/tests/test_document_batch_scope.py \
  workers/analyzer/tests/test_batch_runner.py \
  workers/analyzer/tests/test_batch_cleanup.py \
  workers/analyzer/tests/test_private_import_contract.py -q
~~~

Expected: FAIL because the private batch routes and per-file runner are absent.

- [x] **Step 3: Implement the scoped API and item state machine**

Use strict request models and server scope:

~~~python
@router.post("/document-batches", status_code=202)
def create_batch(
    request: BatchCreateRequest,
    context: AuthContext = Depends(require_household_context),
) -> BatchCreatedResponse:
    created = service.create(
        household_space_id=context.household_space_id,
        created_by=context.user_id,
        family_member_id=request.family_member_id,
        source_ids=request.source_ids,
    )
    return BatchCreatedResponse.from_domain(created)
~~~

The source-list route is authenticated, no-store, and emits only entries from the exact configured inbox; it never accepts a directory query parameter. The password route validates a non-empty bounded string, sends it once to the Worker socket, and responds with only batch_id, state, and an item status projection. Do not add the password to a Pydantic response model, job settings JSON, SQL parameters, logs, or error details. The runner must preserve Phase 1 PASSWORD_REQUIRED for the password-free synthetic route while using a separate runtime password path for private batches.

- [x] **Step 4: Run the GREEN batch tests and integration checks**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_document_batch_api.py \
  apps/api/tests/test_document_batch_scope.py \
  workers/analyzer/tests/test_batch_runner.py \
  workers/analyzer/tests/test_batch_cleanup.py \
  workers/analyzer/tests/test_private_import_contract.py -q
FAMILYCARE_DATABASE_URL=postgresql+psycopg://synthetic_ci:synthetic_only@127.0.0.1:5432/synthetic_ci \
TMPDIR=/tmp uv run pytest -m integration \
  apps/api/tests/test_document_batch_database.py workers/analyzer/tests/test_batch_runner.py -q
~~~

Expected: batch creation, server scope, one-time password handoff, partial success, re-prompt, archive-before-ready, cancellation, and cleanup tests pass without changing the Phase 1 route behavior.

- [x] **Step 5: Commit the batch lifecycle slice**

~~~bash
git add apps/api/src/familycare_api/documents/batch_repository.py \
  apps/api/src/familycare_api/documents/batch_service.py \
  apps/api/src/familycare_api/documents/batch_router.py \
  apps/api/src/familycare_api/documents/import_sources.py \
  workers/analyzer/src/familycare_worker/imports \
  workers/analyzer/src/familycare_worker/runner.py \
  workers/analyzer/src/familycare_worker/repository.py \
  apps/api/src/familycare_api/main.py \
  apps/api/tests/test_document_batch_api.py \
  apps/api/tests/test_document_batch_scope.py \
  workers/analyzer/tests/test_batch_runner.py \
  workers/analyzer/tests/test_batch_cleanup.py \
  workers/analyzer/tests/test_private_import_contract.py
git commit -m "feat(documents): import encrypted family batches"
~~~

## Task 4: Add the authenticated document-import Web flow

**Files:**

- Create: `apps/web/src/api/document-imports.ts`
- Create: `apps/web/src/features/documents/ImportPage.tsx`
- Create: `apps/web/src/features/documents/ImportSourcePicker.tsx`
- Create: `apps/web/src/features/documents/BatchPasswordDialog.tsx`
- Create: `apps/web/src/features/documents/BatchProgress.tsx`
- Create: `apps/web/src/features/documents/document-import.test.tsx`
- Create: `apps/web/e2e/document-import.spec.ts`
- Modify: `.gitignore`
- Modify: `apps/web/src/app/AppRoutes.tsx`
- Modify: `apps/web/src/app/AppShell.tsx`
- Modify: `apps/web/src/styles.css`
- Verify unchanged: `apps/web/playwright.config.ts`

**Interfaces:**

- Consumes: generated `ImportSource`, `DocumentBatch`, `DocumentBatchItem`, and password/cancel operations.
- Produces: `/app/documents/import`, a one-FamilyMember source picker, batch creation, password prompt, per-item status, failed-only retry, cancel, and ledger navigation after completion.
- The Web never accepts a directory path, never uploads a PDF, and never stores source labels, passwords, batches, document state, or errors in Web Storage, IndexedDB, URLs, service-worker caches, console output, or analytics.

- [x] **Step 1: Write failing component tests**

Test one-member selection, checked opaque source IDs, no `<input type="file">`, no path textbox, create/poll progress, password prompt only for `password_required`, clearing password after the request, keeping successful items completed, retrying only failed items, cancellation, 401 cleanup, and no persistent browser writes.

```bash
corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
  src/features/documents/document-import.test.tsx
```

Expected: FAIL because the generated import client and document components do not exist.

- [x] **Step 2: Implement the generated no-store client and accessible import screens**

All calls go through the authenticated no-store wrapper. Keep the password in the dialog component only, submit it once, then clear the state in `finally` and again on close/unmount. Poll a bounded status endpoint while the page is mounted; abort on navigation/logout. Display only safe source labels and stable error codes. Use a real dialog with focus trap/return, `aria-live="polite"` for progress, text in addition to color, and no horizontal overflow at 320 CSS px.

```ts
export async function handoffBatchPassword(
  batchId: string,
  password: string,
): Promise<DocumentBatch> {
  try {
    return await apiRequest(`/api/v1/document-batches/${batchId}/password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    });
  } finally {
    password = "";
  }
}
```

The local reassignment is defense-in-depth only; the component must also clear its controlled state immediately because JavaScript strings cannot be securely zeroized.

- [x] **Step 3: Run GREEN and add the synthetic browser flow**

```bash
corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
  src/features/documents/document-import.test.tsx
corepack pnpm@11.22.0 --filter @familycare/web exec playwright test \
  --workers=1 e2e/document-import.spec.ts
corepack pnpm@11.22.0 web:check
```

The Playwright route stubs use synthetic source labels and exercise select → create → password-required → partial success → retry failed only → completed. Assert no PDF upload request, password persistence, Web Storage write, API/service-worker cache entry, raw response logging, or source path in the URL.

- [x] **Step 4: Commit the Web import slice**

```bash
git add apps/web/src/api/document-imports.ts \
  apps/web/src/features/documents \
  apps/web/src/app/AppRoutes.tsx apps/web/src/styles.css \
  apps/web/e2e/document-import.spec.ts apps/web/playwright.config.ts
git commit -m "feat(web): add encrypted document import flow"
```

## Task 5: Document runtime secret mounts, cleanup evidence, and PR readiness

**Files:**

- Modify: docs/guide.md
- Modify: docs/design/private-data-runtime.md
- Modify: docs/design/pdf-ingestion.md
- Modify: .gitignore only for runtime socket/output exclusions that are narrowly required
- Modify: scripts/check_repository_safety.py only for synthetic source-module exceptions
- Test: scripts/tests/test_repository_safety.py
- Test: workers/analyzer/tests/test_archive_cleanup.py
- Test: apps/api/tests/test_document_batch_contracts.py

**Interfaces:**

- Consumes: batch API, Unix socket handoff, archive metadata, cleanup runner, and the existing safety scanner.
- Produces: an operator guide that names only external path shapes, explains the mode-0600 master-key requirement, and states that Google Drive originals are never modified or deleted.
- The guide must distinguish CI synthetic checks from user-approved private-data acceptance and must state that missing mobile, Windows, provider, and document-format checks remain unverified until performed.

- [ ] **Step 1: Add failure-path cleanup and safety regression tests**

~~~python
def test_decrypted_workspace_is_removed_after_archive_failure(tmp_path, archive_store) -> None:
    with pytest.raises(ArchiveWriteError):
        run_synthetic_item(archive_store=archive_store, fail_archive=True)

    assert list(tmp_path.glob("**/*.pdf")) == []
    assert list(tmp_path.glob("**/*.png")) == []
    assert no_password_or_key_in_captured_logs()
~~~

- [ ] **Step 2: Run the RED cleanup/safety tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  workers/analyzer/tests/test_archive_cleanup.py \
  scripts/tests/test_repository_safety.py -q
~~~

Expected: FAIL until every runner exit path disposes the password scope and removes decrypted PDFs, rendered images, and temporary archive files.

- [ ] **Step 3: Add the operator guide and narrow safety rules**

Document the external path form /absolute/path/outside/repository only as a non-value example. Explain that a master-key file is created and permission-checked outside the checkout, that password input is prompted per batch, and that no import command deletes the manually downloaded source or Google Drive original. Keep any ocr or archive source-code exception limited to tracked Python modules; never allow generated output directories.

- [ ] **Step 4: Run the complete import-focused gate**

Run serially:

~~~bash
TMPDIR=/tmp uv run python scripts/check_documentation.py
TMPDIR=/tmp uv run python scripts/check_repository_safety.py
TMPDIR=/tmp uv run python scripts/check_batch_contracts.py
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_document_batch_contracts.py \
  workers/analyzer/tests/test_archive_crypto.py \
  workers/analyzer/tests/test_archive_rotation.py \
  workers/analyzer/tests/test_batch_password_scope.py \
  workers/analyzer/tests/test_batch_cleanup.py \
  workers/analyzer/tests/test_archive_cleanup.py \
  scripts/tests/test_repository_safety.py -q
TMPDIR=/tmp uv run ruff format --check .
TMPDIR=/tmp uv run ruff check .
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
git diff --check
~~~

Expected: all checks pass and no secret, plaintext, absolute path, or private-derived fixture is visible to the repository safety scanner.

- [ ] **Step 5: Commit documentation and invoke the Root PR gate**

~~~bash
git add docs/guide.md docs/design/private-data-runtime.md \
  docs/design/pdf-ingestion.md .gitignore \
  scripts/check_repository_safety.py scripts/tests/test_repository_safety.py \
  workers/analyzer/tests/test_archive_cleanup.py \
  apps/api/tests/test_document_batch_contracts.py
git commit -m "test(documents): verify encrypted import cleanup"
~~~

Before push, execute the complete Root PR gate from docs/plan/003-v0.1-implementation-index.md, review the full diff once, open the PR, wait for all required checks, merge with a merge commit, and rerun archive round-trip, password-disposal, and migration checks on post-merge main. Real PDF, device, Tailscale, and OpenAI acceptance remain separate and must not be claimed by this PR.

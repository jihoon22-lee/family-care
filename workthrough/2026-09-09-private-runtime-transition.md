# Existing data transition and recovery

B07 [#69](https://github.com/jihoon22-lee/family-care/issues/69) is in progress, based on B06
[PR #81](https://github.com/jihoon22-lee/family-care/pull/81), merged as `f59c8a9` after CI
34306662287 passed 7/7 (including 742 PG tests). The merged B06 source is integrated locally.
The transition uses an isolated restore before activating reconstructed knowledge. Original source,
corrections, event/claim snapshots and review history are preserved; provider work is not automatic.

## Schema fence

Readiness implementation `71a8f10` (integrated as `dbda36f`) ships the exact supported Alembic
revision in both installed distributions and parses required columns without reading rows. Worker
checks before constructing private runners and before its next job. Initial **23 RED failures**
(1.21s) became **63 passed** (2.12s); an early-cancel cleanup regression was separately reproduced
and corrected. Final health/readiness **64 passed** (1.85s), Ruff and mypy **5 files** passed.
These unit checks use fake connections. Root additionally executed both actual readiness probes
against the dedicated synthetic PostgreSQL 0063 database: both returned true.

API fence `bc9032d` (integrated as `07be716`) checks schema before the application lifespan and
before business HTTP requests. Health diagnostics remain reachable; failures return a fixed 503
with no-store and no exception details. DB-absent contract/mock construction remains supported.
Initial **8 failed / 1 passed** exposed the missing guard. Final related fence, health, consumer
and document API suite **48 passed** (5.48s), Ruff/mypy/contracts/diff passed. Existing fake DB
tests required explicit injected probes; an unrelated fake consumer was isolated from DNS waits.
Already-running old binaries and in-flight transactions still require the transition writer barrier.

## Backup and preservation checks

At `07be716` plus the backup/state/test changes, 2026-09-09 UTC, Python 3.14.7 and PostgreSQL 18.6,
task API/Worker/root PYTHONPATH and TMPDIR=/tmp, with the destructive test guard on a dedicated
synthetic DB only:

- Backup low-disk preflight and write-time ENOSPC/EDQUOT use a distinct fixed error and remove the
  newly created partial output. RED **2 failed** (0.08s); the full existing backup suite then
  **15 passed** (0.07s). Disk checks are estimates, not space reservations.
- New `private_runtime_state.py` fingerprints rows and primary-key identities inside PostgreSQL,
  returning only bounded digests for a private journal. A repeatable-read/serializable caller
  transaction is required. Missing-module RED failed at collection (0.10s); initial **5 PG passed**
  (0.16s). Static review then found normal Alembic stamp changes incorrectly included in data
  comparison: RED **1 failed** (0.45s), corrected full suite **6 passed** (0.68s).
- Backfill may append new identities while every original row remains unchanged. New identity
  cannot substitute for a removed original. RED **2 failed** (0.08s); **8 passed** (1.01s), followed
  by **9 passed** (1.02s), including the bounded identity-inventory regression. Strict activation/source
  comparison continues to reject schema or data changes.
- Targeted mypy initially missed the source packages and reported untyped installed imports;
  setting task MYPYPATH corrected resolution and **2 source files passed**. Ruff import/style/line
  issues were corrected. These focused checks are not full B07 completion or actual recovery.

## Protected inventory

The session's existing-data/key/backup/transition authorization was reused for a read-only inventory.
Existing runtime is v0.4.0 at schema 0024; archive and separate recovery key availability were
confirmed. Only schema, counts, sizes and fixed status left the protected process. No source text,
identifier, amount, path or key was written here; no external AI, migration, copy, deletion or runtime
change was performed by that inventory. Backup acquisition, real pg_restore, reconstruction,
source binding, authenticated app acceptance and activation remain pending.

## PostgreSQL restore and reconstruction integration

At `03414d4` plus PostgreSQL tools, cancellation fixes, CI/test/document changes, 2026-09-09 UTC:

- Coordinator `de9ea17` (integrated as `03414d4`) binds explicit sources to cluster/DB identity,
  retained-input digests and pipeline revisions. It reuses preparation, exact-generation metadata
  and existing local projectors without scheduling provider work. Initial fake adapter RED became
  **20 passed** (0.18s); source Ruff/mypy passed. Cooperative deadlines do not forcibly kill a
  projector already executing.
- New PostgreSQL tool missing-module RED failed collection (0.09s). Environment-only credentials,
  exclusive private dump output, in-container timeout, empty-target and transactional restore are
  covered by **5 passed** (0.03s). Static review exposed capture/restore cancellation and destination
  chmod cleanup: **3 RED failures** (0.08s) became backup+tool **23 passed** (0.09s).
- Initial integration attempts had an incorrect dedicated URL, then missing migration baseline;
  neither established a product pass. Applied all migrations to the empty dedicated DB through 0063.
  Real restore then failed because buffered header validation left the inherited descriptor after
  read-ahead; resetting the OS offset corrected the failure.
- `FAMILYCARE_TEST_DATABASE_URL` (dedicated synthetic DB), destructive opt-in and
  `FAMILYCARE_TEST_POSTGRES_CONTAINER` set, `TMPDIR=/tmp uv run pytest -m integration
  scripts/tests/test_private_runtime_restore_integration.py
  scripts/tests/test_restructure_existing_documents_integration.py -q`: **2 passed** (20.12s).
  A genuine custom dump, authenticated backup/materialization and pg_restore preserved all table
  rows plus a reviewed claim snapshot; the restored synthetic archive decrypted. Coordinator SQL
  prepared three explicit sources, retained PARTIAL for unresolved metadata, resumed idempotently
  and rejected a same-row-count original edit. Separate state integration **9 passed** (within
  the preceding 13.29s run whose restore test failed).
- Ruff/format on seven affected files, workflow policy and targeted mypy with source MYPYPATH passed.
  CI now passes its PostgreSQL service container ID at the database-test step. Full B07 required
  suite, protected recovery and activation have not yet been completed.

Protected backup preflight found the separate key readable only within its original container mount;
operations now use a network-disabled, read-only utility container with the original key mount and
only the newly created backup destination writable. The key is never copied into the backup.
The first quiesced capture exceeded the bounded retained-identity inventory and was refused;
old API/Worker/Web were restarted and healthy, with no migration or activation.
The state helper now permits explicit identity-retention tables: excluded large extraction/OCR tables
still hash and compare every row exactly, including in append-enabled comparisons. This keeps the
memory bound without weakening preservation. RED **1 failed** (0.05s), full state PostgreSQL suite
**10 passed** (0.80s), targeted mypy/Ruff passed at `e571ae7` plus this state/test/document change.

## Protected restoration acceptance

At clean source `5d90fd7`, the approved local operation quiesced the owned old Web/API/Worker,
exported one repeatable-read PostgreSQL custom snapshot and captured the encrypted archive using
its separate original key mount. The signed backup and private baseline journal remain outside Git.
A fresh source transaction matched the capture while writers were stopped; the old services were
restarted immediately after capture. An isolated database restored from the authenticated inputs
matched every original row at schema 0024. All restored archive objects authenticated, decrypted
in memory and matched their document-version content hashes. No plaintext PDF was written.

Only the isolated restoration was migrated to 0063. Every original column/row still matched the
baseline afterward. Actual API/Worker readiness probes both accepted 0063 and rejected the old
0024 source. The original runtime database remains unchanged; no external AI was called.

The initial full-corpus reconstruction reached its cooperative bound after nine local steps.
This exposed repeated whole-corpus hashing at each local source step. Follow-up `c468cf7`
(integrated as `16d9ae2`) checks the exact current source during local work while retaining
full-source checks at re-entry, before/after every global projector and at completion. RED **4**
selective-validation failures plus **1** bounded-progress reporting failure became **25 unit passed**
(0.19s), Ruff/mypy passed. Root's existing actual PostgreSQL reconstruction/resume/source-change
regression passed **1 test** (6.31s) after integration. Bounded reports retain only progress actually
observed before the deadline; unobserved sources are not counted as prepared.

Reconstruction is still in progress. Some retained sources have explicit partial plans, local metadata
is not yet complete, and package aliases have no exact full-relative-path matches to retained batch
sources. No filename similarity or summary is promoted to a verified source binding. Authenticated
application acceptance, final source barrier/activation and full B07 CI remain pending.

Draft PR #82 CI 34310699215 passed repository safety, Web and all three container builds; its
Python job failed strict mypy because new script tests imported the previously untyped API/Worker
integration fixture graph, and new fake tests lacked annotations. The integration tests are moved
to `apps/api/tests/test_private_runtime_restore_integration.py` and
`apps/api/tests/test_restructure_existing_documents_integration.py`, matching their existing fixture
suite. Script unit mocks now use explicitly typed handles and direct standard-library monkeypatches;
no required check or typing rule is removed. The coordinator fake adapter also receives complete
annotations in its follow-up. Full combined verification is rerun after these test-only changes.

After test relocation and typed fake adapters, clean source `e04604c` passed full mypy
(**350 source files**) and default pytest (**3,558 passed, 754 integration deselected,
3 subtests passed**, 55.50s). The relocated restore/coordinator integration tests plus state
integration passed **12 tests** (21.09s) on the dedicated migrated PostgreSQL 18.6 container.
Documentation, repository safety, generated contracts, 11 commit subjects, actionlint and diff
checks passed. The earlier draft CI's PostgreSQL integration also passed (20m39s); its Python
failure remains recorded until a new run verifies the corrected head.

## Initial partial source availability

Protected reconstruction exposed an integration gap: a new partial chunk plan kept all independent
metadata unavailable even when useful original nodes were retained and no previous current source
existed. `db9e8dd` (integrated as `50c2e2d`) permits that first nonempty partial source to be current,
while preserving any existing current and refusing cancelled/empty sources. Canonical source/plan
replay and item locking still apply. Preparation revision
`stored-structure-availability-v4-ch4096-context4096-max16384` revisits terminal history once,
reuses the same generation and appends a new preparation record; old records are not rewritten.
The coordinator handles anchored metadata on an approved current partial source while reporting
`partial_sources` and `unprocessed_ranges`. It never marks the chunk plan complete or bypasses
role, enrollment, layout, semantic or rule validators. Protected plans must be captured for v4.

The agent's focused PostgreSQL source/preparation suite passed **22** (9.04s), including initial
partial metadata publication with zero enrollment, old partial replay once, previous-current
preservation and empty/cancelled refusal; coordinator units **26 passed** (0.18s).
Root at `50c2e2d` passed full Ruff/format, mypy **350 files**, default pytest **3,559 passed,
759 integration deselected, 3 subtests passed** (34.92s), and both Worker source/preparation files
plus the API coordinator integration: **23 PostgreSQL tests passed** (18.25s).
These focused runs precede the new protected replay and final CI; they do not establish complete
interpretation or native source binding of the actual corpus.

## Existing private page compatibility

The separate authenticated acceptance clone passed login and original-excerpt reads, then returned
no local guidance for an existing event. A count/field-only diagnostic traced this to private citation
pages: the established private catalog accepts positive page addresses, but the new common evidence
model incorrectly imposed native PDF intake's 500-page limit on them. The resulting private adapter
exception affected both evaluation paths. No private page value or document content was logged.

Synthetic large-page tests reproduced **3 failures** (0.39s). Private terms/certificate addresses and
summary responses now preserve their original positive page numbers. Operational and semantic
originals retain the native limit; the operational constraint is also explicit in the neutral schema.
The new regression plus evidence HTTP and local guidance units passed **24** (0.64s). Decision/claim
schemas and OpenAPI were regenerated separately with their existing commands; Web types remain
unchanged and the full contract checker passed. This changes citation compatibility, not PDF intake
capacity or the authority of private summaries.

At clean source `93c6a51`, the protected ASGI acceptance helper passed against a separate clone:
real login/session/CSRF, private catalog and claim-history reads, available original excerpts,
AI-off local analysis for all retained events, and equality with stored-result reads. External
HTTP attempts and provider work creation were zero. Counts, timings and configuration remain in
the private journal; no document text or actual identifiers enter this artifact. This validates
ASGI plus PostgreSQL, not the gateway/browser, native alias bindings or final runtime activation.
Private adaptation failure is now isolated from the native evaluation path; the dedicated
PostgreSQL source-isolation regression passed **7 tests** (12.23s).

PR #82 CI [34312769544](https://github.com/jihoon22-lee/family-care/actions/runs/34312769544)
passed all seven required checks at `eb920c5`. That run precedes the private page and source
isolation fixes above; subsequent metadata changes require their own final verification.

## Proven metadata prefixes

A later overlapping table previously invalidated a geometrically unambiguous opening title and
insurer row. Worker and independent API validation now preserve the verified prefix and quarantine
formal titles/facts from the first overlap onward. Unknown geometry and contradictory native order
retain the conservative fallback. Existing independent contractual-body proofs remain unchanged.
The API uses this behavior only for metadata v8; older proposals retain their versioned validation.
The neutral schema and both generated consumers accept v8. Migration `0064_metadata_proven_prefix`
adds the matching publication revision, preserves prior rows and refuses downgrade after new
proposal/publication history exists. API/Worker readiness requires the new schema.

The synthetic prefix regression initially failed twice; after the core fix the broader metadata
suite passed **165 tests**. Root v8 integration exposed and updated two tests tied to the old
current revision, then passed **205 unit tests** (5.77s). The new neutral v8 test initially failed
the enum, and the new PostgreSQL replay test initially could not revisit terminal v7. After schema
migration and implementation, replay/history preservation plus navigation and publication passed
**19 PostgreSQL tests** (20.85s); generated contracts passed. These results use `bd8f393` plus the
v8 revision/schema/migration/readiness/test changes in this section. Protected reprocessing and
final required checks remain separate.

The first full Python run found one existing invalid-table-coordinate regression: a zero-area box
was incorrectly admitted to positional ordering. New prefix processing now requires finite,
positive-area boxes, while historical API revisions keep their original behavior. Related source
change/metadata tests passed **109** (1.55s), then full Ruff/format, mypy **350 files**, default pytest
**3,575 passed / 763 integration deselected / 3 subtests passed** (33.25s), container definitions and
workflow checks passed. Final affected PostgreSQL publication/history tests passed **19** (19.33s).
Web format/lint/types, **238 tests in 31 files** (50.10s) and production/PWA build passed with the
unchanged Web source and frozen lockfile. These runs use `bd8f393` plus this v8 bundle; no private
data or provider participates in the public suites.

The protected target and the separate acceptance clone subsequently reached 0064 with every
pre-migration row preserved. The target also passed comparison of all original baseline rows,
allowing only newly appended records. The private journal stores the evidence; the original live
database remains unchanged. Full CI, browser acceptance and final activation are still pending.

At clean `5483b7e`, [CI 34316501218](https://github.com/jihoon22-lee/family-care/actions/runs/34316501218)
passed all seven required jobs, including PostgreSQL integration and the empty database migration
round trip. Protected Chromium then passed real login, secure/HttpOnly/Strict cookie checks and
stored-event result pages at 320px and 1280px, with no browser errors, AI POSTs, external browser
requests, unsafe successful API cache headers or observed sensitive storage/cache writes. No
screenshots or traces were saved. This used the isolated acceptance clone and a temporary local
TLS gateway, not the production gateway or actual Windows/mobile devices. Initial service-worker
registration failed on the temporary self-signed certificate; trusting only its public-key pin in
the isolated browser fixed that test-environment failure.

An approved, minimal original-header rendering exposed a separate runtime dependency gap: a PDF
without embedded Korean fonts rendered numbers and rules while dropping Hangul. Supplying a local
Korean fallback font to the same PDFium image restored the text. The two private diagnostic fragments
were removed immediately after inspection. No source text, document identity or original layout is
used in the wholly synthetic font-packaging regression. This one-file A/B observation does not
establish recovery of all OCR failures or solve missing native source bindings.

The Worker runtime now installs `fonts-noto-cjk`. PDFium's Linux font scanner reads system font
directories directly; Tesseract's Korean recognition data does not supply PDF rendering fonts.
The deterministic 2,850-byte `fixtures/synthetic/korean-font-fallback.pdf` uses seven separately
placed Korean CID-font glyphs and a Latin control without embedding a font. Its generator and
11 unit cases establish provenance and reject individual empty glyph cells. The standalone image
smoke uses only installed PDFium/Pillow, writes no image and never skips a missing-font failure;
the Worker CI job runs it with a read-only filesystem, read-only mounts and networking disabled.

The initial smoke helper exposed unsupported PDFium page context-manager usage; explicit ExitStack
cleanup fixed that helper before the font comparison. The original v0.4 Worker then failed with
`Korean glyph cell is blank`; the newly built `familycare-worker:transition-fonts` passed all seven
glyph cells and the control. Its source is `883ff9f` plus the font Dockerfile, CI smoke and helper
cleanup changes. Full Ruff/format, mypy **353 files**, default pytest **3,586 passed / 763 integration
deselected / 3 subtests passed** (37.43s), container/workflow policy and actionlint passed. The unchanged
Web and schema/DB inputs retain the preceding source-bound results; the new final CI is pending.

Final source `04fbbda540b0002bc9dc56c04ea5aad79e68322d` passed all seven required jobs in
[CI 34318679642](https://github.com/jihoon22-lee/family-care/actions/runs/34318679642), including the
new Worker glyph smoke and PostgreSQL/migration checks. [PR #82](https://github.com/jihoon22-lee/family-care/pull/82)
merged as `18dc981f344bf9d9d656d513aa08a05da1f30b42` on 2026-09-09. Protected backfill reached
terminal `PARTIAL`; a subsequent comparison preserved every original baseline row. Read-only
inspection of retained package artifacts found no exact PDF-byte identity mapping for the opaque
source aliases. This remains a source-declaration gap, not an assertion that documents were never
provided. Policy page roles and usable retained data are reported separately from metadata counts.

The separate acceptance clone at `04fbbda` also passed the real Chromium candidate→claim-draft→reload
path at narrow width, with observed sensitive storage/cache writes and external/AI POSTs remaining
zero. Those test drafts exist only in the clone. The original live v0.4/0024 DB and its claims remain
untouched. B08 records the broader acceptance limits and the separate one-call synthetic live-model
evaluation; none of these checks marks activation, the milestone or release complete.

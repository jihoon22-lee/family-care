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

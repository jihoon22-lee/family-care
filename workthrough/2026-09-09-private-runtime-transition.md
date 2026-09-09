# Existing data transition and recovery

B07 [#69](https://github.com/jihoon22-lee/family-care/issues/69) is in progress, based on B06
[PR #81](https://github.com/jihoon22-lee/family-care/pull/81). B06 CI integration is still pending.
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
  by the bounded identity-inventory regression in the final related run. Strict activation/source
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

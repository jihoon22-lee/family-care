# Independent metadata header regions

For #62/#63/#69, metadata v11 can recover a first document header when unrelated
physical columns cause the earlier linear prefix to stop. It uses all original
page nodes to verify the title, complete word ownership, actual rectangle overlap,
source layer and an adjacent field corridor. A strict company/product caption or
valid labeled field may precede the required formal title through that same flow. It retains source IDs/spans and the
full page/prior-page reference context; fields outside the corridor stay unresolved.
Role-only recognition does not supply missing identity or applicability evidence.

API and Worker independently enforce the proof. Schema 0073 admits v11 history
without rewriting v10 or earlier publications and rejects a history-losing downgrade.
Existing overlapping user decisions and program refinement rules remain in force.
The header proof has explicit limits for source size, table representation, field
scans and repeated text inspection; exhausted work returns no partial proof.
The canonical metadata schema also supplies revision-bound exact company captions
from the public association sections linked in the design. These require complete
header proof and direct role adjacency, preserve the raw name, and cannot collapse
aliases, agency/group mentions, legal succession or historical identities.

## Local verification

2026-09-10 UTC, base `d537c4065a94f903f3abc9ed81707d7a75d68930` plus this task's
API/Worker, generated metadata, migration, synthetic tests and documentation changes:

- A valid missing-header RED failed before implementation. The initial fixture did
  not generate the intended column flag and was corrected before the valid RED.
- New reference-context failures were reproduced and corrected. New header tests
  passed 27 cases; related metadata/source tests passed 284 cases (6.42s).
  Eleven intervening failures were historical v10 fixture/current-version assertions;
  explicit historical producers preserve their old acceptance semantics.
- Independent static review found no additional source/role correctness issue but
  identified unbounded repeated scans. Four budget regressions failed before the
  guard, then header/budget tests passed 31 cases (0.96s). An initially omitted API
  constant in that follow-up was corrected before the successful run.
- The unpublished-v11 history assertion failed against the previous 0072 source.
  On a dedicated synthetic PostgreSQL DB at 0073, new header publication, old v10
  preservation, downgrade refusal and no-history round trip passed four tests
  (5.66s). The first combined run had a missing fixture argument, then passed after
  supplying the normal structure-plan limits.
- Before the title-prelude extension, related PostgreSQL tests passed 86 cases
  (105.70s; 25 non-integration cases deselected). Modules covered header/physical
  flow, body, issuer, navigation, prefix, supersession, native enrollment, metadata
  publication, lineage revisions and the Worker metadata repository.
- The prelude behavior failed 14 new cases before implementation (16 controls
  passed), then its expanded 37-case suite and related header/metadata tests passed
  144 cases (6.15s). Unknown prefixes, short company suffixes, missing/side-column
  titles, source tampering, reference context and budget exhaustion stay rejected.
  Existing direct caption/title adjacency remains required for insurer facts.
- After integration, the four header PostgreSQL tests passed again (7.36s combined
  run also had two new test-fixture failures). The separate extraction-failure
  preservation tests passed both cases (3.63s), proving exact original structure,
  enrollment publication and ledger preservation after terminal extraction failure
  or incomplete new source. Earlier fixture mistakes used a nonexistent publication
  sort column and a nonterminal batch state; both were corrected before that pass.
- Before the exact-caption addition, the full Python suite passed 3,994 cases,
  873 integration cases were excluded, and three subtests passed (34.35s). Whole-tree
  Ruff format/check, mypy (364 files), contracts and static container/workflow checks
  also passed. The caption change requires the affected completion checks again.
- Exact-caption behavior failed three cases with ten controls passing before the
  implementation; two earlier overstrict role-only control assertions were corrected.
  Combined header/caption tests then passed 81 cases (1.33s), and the expanded caption
  suite passed 14 (0.66s), including ordinary headers without a column ambiguity.
- `corepack pnpm web:check` passed format/lint/type, 249 tests (51.42s) and production
  build. Web input files remain unchanged by the later caption addition.

After the final caption change, full Python passed **4,008 tests, three subtests**
(34.92s; 873 integration cases excluded), Ruff format/check passed (929 files),
mypy passed 364 source files, and generated/OpenAPI contract checks passed.
The affected header/issuer/publication PostgreSQL modules passed **20 tests**
(17.72s). The earlier broader 86-case PG result remains separate from this rerun.
Documentation (50 files), repository safety (1,145 paths) and diff checks passed;
the final record-only update receives those checks again before commit.

The final commands were `TMPDIR=/tmp uv run ruff format --check .`,
`TMPDIR=/tmp uv run ruff check .`,
`TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts`,
`TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`,
`TMPDIR=/tmp uv run python scripts/check_contracts.py`,
`TMPDIR=/tmp uv run python scripts/check_containers.py`, and
`TMPDIR=/tmp uv run python scripts/check_workflows.py`.
Static container/workflow inputs did not change after their passing run.
The final dedicated synthetic PostgreSQL command was:

```bash
TMPDIR=/tmp uv run python /tmp/familycare-header-check.py pytest -m integration apps/api/tests/test_metadata_header_revision.py apps/api/tests/test_metadata_insurer_publication.py apps/api/tests/test_document_metadata_publication.py -q --tb=short
```

The local wrapper supplies only the dedicated synthetic test database and explicit
destructive-test opt-in. Python 3.14.7, PostgreSQL 18.6, Node 24.18.0 and the locked pnpm were
used. Independent static reviews checked full-source header boundaries and the
exact public vocabulary/provenance; no additional correctness issue remained.
PR CI/image builds and the clean-source protected replay remain pending.

The separate v10/schema0072 protected acceptance is recorded in
[its existing workthrough](2026-09-10-retained-terms-linking.md#integration-and-isolated-schema-0072-acceptance).
Actual v11 support/publication, final devices, tag and deployment are not yet verified.

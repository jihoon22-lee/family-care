# Source unit in a currency field

- Status: implementation and focused verification complete; protected application and final CI pending.
- Scope: B02 Task 3/4 / #63; final activation and acceptance #69/#70.
- Base: PR #106 source `39a63bcf4e3ed61eb0be44c0f27ab6eebf1205ed`.
- Branch: `fix/source-unit-currency`.

## Problem and outcome

Explicit v13 recovered 37 enrollment rows on the approved clone, preserving all prior
records. Twelve new rows retained empty money because the saved draft used the exact
native Korean amount unit as its currency value. All twelve independently proved the
complete name cell, unscaled integer, amount column and explicit unit; the ISO-only
currency guard prevented the otherwise valid conversion. This is a known processing
limitation, not missing user documentation.

Explicit v14 / normalization v8 accepts that exact source unit only after the unchanged
same-row proof. It creates a separate scaled amount/KRW draft for fresh verification.
Foreign or differing units, invented context and old raw responses remain unchanged.
The API separately proves the original numeric cell and unit, old v13 program removal,
source/generation/association, fresh verifier and unchanged ledger/user-review versions.
Only the absent money pair and version/time may change. Historical v1-v13 behavior stays
unchanged; schema 0083 permits the new explicit processing revision.

## Evidence and remaining acceptance

PR #106 protected execution: schema 0082, 8 ranges retained as REVIEW, 37 new Riders,
selected contract 16 to 53. Additional journal 22 requests / USD 0.52531240. Its canonical
refresh created 34 links, reaching 50 selected / 59 global, then the helper's overly
broad all-53 expectation failed. Ten amount/currency conflicts are empty native pairs;
five display-name conflicts remain. Three native rows have no proven canonical link.
These partial links are not reported as complete identity reconciliation.

The first authenticated app helper failed on its own wrong Evidence response field
name before creating an event. Correcting that field and using the actual 50-link
boundary passed the final AI-off read/event/history checks in 67.883 seconds: native53,
combined100 to unique50, source excerpt available, previous insurance/review/claim rows
preserved, external HTTP/provider jobs0. The generic planned-event sample had total191,
evaluated0, unsupported191 and candidates0; this does not establish useful claim guidance.
The separate rule-publication reader is being inspected. The catalog's executable fact
flags are intentionally false and are not a defect or an authorization to promote data.

Finish this bundle's implementation/tests/docs before one focused verification pass.
Use affected-only reruns after failures and final required CI for full suites. No actual
source text, values, identities or paths are recorded here. Protected data remains in
the authorized owned clone; no live switch or release has occurred.


## Focused verification

On `9ff1a32` plus the final API/replay/schema/runtime/test/doc wiring, the following
unit selection passed 192 cases (2 deselected, 6.50 seconds), and Mypy passed 369 files:

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_source_unit_currency_draft.py workers/analyzer/tests/test_policy_draft_recovery.py apps/api/tests/test_missing_amount_enrichment.py apps/api/tests/test_guidance_amount_source.py apps/api/tests/test_policy_currency_enrichment.py apps/api/tests/test_runtime_schema_readiness.py workers/analyzer/tests/test_runtime_schema_readiness.py -q --tb=short
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
```

The first focused PostgreSQL run had two failures and one pass (18.95 seconds): the
two publication cases started against the still-0082 synthetic test database and v14
was correctly refused. The migration roundtrip/history case then passed and left the
dedicated synthetic database at 0083. No product-code change was needed. Only the two
failed publication/user-correction cases were rerun: both passed in 21.43 seconds.
Thus three distinct new PostgreSQL cases passed across the two runs.

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_missing_amount_enrichment_integration.py workers/analyzer/tests/test_source_unit_currency_revision.py -m integration -k 'v14 or source_unit_currency_migration' -q --tb=short
TMPDIR=/tmp uv run pytest apps/api/tests/test_missing_amount_enrichment_integration.py -m integration -k v14 -q --tb=short
```

Both used the owned PostgreSQL 18 synthetic test container and explicit destructive-test
opt-in, never the private database. Packaging checks passed: documentation50, repository
safety1203 paths, Ruff lint/format986 files, and diff whitespace. Required final CI will
supply full-suite evidence without duplicate full local tests.

The separate reader diagnosis found the 191-input denominator is private94 + operational147
minus canonical50, not duplicate insertion of the private context. This member has no
published rules/calculations; other current members have existing supported publications.
Final representative use must use a previously supported event and still retain this
member's unsupported count in the complete source/support report.

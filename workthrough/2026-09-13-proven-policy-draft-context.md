# Proven policy draft context

- Status: implementation and focused verification complete; final PR CI and protected application pending.
- Scope: B02 Task 3/4 / WP03 #63, with final activation/acceptance still #69/#70.
- Base: PR #105 source `f1ffb50a49af43d42ad1d02d33ebffef89691075`.
- Branch: `fix/proven-policy-draft-context`.

## Problem and planned outcome

A verifier can cite an actually supplied, program-proven table header which was not
attached to the original draft field yet. The strict validator then correctly rejects
an out-of-draft citation but the draft preparation failed to include necessary context.
Attach only independently proven, value/type-preserving field context before a fresh
verification. Foreign and unrelated Evidence remain rejected.

Stored responses can also retain otherwise valid candidates with a missing or incorrect
primary-range assignment. Explicit v13 reconciles only uniquely source-proven mappings,
preserving the original JSON, losses, uncertainty and earlier review outcomes. v1-v6
semantics are unchanged. Schema 0082 admits the new explicit path without auto-scheduling.

## Protected diagnosis

The preceding owned schema-0081 operation filled seven existing empty money pairs and
added nine Riders. Its selected contract has 16 canonical native/private identities;
only two display-name conflicts remain. Another selected contract retains nine links.
The additional authorized journal is USD 0.39536340 / 19 requests, target USD 1 / hard 2.

For the next repair, one saved range has eight raw candidates with three unassigned IDs;
all 32 range IDs are present and none are foreign or duplicated. Another has 14 candidates
and one candidate/range link without that primary in its fields. A third has 17 provider-
approved candidates: every extra verifier citation is exactly the supplied header context
independently added by the grounder; foreign IDs are zero. All earlier failed states remain
preserved. These are developer diagnoses, not additional model calls or holdout samples.

## Validation and application plan

Finish pure normalization, replay/runtime/migration, API compatibility, synthetic tests
and documentation first. Per-commit syntax/format/scope checks only. At PR completion,
run one focused check set, rerun only affected failures and reuse final required CI.
Then explicitly reverify changed drafts on the approved owned clone within the durable
additional USD ceiling. Actual private files/identifiers/values never enter Git or logs.

Terms identity, full supplied-catalog support and final live release remain separate
uncompleted acceptance conditions. No live source switch or tag is made by this bundle.

## Focused verification and corrections

At `6aad13a` plus its final test edits, the first related unit run had 117 passes and
two fixture failures (4.45 seconds). The partial-primary fixture now compares the
unchanged v6 partial result, and the missing-header fixture removes context while
preserving all declared primary identities. Only those two cases were rerun: 2 passed
in 0.88 seconds. Mypy passed 369 source files.

The first focused PostgreSQL run had 2 passes and 5 failures (49.06 seconds, 13
deselected). A fixture tried to deepcopy an immutable MappingProxy payload; its copy
was corrected. The substantive failure was a non-idempotent table proof: a primary
header in the prepared name references was counted as an enrollment row on re-ground,
turning unchanged fields into INVENTED_FIELD and preventing publication/preservation.
A small synthetic reproduction confirmed the status change with identical fields and
no provider/DB calls. The fix is opt-in grounding v5 for v13, leaving historical v1-v4
interpretation unchanged. Only affected checks will be rerun.

A single-case synthetic publication trace (9.15 seconds) confirmed candidates were
excluded before the API publication loop; it was a failed diagnostic, not additional
passing verification. No actual data or external provider was used for these checks.


At `e908176` plus the final v13-only grounding wiring and fixture edits, the related
unit run passed 131 cases (2 deselected, 1.74 seconds), and Mypy passed 369 source
files. The five affected PostgreSQL cases then passed (7 deselected, 47.50 seconds).
Together with the two unaffected passes from the first integration run, all seven
distinct focused PostgreSQL cases passed; this was not a single seven-case run.

Commands used for the final affected checks:

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_primary_header_context_grounding.py workers/analyzer/tests/test_policy_draft_recovery.py workers/analyzer/tests/test_policy_table_grounding.py workers/analyzer/tests/test_scoped_policy_verifier.py apps/api/tests/test_missing_amount_enrichment.py apps/api/tests/test_guidance_amount_source.py -q --tb=short
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
TMPDIR=/tmp uv run pytest apps/api/tests/test_missing_amount_enrichment_integration.py apps/api/tests/test_scoped_policy_verifier_integration.py -m integration -k v13 -q --tb=short
```

PostgreSQL checks used the existing owned, explicitly destructive-enabled synthetic
PostgreSQL 18 test database, schema 0082. Public fixtures and mock provider responses
were used. Full suites, Web checks and remaining required checks are delegated to
final PR CI, without duplicate local full-suite execution.

Final packaging checks on the same code plus this report passed: documentation
(50 files), repository safety (1199 paths), Ruff format (982 files), Ruff lint,
`git diff --check`, and branch conventions. These checks do not replace required CI.

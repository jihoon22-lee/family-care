# Proven policy draft context

- Status: implementation in progress; focused verification not started.
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

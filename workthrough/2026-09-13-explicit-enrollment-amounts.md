# Explicit enrolled amount recovery

- Status: implementation in progress; detailed PR verification not started.
- Scope: B02 Task 3/4, WP03 #63; final activation/acceptance remains #69/#70.
- Base: PR #103 source `2aa9d22430bcf00175f1a816bcbce2980686d31b`.
- Branch: `fix/explicit-enrollment-amounts`.

## Problem and intended behavior

A retained draft used the unscaled number from an explicitly unit-labelled enrollment
row. The earlier normalizer correctly removed unsupported amount fields; absent
currency and amount remained absent in the ledger. Currency-only enrichment cannot
recover this case because it intentionally requires the original amount to remain equal.

The explicit v12/v6 path derives a new draft only from the same complete native row,
its exact name cell and unambiguous numeric/unit proof. It preserves the original raw
response, earlier rejection and field evidence. A fresh independent verifier and API
source proof are required before filling both empty ledger fields. Existing values,
user decisions, source/member identity and other ledger fields remain protected.

## Implementation bundle

- Same-row explicit-unit normalization, including jointly proven name recovery.
- v12 replay from unchanged v2–v11 source envelopes and preservation of earlier decisions.
- Independent API proof for filling absent amount/currency with append-only publication.
- Function-only schema 0081 admission and matching API/Worker readiness.
- Synthetic positive/negative, source-history, replay and migration coverage.

## Protected diagnosis boundary

Read-only diagnosis of one approved original contract found seven existing native
Riders with absent amount/currency and one review candidate. All eight raw candidates
cite complete native table rows with table-header context. The original raw request
was retained v2 and its v4 replay receipt used normalization v1. Unscaled raw amounts
explain the earlier rejection; this was not missing source text.

The same source contains independently named additional enrolled rows in pending
ranges. Pending processing is not a completed-run omission. One physical name geometry
remains unresolved. Private source values, identifiers and reports stay outside Git.
No provider request was used for this diagnosis; additional budget remains USD 0.2429662.

## Verification

Per-commit syntax/format/plan-scope checks only while implementation proceeds. Once
this bundle is complete, perform one focused detailed pass and reuse matching required
CI. Failed checks are retained and only affected checks rerun. The prior PR's CI is
independent evidence for its prior source and does not cover this implementation.

## Remaining acceptance

Apply the completed bundle to the approved owned clone, recover supported existing
fields and finish the selected pending ranges within the additional USD 1 target / 2
hard ceiling. Source/canonical and terms identity acceptance must reflect actual proof;
this bundle alone does not complete the full supplied catalog or final release.

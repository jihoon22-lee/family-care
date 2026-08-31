# Advisory Coverage Calculation Design

**Status:** Approved by the user for implementation

## Goal

Make every certificate-derived benefit coverage available to the decision result instead of
classifying incomplete deterministic-rule publication as a blocked product state. Preserve the
distinction between a conditional calculation and a confirmed eligibility decision so that the
application computes useful amounts without silently deciding the insurer's final liability.

## State model

`private_knowledge_coverage_execution_dispositions` accepts four values:

- `PUBLISHED`: exact executable eligibility rules and any calculation publication passed the
  publication gate.
- `ADVISORY`: the coverage is admitted by either the immutable certificate snapshot or a
  publication-scoped user enrollment confirmation and is available to search, recommend, and
  display, but one or more exact eligibility or calculation rules still require review. It is
  conditionally calculated only when a directly reviewed, cited calculation publication already
  exists.
- `BLOCKED`: legacy or exceptional publication failure only. A current reviewed publication must
  not use this value for ordinary rule-review work.
- `NOT_APPLICABLE`: a non-benefit component that is not a claim candidate.

Publication remains append-only. Existing historical runs keep their original disposition. A new
current publication supersedes the prior current run and converts reviewed `BLOCKED` coverage rows
to `ADVISORY` without inventing eligibility rules or citations. A user confirmation changes only
the publication's effective coverage-enrollment admission. It does not rewrite the certificate
snapshot or promote unresolved mapping, document identity, edition, section, or overall decisions.
Raw `NO_MATCH` enrollment can never be overridden.

## Calculation model

For a fixed-benefit coverage:

1. A `PUBLISHED` candidate whose required rules match uses its reviewed calculation publication
   and remains `CALCULATED`.
2. An unresolved `PUBLISHED` or `ADVISORY` candidate may expose a conditional amount only when its
   reviewed calculation publication and citations are sufficient to execute the same deterministic
   formula. The candidate stays `UNKNOWN`, `confirmed_amount` stays null, and the unresolved reason
   remains attached.
3. A certificate insured amount is reference evidence, never a generic benefit-payment formula.
   A catalog-only advisory row with no reviewed calculation publication has no calculated amount.
4. A decisive `NO_MATCH` candidate is not calculated.
5. Indemnity coverage still requires receipt, deductible, rate, limit, and allocation inputs; an
   insured amount or limit is not presented as a payable indemnity amount.

The response's `conditional_fixed_subtotals` deliberately includes these executable conditional
fixed amounts. Its name and each calculation's null `confirmed_amount` distinguish the result from
an insurer-confirmed payment. Catalog-only rows and indemnity estimates never enter that subtotal.

## API and UI

The v2 decision response adds `advisory_coverage_count` and retains
`blocked_coverage_count` for legacy evidence. The result page labels advisory coverage as enrolled
and searchable but rule-incomplete. It labels an executable amount on an unresolved candidate as a
conditional estimate rather than confirmed payment. Event result cards omit catalog-only rows with
no evaluations and no calculation; the completeness panel still reports their catalog count.

## Privacy and authority

- No source document, extracted clause text, real identity, policy number, amount, path, or Drive ID
  enters Git, tests, logs, or release metadata.
- Certificate enrollment, current contract confirmation, terms applicability, and
  publication-scoped enrollment authority remain separate fields. `ADVISORY` does not rewrite any
  immutable source value.
- `CERTIFICATE_SNAPSHOT` authority requires certificate enrollment `MATCH`.
  `USER_CONFIRMED_COVERAGE_ENROLLMENT` may admit a certificate enrollment `UNKNOWN` only for the
  current advisory publication, records the confirming actor, and remains visible in the
  publication reconciliation counts and result evidence.
- The result exposes the recorded calculation and its assumptions; a human decides whether the
  event satisfies the policy terms.

## Verification

- Migration upgrade/downgrade and PostgreSQL integration tests cover the new disposition and
  decision snapshot columns.
- Package, reconciliation, repository, domain, schema, and generated-contract tests prove advisory
  closure without weakening the `PUBLISHED` rule/citation gate.
- Authority-matrix tests reject certificate-unknown without user confirmation, user override of a
  certificate `NO_MATCH`, user authority on `PUBLISHED`, and user authority for non-benefit rows.
- PostgreSQL tests prove raw certificate `UNKNOWN` remains unchanged while an advisory
  recommendation stores the publication disposition, authority, and confirming actor lineage.
- Engine tests prove that only reviewed fixed formulas can calculate conditionally, that catalog-only
  and indemnity rows cannot create amounts, and that decisive `NO_MATCH` behavior is unchanged.
- Web tests prove the advisory and conditional labels.
- A protected external publication is dry-run, restored-database verified, applied once, and
  checked through the authenticated HTTP and browser paths before release.

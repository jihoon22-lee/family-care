# Synthetic claim guidance benchmark v1

## Purpose and provenance

These 20 cases were invented from scratch for B01 / issue #61 and requirements
R01/R07/R09/R10/R19/R20. They contain no real contract, person, document, amount,
or extracted wording. The invented amounts use `synthetic-credit`, not currency.
The fixture is an independent expectation set; an engine adapter must create
predictions by executing the engine against `scenario_parameters`. Copying
`expected_primary` or `expected_conditional` into predictions is only scorer
calibration, never product evaluation.

The targets below were selected before running or tuning an engine against this
fixture, on 2026-09-07 against baseline source
`77bc1cd9946328f28f2d36cbae15ffccdbc5d41e`. They are acceptance targets, not a
measured claim about product accuracy. Changes require an explicit fixture version
and rationale; difficult cases must not silently disappear from the denominator.

## Frozen targets and metric definitions

| Metric | Target | Denominator |
|---|---|---|
| Candidate recall | at least 0.90 | All expected primary and conditional candidates |
| Primary precision | at least 0.90 | All emitted primary candidates |
| Conditional precision | at least 0.90 | All emitted conditional candidates |
| Unrelated event false recommendation rate | at most 0.05 | Cases with no expected candidate in either group |
| Unnecessary hold rate | at most 0.05 | All `answerable` cases |

Recall allows recovery in either group. Each group's precision requires the
correct label: promoting a conditional candidate to primary is an error, as is
putting a primary candidate in the conditional group. An unrelated event fails
when any candidate is emitted. An answerable case is unnecessarily held when
`held` is true, or when it has expected candidates but none is recovered. Emitting
an unrelated candidate cannot hide a hold. An answerable negative case with no
output and `held=false` is a valid answer.

Metrics retain numerator and denominator. A zero denominator yields JSON `null`,
not an invented 1.0. No emitted predictions for a group with expected positives
fails that precision target. A genuinely absent group has no applicable precision
threshold, and a subset without related or unrelated events cannot measure the
corresponding metric. Reports include total cases and output count. Full v1 and
both splits contain positive, conditional, and negative cases so every target
is exercised. Candidate metrics are micro-averages; event rates count cases.

## Input schema

`cases.v1.json` is a JSON array. Every case contains exactly:

- `case_id`: unique synthetic case key.
- `split`: `dev` or `holdout`.
- `contract_group`: identity group wholly contained in one split.
- `scenario_id`: representative product scenario `S01` through `S14`.
- `candidate_pool`: every available candidate key, including relevant and
  irrelevant candidates. A list with unique elements.
- `expected_primary`, `expected_conditional`: independent, disjoint subsets of
  the pool. An empty pair is an intentional negative answer.
- `answerable`: whether a useful response is possible; this can be true when the
  correct answer is no related candidate, or when an amount is unavailable.
- `scenario_parameters`: adapter input, described below.

Each prediction contains `case_id`, `primary`, `conditional`, and optional
`held` (default false). Holding and emitting candidates simultaneously is invalid.
Predictions must cover every case exactly once even when scoring only one split.
Missing, extra, unknown, duplicate, or overlapping predictions are rejected before
split filtering. Case and candidate identifiers match `[a-z][a-z0-9-]{0,63}`.
The limits are 1,000 cases, 128 candidate keys per list, and 1 MiB per JSON input.
Duplicate JSON fields, overlapping contract groups or candidate identities across
splits, unknown case fields, and malformed flags are rejected.

### Scenario parameters for an engine adapter

`event_facts` maps explicit field paths to synthetic scalar values. Absence is
unknown; false is an explicit negative. `coverages` defines each candidate:

- `coverage_key` links the candidate pool to this input specification.
- `event_field` / `event_value` is an explicit relevance condition. Matching this
  condition establishes the event anchor. An unrelated or missing event anchor
  must not make the entire catalog conditional.
- Optional `required_field` / `required_value` adds a separate condition. When the
  event anchor matches, missing this field makes the candidate conditional, and
  explicit mismatch excludes it. Planned performance is distinct from actual
  performance; neither can be silently fabricated from the other.
- `enrollment`: `confirmed` or `not_enrolled`.
- `subject`: `same_member` or `other_member`.
- `status`: `unknown` allows a document-maintenance assumption; `active` is
  confirmed; `terminated` contradicts coverage at the event; `active_at_event`
  preserves historical coverage despite current termination; `conflicting`
  keeps only conditional guidance for the affected candidate.
- `benefit_type`: `FIXED` or `INDEMNITY`.
- `source_count`: duplicate evidence source count, not duplicate benefits.
- Optional `contract_identity`: separately subscribed synthetic contracts remain
  distinct even when their benefit conditions match.
- Optional `calculation`: invented formula inputs and, when calculable, an
  independently stated `expected_amount`. These values are **not scored by this
  candidate scorer**. Supported kinds are fixture intentions: `fixed`, `daily`,
  `ratio`, and `indemnity`. Missing cost does not erase a relevant candidate.

`ai_enabled` is false throughout. An adapter must establish actual external-call
count separately; a fixture flag cannot prove no provider call occurred.
`aggregation=no-indemnity-sum` records the S08 requirement separately from
candidate membership. Calculation units, rounding, duplicate-source processing,
contract joining, actual status intervals, and source citation correctness need
engine/integration assertions; loading a case does not verify them.

## Splits and representative coverage

Ten development and ten holdout cases use distinct synthetic contract identities
and candidate keys. The holdout changes event and contract structures instead of
using nearby sentence paraphrases of development cases. These are deterministic
engineering partitions, not a claim of unseen clinical or statistical validity.
No raw-document corpus or text parser is tested by this structured fixture.

| Scenario | Development intention | Holdout intention |
|---|---|---|
| S01 | Unknown current status, first-claim condition | Renewal uncertainty, frequency condition |
| S02 | Terms-only diagnosis benefit | Unenrolled fracture benefit |
| S05 | Planned surgery and explicitly negated surgery | Incomplete diagnosis and negated admission |
| S06 | AI-disabled diagnosis | AI-disabled fracture |
| S07 | Daily amount with deducted days and day cap | Ratio with reduction and amount cap |
| S08 | Known partial cost plus missing second cost | Two indemnity candidates without costs |
| S09 | Same coverage from two evidence sources | Two genuinely distinct contracts |
| S10 | Another member's coverage | Unrelated routine event |
| S11 | Historical active interval, terminated alternative | Conflicting status, terminated alternative |

S03/S04/S12/S13/S14, real PDFs, external AI review, protected-data conversion,
Windows/mobile, latency, full-catalog support, and money/evidence correctness are
outside this scorer's executed coverage. Downstream WPs own those verifications.

## Invocation and Python API

```bash
TMPDIR=/tmp uv run python scripts/claim_guidance_benchmark.py --negative-controls
TMPDIR=/tmp uv run python scripts/claim_guidance_benchmark.py --predictions /tmp/synthetic-predictions.json --split holdout
```

`load_cases(path)` returns `tuple[BenchmarkCase, ...]`; `load_predictions(path)`
returns `tuple[Prediction, ...]`. `score_predictions(cases, predictions,
split=None)` returns a `BenchmarkResult` with five `Metric` values, case/output
counts, `failures`, and `passed`. `as_dict()` includes JSON-ready metric values.
`negative_controls(cases)` returns deterministic `all_hold`, `all_primary`, and
`all_conditional` predictions over the full candidate pools.

Scoring exits 0 for passing targets, 1 for failed targets, and 2 for invalid input.
Negative-control mode exits 0 only when **every** intentionally broken control
fails. This proves the scorer detects those failure modes, not that the product
meets the targets. Run tests for control failure in both individual splits too.
No command sends data to another service; outputs are aggregate scores only.

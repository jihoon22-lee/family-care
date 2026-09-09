# v0.5 acceptance evidence

Optional review instructions now spell out the source proof already required by the API: exact
single-citation statements, exhaustive supplied-region accounting and citation-backed relationships.
API and Worker use prompt revision v3; prior prompt jobs cannot silently run new instructions or
spend budget, while stored jobs/results retain their original identity. Proposal schema, source
verification and calculation authority are unchanged. The real SDK bridge now exercises executable
proposals and rejects missing relation citations and joined statements without losing the original.

The B01–B07 record is connected to R01–R20/S01–S14 in the verification document and active B08 plan.
B07 merged as `18dc981` after `04fbbda` passed all seven required CI checks. Protected-source,
device, broader quality and release gaps remain; this change does not complete the milestone or
publish version 0.5.0.

At `04fbbda`, the frozen development/holdout candidate benchmark passed its fixed local targets;
all-hold/all-primary/all-conditional controls failed the targets as intended. The same adapter and
data on frozen baseline `77bc1cd` failed the targets, with all 32 loaded API module paths/bytes
verified against Git. The verification document records the exact commands, numerators/denominators,
output counts and engine-only timing limits. Protected clone ASGI/Chromium and post-backfill
preservation evidence remain separate from synthetic checks and from live activation.

Four authorized real-provider requests diagnosed the same synthetic development case through the
guarded disposable PostgreSQL, existing queue/budget/SDK/projector and zero SDK retries. The first
failed response validation; the next two produced opinions with no recovered candidate. Offline
replay isolated a missing cross-region reference citation, then a joined statement. Restoring only
the exact statement in the latter replay changed verified nodes/edges from 3/0 to 4/3 and recovered
300, without external calls. Final prompt v3 recovered one candidate and the locally recalculated
expected 300 in one real request (partial reviewed scope), while preserving the original. All earlier
failures remain recorded; this is one development sample, not four independent cases or model-wide
accuracy. Total actual usage was 15,707 input / 7,077 output tokens; no real document was transmitted.

At `71931a1` plus the final API/Worker prompt, tests and documentation diff, related Worker tests
passed 68/68 in 2.67s and the SDK/Worker/API PostgreSQL bridge passed 3/3 in 27.08s. Old v1 and v2
no-send checks each failed before their revision change. The final real request took 22.977s and
used 4,010 input / 1,859 output tokens; the wrapper passed in 32.12s, including fixture setup.
Full PR validation is recorded below when complete. Package versions and runtime configuration
remain unchanged. Native binding acceptance and remaining B08 checks precede release/deployment.

At the same `04fbbda` source, the protected clone also passed authentication, stored-result display,
320/1280px layout and claim-draft creation/reload in the Windows host's Chrome headless, using an
isolated incognito context and temporary local TLS gateway. Observed external HTTP, AI POSTs,
page errors, sensitive storage/cache writes and screenshots/traces were zero. The first Windows
launch failed on package resolution; explicitly loading the existing Playwright core entry point
resolved it without changing product dependencies. Mobile hardware and PWA installation remain
unverified. The temporary API server and owned browser were stopped after the check.

The existing protected ASGI acceptance also passed a sequential same-input idle/load comparison,
with source verification reading the restored target on the same PostgreSQL instance. Only the
acceptance clone received API writes; target connections were read-only, the live source was not
connected, and the verifier stopped and joined. Input identity, persisted-result equality and zero
external HTTP/AI job delta were checked. Timings and process RSS/CPU remain in the private report;
this is a warm read-contention observation, not cold DB, gateway or import/OCR/review-load acceptance.

Final local checks at `71931a1` plus the recorded implementation/test/documentation diff:
Web check passed 238 tests in 31 files and the production/PWA build; Ruff format/lint passed,
mypy passed 353 source files, and default pytest passed 3,588 tests plus 3 subtests in 37.91s
(766 integration tests deselected). Contracts, container definitions, workflow policy and diff
checks passed. A line-length failure was fixed by splitting adjacent string literals; the final
instruction was compared byte-for-byte with the successful saved synthetic provider request.
The focused PostgreSQL result above is separate from the full integration CI still pending.

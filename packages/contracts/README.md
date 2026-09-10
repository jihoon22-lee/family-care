# FamilyCare Contracts

This directory contains the versioned contracts shared by the FamilyCare web,
API, and analyzer services.

- `openapi/` is generated from the FastAPI application and committed so drift is reviewable.
- `schemas/` contains transport-neutral JSON Schemas for analysis jobs, document ingestion and
  extraction, encrypted batch status, OCR provenance, policy ledger/candidate review, insurance
  document inventory, integrated insurance reconciliation, clause search, Rider-Clause rules,
  coverage decision v1/v2 (including optional local guidance v1 snapshots), benefit
  calculation, medical-event structuring, claim workflow, and the non-executable household-scoped
  private-knowledge catalog.
- `examples/` contains synthetic examples that must not include real insurance or family data. Queue examples are password-free and do not contain `content_sha256` before Worker intake; encrypted batch examples contain only opaque source IDs and bounded status projections.
- `apps/api/src/familycare_api/documents/generated_contracts.py` and `workers/analyzer/src/familycare_worker/generated_contracts.py` are deterministic TypedDict consumers generated from the Phase 1 document schemas; do not edit them manually.
- `apps/api/src/familycare_api/documents/generated_batch_contracts.py` and `workers/analyzer/src/familycare_worker/generated_batch_contracts.py` are deterministic TypedDict consumers generated from the encrypted batch schemas; do not edit them manually.
- `schemas/document-metadata-proposal.v1.schema.json` owns the protected local component proposal
  shape and explicit role/field vocabulary. Regenerate both `generated_metadata.py` consumers with
  `TMPDIR=/tmp uv run python scripts/generate_document_metadata_contract.py`. Its independent
  revision does not change retained IR identity. These proposals classify source content only;
  the API rechecks original anchors, conflicting values, dates and page boundaries before use.
- `apps/api/src/familycare_api/contracts/generated_business.py` includes deterministic TypedDict
  consumers for the shared policy, review, decision, calculation, claim, and private-knowledge
  contracts; do not edit it manually.
- `apps/web/src/api/generated.ts` is the deterministic TypeScript consumer generated from the canonical OpenAPI document; do not edit it manually. Candidate review and Clause search operations appear there only after their FastAPI routes are registered.

Run `TMPDIR=/tmp uv run python scripts/check_contracts.py` to validate the committed artifacts,
including schema/example privacy rules, safety-limit metadata, generated-type drift, and the canonical
OpenAPI document. The generated snapshot is the source for the current operation and component
inventory; counts are not a compatibility promise.

Local guidance is generated from `familycare_api.guidance.models` through the decision response.
Regenerate its neutral schema with `TMPDIR=/tmp uv run python scripts/check_contracts.py
--write-decision-schema`, OpenAPI with `--write-openapi`, and then the Web consumer. Existing v1/v2
snapshots without `local_guidance` retain their prior meaning; new guidance carries its own version,
document-based assumptions, evidence, candidate relevance and independent estimate readiness.

Claim preparation accepts either the legacy Rider selector or a stored local-guidance run,
event version and canonical coverage reference. The API copies the selected candidate, cases,
scenarios, calculation trace and versions into `ClaimLocalGuidanceSnapshot`; recorded payment
remains separate. Regenerate the embedded neutral snapshot contract with
`TMPDIR=/tmp uv run python scripts/check_contracts.py --write-claim-schema`, then OpenAPI and the
Web consumer. Claim and payment history require exactly one complete operational or private
coverage source. Historical operational snapshots and hashes remain unchanged.

To regenerate only the document TypedDict consumers, run
`TMPDIR=/tmp uv run python scripts/generate_document_contract_types.py`; to regenerate encrypted
batch consumers, run `TMPDIR=/tmp uv run python scripts/generate_batch_contract_types.py`. Generate
the private-knowledge JSON Schema with
`TMPDIR=/tmp uv run python scripts/generate_private_knowledge_contract.py`, then regenerate the shared
business TypedDicts with `TMPDIR=/tmp uv run python scripts/generate_business_contract_types.py`. The
focused batch checker is `TMPDIR=/tmp uv run python scripts/check_batch_contracts.py`. To check or
regenerate the Web consumer, run
`TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py --check` or omit `--check`. Use
`--write-openapi` only after intentionally changing the API contract; operations must be emitted by
FastAPI rather than hand-edited into committed artifacts.

The OCR contract is deliberately separate from native extraction. Run
`TMPDIR=/tmp uv run python scripts/check_ocr_contracts.py` for its focused provenance and privacy gate.

Detailed terms candidates use `schemas/terms-semantic-knowledge.v1.schema.json` as their
canonical contract. Run `TMPDIR=/tmp uv run python scripts/generate_terms_semantic_contract.py`
to regenerate the strict API/Worker consumers; `check_contracts.py` detects drift.
The same schema defines bounded `SemanticWorkEnvelope`/`SemanticWorkRegion` inputs for
asynchronous candidate work, including source identity, whole supplied regions, primary scope,
and the complete expected-region manifest. These internal fields are not a provider payload;
the Worker must minimize identifiers and text before an authorized external call.
A valid candidate or citation does not confer rule authority: source identity, exact original
spans, field meaning and dependency relations require independent validation before compilation.

Explicit `MedicalEvent.treatment_kind` semantic conditions use only `equals`, no unit, and
`surgery`, `admission` or `outpatient`. They preserve a source activity separately from
clinical classification codes and generic performed observations. Compiler v2 requires schema
`0070_semantic_activity`; earlier publications stay immutable. `diagnosis_confirmed` event
inputs accept only true, false or unknown, and AI proposals still require user confirmation.

Compiler v3 / meaning v4 additionally preserve source-backed covered-receipt reimbursement
and conditional reduction before deduction, amount cap and rounding. Schema
`0071_source_calculations` admits v3 publications without changing v1/v2 history.
The data-only `if` calculation takes exactly a `MedicalEvent.reduction_applies` Boolean
field and two numeric branches. A missing, stale or unconfirmed condition cannot select a
branch; Boolean values never become numeric amounts. Guidance traces retain the actual
Boolean and the selected branch's original AST address. The explicit user event fact uses
the existing `facts` object and does not expand AI structuring fields. Covered receipt
amounts still require the normal confirmed receipt source and matching currency.

Metadata v10 / `0072_metadata_physical_flow` separates retained extractor ordinals
from independently proven physical reading order. Original words, geometry and
component history remain unchanged. The API inherits v9 word-lineage checks,
validates the new physical prefix and body flow independently, and passes the
stored revision through Clause and semantic source readers. Covered-person
conditional sentences identify content only. Product captions retain their full
bounded variant suffixes; neither classification establishes enrollment or edition
applicability. A v10 proposal or publication prevents downgrade to 0071.


Metadata v11 / `0073_metadata_header_regions` adds an independently proven first
header flow for separated physical columns. A strict company/product caption or
valid explicit field may precede the required formal title through the same bounded
adjacent flow; a broad cover prelude alone cannot authorize it. It preserves complete source spans,
original page reference context, unresolved fields outside the flow, and all prior
revision semantics. Both consumers bound table representation and flow comparison
work; exhausted proofs never return a truncated success. A role without printed
identity remains role-only, and v11 proposal/publication history blocks downgrade.

The schema's `x-insurer-caption-vocabulary` records exact public association captions,
source sections, observation date and vocabulary version. Generated API/Worker
consumers use it only in v11, with complete header proof and existing caption/role
adjacency. It is not a legal-name/alias registry or an applicability decision; source
links and the inference boundary are in [the design](../../docs/design/clause-linking-search.md).

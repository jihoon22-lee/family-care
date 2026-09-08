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

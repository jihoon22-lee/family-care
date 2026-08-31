# FamilyCare Contracts

This directory contains the versioned contracts shared by the FamilyCare web,
API, and analyzer services.

- `openapi/` is generated from the FastAPI application and committed so drift is reviewable.
- `schemas/` contains transport-neutral JSON Schemas for analysis jobs, document ingestion and
  extraction, encrypted batch status, OCR provenance, policy ledger/candidate review, insurance
  document inventory, clause search, Rider-Clause rules, coverage decision v1/v2, benefit
  calculation, medical-event structuring, claim workflow, and the non-executable household-scoped
  private-knowledge catalog.
- `examples/` contains synthetic examples that must not include real insurance or family data. Queue examples are password-free and do not contain `content_sha256` before Worker intake; encrypted batch examples contain only opaque source IDs and bounded status projections.
- `apps/api/src/familycare_api/documents/generated_contracts.py` and `workers/analyzer/src/familycare_worker/generated_contracts.py` are deterministic TypedDict consumers generated from the Phase 1 document schemas; do not edit them manually.
- `apps/api/src/familycare_api/documents/generated_batch_contracts.py` and `workers/analyzer/src/familycare_worker/generated_batch_contracts.py` are deterministic TypedDict consumers generated from the encrypted batch schemas; do not edit them manually.
- `apps/api/src/familycare_api/contracts/generated_business.py` includes deterministic TypedDict
  consumers for the shared policy, review, decision, calculation, claim, and private-knowledge
  contracts; do not edit it manually.
- `apps/web/src/api/generated.ts` is the deterministic TypeScript consumer generated from the canonical OpenAPI document; do not edit it manually. Candidate review and Clause search operations appear there only after their FastAPI routes are registered.

Run `TMPDIR=/tmp uv run python scripts/check_contracts.py` to validate the committed artifacts,
including schema/example privacy rules, safety-limit metadata, generated-type drift, and the canonical
OpenAPI document. The current `0.3.2` OpenAPI snapshot contains 69 paths, 81 operations, and 150
component schemas; these counts are a review aid rather than a compatibility promise.

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

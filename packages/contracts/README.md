# FamilyCare Contracts

This directory contains the versioned contracts shared by the FamilyCare web,
API, and analyzer services.

- `openapi/` is generated from the FastAPI application and committed so drift is reviewable.
- `schemas/` contains transport-neutral JSON Schemas, including the pre-intake `analysis-job.v1`, `document-ingestion.v1`, post-extraction `extraction-result.v1`, separate `ocr-result.v1`, encrypted `document-batch.v1` and `document-batch-status.v1`, versioned `policy-candidate.v1`, and bounded `clause-search.v1` contracts.
- `examples/` contains synthetic examples that must not include real insurance or family data. Queue examples are password-free and do not contain `content_sha256` before Worker intake; encrypted batch examples contain only opaque source IDs and bounded status projections.
- `apps/api/src/familycare_api/documents/generated_contracts.py` and `workers/analyzer/src/familycare_worker/generated_contracts.py` are deterministic TypedDict consumers generated from the Phase 1 document schemas; do not edit them manually.
- `apps/api/src/familycare_api/documents/generated_batch_contracts.py` and `workers/analyzer/src/familycare_worker/generated_batch_contracts.py` are deterministic TypedDict consumers generated from the encrypted batch schemas; do not edit them manually.
- `apps/web/src/api/generated.ts` is the deterministic TypeScript consumer generated from the canonical OpenAPI document; do not edit it manually. Candidate review and Clause search operations appear there only after their FastAPI routes are registered.

Run `TMPDIR=/tmp uv run python scripts/check_contracts.py` to validate the committed
artifacts, including schema/example privacy rules, safety-limit metadata, and generated-type drift. To regenerate only the document TypedDict consumers, run `TMPDIR=/tmp uv run python scripts/generate_document_contract_types.py`; to regenerate encrypted batch consumers, run `TMPDIR=/tmp uv run python scripts/generate_batch_contract_types.py`. The focused batch checker is `TMPDIR=/tmp uv run python scripts/check_batch_contracts.py`. To check or regenerate the Web consumer, run `TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py --check` or omit `--check`. Use `--write-openapi` only after intentionally changing the API contract; candidate operations must be emitted by FastAPI rather than hand-edited into the committed artifact.

The OCR contract is deliberately separate from native extraction. Run
`TMPDIR=/tmp uv run python scripts/check_ocr_contracts.py` for its focused provenance and privacy gate.

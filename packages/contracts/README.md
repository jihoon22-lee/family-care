# FamilyCare Contracts

This directory contains the versioned contracts shared by the FamilyCare web,
API, and analyzer services.

- `openapi/` is generated from the FastAPI application and committed so drift is reviewable.
- `schemas/` contains transport-neutral JSON Schemas, including the pre-intake `analysis-job.v1`, `document-ingestion.v1`, post-extraction `extraction-result.v1`, and versioned `policy-candidate.v1` contracts.
- `examples/` contains synthetic examples that must not include real insurance or family data. Queue examples are password-free and do not contain `content_sha256` before Worker intake.
- `apps/api/src/familycare_api/documents/generated_contracts.py` and `workers/analyzer/src/familycare_worker/generated_contracts.py` are deterministic TypedDict consumers generated from the schemas; do not edit them manually.
- `apps/web/src/api/generated.ts` is the deterministic TypeScript consumer generated from the canonical OpenAPI document and candidate schema; do not edit it manually. Candidate review route operations appear there once their FastAPI routes are registered.

Run `TMPDIR=/tmp uv run python scripts/check_contracts.py` to validate the committed
artifacts, including schema/example privacy rules, safety-limit metadata, and generated-type drift. To regenerate only the document TypedDict consumers, run `TMPDIR=/tmp uv run python scripts/generate_document_contract_types.py`. To check or regenerate the Web consumer, run `TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py --check` or omit `--check`. Use `--write-openapi` only after intentionally changing the API contract; candidate operations must be emitted by FastAPI rather than hand-edited into the committed artifact.

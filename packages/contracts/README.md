# FamilyCare Contracts

This directory contains the versioned contracts shared by the FamilyCare web,
API, and analyzer services.

- `openapi/` is generated from the FastAPI application and committed so drift is reviewable.
- `schemas/` contains transport-neutral JSON Schemas.
- `examples/` contains synthetic examples that must not include real insurance or family data.

Run `TMPDIR=/tmp uv run python scripts/check_contracts.py` to validate the committed
artifacts. Use `--write-openapi` only after intentionally changing the API contract.

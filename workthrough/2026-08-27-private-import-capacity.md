# Private PDF import capacity increase

## Overview

FamilyCare의 private PDF 입력 한도를 25 MiB에서 128 MiB로 확장했다. 관측된 약 81 MiB 문서를 수용하면서 500-page 제한과 parser의 64 MiB output/`RLIMIT_FSIZE`, 1536 MiB address space, 90-second CPU, 120-second wall timeout, 64-descriptor 제한은 그대로 유지했다.

## Context

- 기존 source catalog, Worker intake, 복호화 평문 writer가 25 MiB를 초과한 파일을 거부했다.
- managed archive는 64 MiB까지만 허용하여 intake 한도만 변경해도 큰 문서가 archive 단계에서 실패했다.
- AES-GCM ciphertext는 plaintext와 같은 길이이며 16-byte authentication tag는 `managed_archives.auth_tag`에 별도로 저장된다. 따라서 `ciphertext_size` 제약은 정확히 128 MiB로 설정했다.
- 실제 보험 문서, Drive 식별자, 문서 본문, 개인정보, 인증정보는 이 변경과 검증에 사용하지 않았다.

## Changes made

### Runtime and API bounds

- `workers/analyzer/src/familycare_worker/pdf/limits.py`: `MAX_INPUT_BYTES`를 128 MiB로 변경했다.
- `workers/analyzer/src/familycare_worker/archive/crypto.py`: archive plaintext/ciphertext 한도를 128 MiB로 변경했다.
- `apps/api/src/familycare_api/documents/import_sources.py`: private source catalog 한도를 128 MiB로 변경했다.
- `apps/api/src/familycare_api/documents/batch_router.py`: API projection이 같은 상수를 사용하도록 연결했다.

### Database and contracts

- `apps/api/migrations/versions/0014_private_import_capacity.py`: 기존 archive ciphertext check를 forward migration으로 128 MiB까지 확장하고 downgrade에서 역사적 64 MiB 제약을 복원한다.
- `packages/contracts/schemas/document-ingestion.v1.schema.json`: input safety limit을 128 MiB로 변경했다.
- `packages/contracts/schemas/document-batch-status.v1.schema.json`: import source maximum을 128 MiB로 변경했다.
- `packages/contracts/openapi/familycare.v1.json`: FastAPI OpenAPI를 재생성했다.
- `scripts/check_document_contracts.py`, `scripts/check_batch_contracts.py`, `scripts/check_contracts.py`: 계약 drift 검사가 새 경계를 고정하도록 변경했다.

### Tests

- `workers/analyzer/tests/test_pdf_isolation.py`: exact input limit을 확인한다.
- `workers/analyzer/tests/test_pdf_intake.py`: sparse synthetic PDF의 128 MiB와 128 MiB + 1 경계를 확인한다.
- `workers/analyzer/tests/test_archive_crypto.py`: exact-boundary AES-GCM 처리와 bounded overflow read를 확인한다.
- `apps/api/tests/test_import_source_catalog.py`: source catalog의 exact/overflow 경계를 확인한다.
- `apps/api/tests/test_document_batch_api.py`: Pydantic response bound를 확인한다.
- `apps/api/tests/test_document_batch_contracts.py`, `apps/api/tests/test_document_contracts.py`: committed schema limits를 확인한다.
- `apps/api/tests/test_archive_capacity_migration.py`: 0014 upgrade/downgrade constraint를 확인한다.

### Documentation

현재 safety contract를 다음 문서에 반영했다.

- `docs/architecture.md`
- `docs/design/pdf-ingestion.md`
- `docs/design/private-data-runtime.md`
- `docs/design/security-privacy.md`
- `docs/design/test-strategy.md`
- `docs/guide.md`
- `docs/plan/014a-private-import-reliability.md`

## Key code examples

```python
# workers/analyzer/src/familycare_worker/pdf/limits.py
MAX_INPUT_BYTES: Final = 128 * 1024 * 1024
MAX_PDF_PAGES: Final = 500
MAX_OUTPUT_BYTES: Final = 64 * 1024 * 1024
```

```python
# apps/api/migrations/versions/0014_private_import_capacity.py
_CIPHERTEXT_SIZE_LIMIT = 128 * 1024 * 1024

op.create_check_constraint(
    "ck_managed_archives_ciphertext_size_limit",
    "managed_archives",
    f"ciphertext_size <= {_CIPHERTEXT_SIZE_LIMIT}",
)
```

## Verification results

### Test-first evidence

```text
Focused RED: 12 failed in 17.73s
Reason: old 25 MiB API/Worker/schema limits, old 64 MiB archive limit,
        and missing 0014 migration

Focused GREEN after implementation: 12 passed in 10.49s
Root focused regression after review: 109 passed in 24.99s
```

### Full local gate

```text
documentation contract: passed (45 files)
repository safety: passed (503 paths)
Web: 18 test files, 99 tests passed; production build passed
Ruff format: 338 files already formatted
Ruff lint: passed
mypy: 161 source files passed
pytest: 1151 passed, 104 deselected, 3 subtests passed
contract checks: passed
container definition checks: passed
workflow policy checks: passed
```

## Remaining work

- Rebuild the private API/Worker images and apply migration 0014 before importing large documents.
- Keep actual private-document acceptance outside the repository and report only sanitized aggregate results.
- Connect successful private extraction to Evidence persistence, explicit `policy`/`terms` classification, AI candidate review, and ledger publication before claiming end-to-end policy structuring.

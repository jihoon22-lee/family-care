# Explicit private import document kinds

## Overview

FamilyCare의 private PDF batch가 각 source를 `policy`, `terms`, `supporting` 중 하나로 명시하도록 변경했다. 파일명 추정은 가입 근거로 사용하지 않으며, 선택된 종류를 API, PostgreSQL batch item, Worker document 생성, Web 상태 화면까지 보존한다.

## Context

- 기존 batch request는 opaque `source_id` 목록만 받아 Worker가 모든 private document를 `supporting`으로 생성했다.
- policy 구조화 작업은 증권과 약관을 구분해야 하지만, 약관에 조항이 존재한다는 사실만으로 가입 rider를 판단하면 안 된다.
- API의 기존 100-source 상한을 유지했다. request/status JSON Schema도 100개로 정렬해 계약 불일치를 제거했다.
- 기존 DB 행은 migration 중에만 `supporting`으로 백필하고, migration 완료 후 server default를 제거해 신규 내부 write가 종류를 생략하지 못하게 했다.

## Changes made

### API and persistence boundary

- `apps/api/src/familycare_api/documents/batch_router.py`: request를 `sources: [{source_id, document_kind}]`로 변경하고 중복 ID, 미지원 종류, 추가 필드를 거부한다.
- `apps/api/src/familycare_api/documents/batch_service.py`: catalog에서 해석한 source와 명시 종류를 하나의 selection으로 repository에 전달한다.
- `apps/api/src/familycare_api/documents/batch_repository.py`: raw source fallback을 제거하고 `BatchSourceSelection`만 허용한다. status projection에도 종류를 포함하되 `source_key`는 반환하지 않는다.
- `apps/api/migrations/versions/0015_private_batch_document_kind.py`: checked, non-null `document_kind` column을 추가하고 백필 후 server default를 제거한다.

### Worker

- `workers/analyzer/src/familycare_worker/repository.py`: leased batch item의 종류를 읽고 `documents.document_kind`에 그대로 기록한다.
- `workers/analyzer/src/familycare_worker/imports/batch.py`: Worker protocol에 document kind를 명시한다.
- 합성 Worker/PostgreSQL fixture는 선택된 `policy` 종류가 생성된 document까지 전달되는지 확인하도록 확장했다.

### Contracts and Web

- batch request/status schemas, examples, generated API/Worker/Web types, OpenAPI, contract checkers를 새 per-source contract로 재생성했다.
- `apps/web/src/features/documents/ImportSourcePicker.tsx`: 선택된 source마다 증권, 약관, 보조자료를 고르는 control을 제공한다. 초기값은 보수적인 `supporting`이다.
- `apps/web/src/features/documents/ImportPage.tsx`: 선택 상태와 문서 종류를 함께 제출한다.
- `apps/web/src/features/documents/BatchProgress.tsx`: 처리 중인 각 항목의 명시 종류를 표시한다.
- 관련 unit/E2E 합성 fixture에 필수 종류를 추가했다.

## Key code examples

```json
{
  "schema_version": "1",
  "family_member_id": "00000000-0000-4000-8000-000000000004",
  "sources": [
    {
      "source_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "document_kind": "policy"
    }
  ]
}
```

```python
op.add_column(
    "document_batch_items",
    sa.Column(
        "document_kind",
        sa.String(length=16),
        nullable=False,
        server_default=sa.text("'supporting'"),
    ),
)
op.alter_column(
    "document_batch_items",
    "document_kind",
    existing_type=sa.String(length=16),
    existing_nullable=False,
    server_default=None,
)
```

## Verification results

### Test-first evidence

```text
Initial RED: 3 failed, 5 passed
Reason: per-source document kind was not yet required or persisted.

Initial focused GREEN: 8 passed
Root focused regression after review: 60 passed in 18.47s
```

### Focused checks

```text
Batch contract checker: passed
Full contract checker/OpenAPI drift: passed
Ruff format (15 focused files): passed
Ruff lint (15 focused files): passed
mypy (7 source files): passed
Web Prettier focused check: passed
Web ESLint focused check: passed
Web TypeScript build/typecheck: passed after three synthetic fixture corrections
Web focused Vitest: 2 files, 8 tests passed in 93.83s
Documentation contract: passed (45 files)
Repository safety: passed (508 paths)
git diff --check: passed
```

The first Web typecheck correctly failed because three synthetic `BatchItemResponse` fixtures lacked the new required field. Those fixtures were updated and the rerun passed. An earlier broad Web test attempt was interrupted with exit 130 and is not counted as a result.

## Privacy and runtime boundary

- 실제 보험 PDF, 추출 text, 개인정보, Drive 식별자, password, token은 사용하거나 기록하지 않았다.
- 인증 설정, 비밀번호, 실행 중인 container, import source, archive object는 변경하지 않았다.
- Migration 0015의 실제 PostgreSQL upgrade와 task-owned integration test는 아직 실행하지 않았다.
- 전체 저장소 gate, image rebuild, runtime migration/restart는 후속 작업을 묶은 최종 검증까지 보류한다.

## Next steps

- 성공한 private extraction과 같은 transaction에서 page-addressable `Evidence`를 `NEEDS_REVIEW`로 저장한다.
- policy document에만 별도 retryable structuring job을 enqueue한다.
- 합성 end-to-end pipeline을 완성한 뒤 전체 serial gate와 PostgreSQL migration 검증을 수행한다.

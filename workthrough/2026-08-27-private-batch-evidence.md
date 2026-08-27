# Atomic private batch Evidence persistence

## Overview

성공한 private PDF batch가 extraction/archive 성공 상태만 남기고 policy Evidence를 만들지 않던 단절을 해소했다. 이제 Worker는 검증된 각 물리 페이지에 bbox 없는 `NEEDS_REVIEW` Evidence를 같은 PostgreSQL transaction에서 저장하고, 후속 policy structuring을 위해 OCR provenance를 존중하는 bounded in-memory slice를 읽을 수 있다.

## Context

- 기존 `BatchRepository.mark_succeeded()`는 DocumentVersion, Extraction, native blocks/tables, optional OCR layer, encrypted archive, terminal batch state를 원자적으로 저장했지만 `evidence`에는 아무 row도 쓰지 않았다.
- Policy candidate와 ledger API는 household-scoped Evidence를 요구하므로 extraction 성공만으로는 review/ledger 경로에 진입할 수 없었다.
- Evidence가 약관 또는 증권에 존재한다는 사실은 가입 판단이 아니다. 모든 새 페이지 Evidence는 `NEEDS_REVIEW`이며 candidate structurer나 verifier가 이를 자동 확정하지 않는다.

## Changes made

### Atomic page Evidence

- `workers/analyzer/src/familycare_worker/repository.py`
  - locked batch item query가 parent batch의 `household_space_id`를 함께 가져온다.
  - validated PDF page count와 extraction pages가 정확히 일치하고 1..500 순서인지 검사한다.
  - 각 extraction page insert 직후 같은 transaction에서 `evidence`에 household, DocumentVersion, Extraction, content hash, 1-based page, `NEEDS_REVIEW`를 기록한다.
  - bbox column은 모두 null로 유지한다. 이후 blocks, OCR, archive, document/batch transition 중 하나라도 실패하면 Evidence도 함께 rollback된다.

### Bounded Evidence loader

- `workers/analyzer/src/familycare_worker/ai/evidence_loader.py`
  - household, DocumentVersion, Extraction, matching content hash, successful Extraction, non-deleted policy/terms document를 모두 SQL에서 확인한다.
  - bbox 없는 `NEEDS_REVIEW` page Evidence만 physical page 순서로 읽는다.
  - `OCR_REQUIRED` page는 successful OCR text를 우선하고 empty OCR일 때 native text로 fallback한다. 다른 page는 native text만 사용한다.
  - DB에서 page별 최대 64 blocks를 정규화하고, Python 경계에서 공백을 다시 정규화해 240자로 제한한다.
  - 최대 500 page rows를 검증하고 provider-safe `EvidenceSlice`는 최대 64개만 만든다. duplicate/out-of-order page, foreign version, unsupported document kind는 fixed `EVIDENCE_LOAD_ERROR`로 거부한다.

### Tests

- `workers/analyzer/tests/test_private_batch_evidence.py`: page count, sequential page identity, 500-page bound, household/version/extraction/hash/state tuple을 고정한다.
- `workers/analyzer/tests/test_policy_evidence_loader.py`: 64-slice/240-character limits, whitespace normalization, unique ordered pages, document scope와 kind를 검증한다.
- `workers/analyzer/tests/test_batch_database.py`: fresh synthetic PostgreSQL에서 migration head 후 실제 low-quality PDF import가 page Evidence를 쓰고, loader가 native 대신 synthetic OCR text를 선택하는지 검증한다. cleanup은 Evidence를 먼저 제거한다.

## Key code examples

```python
connection.execute(
    """
    INSERT INTO evidence (
        household_space_id, document_version_id, extraction_id,
        content_sha256, physical_page, review_state
    )
    VALUES (%s, %s, %s, %s, %s, %s)
    """,
    evidence_record,
)
```

```text
page.classification == OCR_REQUIRED
  -> successful OCR text
  -> empty OCR fallback: native text

page.classification == TEXT_SUFFICIENT
  -> native text

all rows -> validate at most 500 -> non-empty first 64 -> 240 characters each
```

## Verification results

### Test-first evidence

```text
Page Evidence RED: collection error; _page_evidence_records did not exist
Page Evidence GREEN: 4 passed in 3.38s

Evidence loader RED: collection error; ai.evidence_loader did not exist
Evidence loader GREEN: 5 passed in 13.53s
Focused combined regression: 20 passed in 16.80s
```

### PostgreSQL acceptance

```text
Fresh PostgreSQL 18.6-alpine: migrations 0001 through 0015 applied
Initial pytest invocation without -m integration: 1 deselected, exit 5 (no result)
Explicit integration rerun after page persistence: 1 passed in 5.59s
Final integration with OCR-preferred loader: 1 passed in 19.38s
Temporary test container removed after each run
```

### Static checks

```text
Ruff format: 5 focused files passed
Ruff lint: 5 focused files passed after removing one unused import
mypy: 2 source files passed
Documentation contract: passed (45 files)
Repository safety: passed (512 paths)
git diff --check: passed
```

## Privacy and authority boundary

- 실제 보험 PDF, 실제 문서 text, 이름, 증권번호, Drive 식별자, password, token은 사용하거나 기록하지 않았다.
- 모든 fixture와 OCR text는 처음부터 만든 합성 값이다.
- Evidence text는 batch API, logs, exceptions, workthrough에 저장되지 않는다. Loader 결과는 아직 provider에 전송되지 않는다.
- 현재 FamilyCare database, API, Worker, Web container와 인증/session 상태는 변경하지 않았다.
- Provider 연결 전 불필요한 가족 표시값, 증권번호, 연락처를 제거하는 최소화 경계는 다음 Task의 필수 항목이다.

## Next steps

- Policy import success와 별도 leased/retryable structuring job을 원자적으로 연결한다.
- Provider 전송 전 runtime-derived sensitive values와 format-detected identifiers를 최소화한다.
- Structurer를 one-policy/multiple-rider bounded batch로 확장하고, verifier 결과를 candidate review tables에 저장한다.

# PDF ingestion design

- 상태: Phase 1 승인 기준
- 적용 단계: Phase 1 — Synthetic PDF Ingestion
- 구현 계획: `docs/plan/002-synthetic-pdf-ingestion.md`
- 기술 결정: `docs/adr/0006-permissive-pdf-parser-stack.md`

## Scope

Phase 1은 공개 저장소와 CI에서 처음부터 만든 합성 PDF를 안전하게 검증하고, 텍스트·표·페이지 좌표를 근거와 함께 저장하는 최소 ingestion 경계를 구현합니다. 실제 PDF, 실제 보험·의료 자료, private external root는 Phase 1 구현과 CI에서 열지 않습니다. 테스트는 합성 fixture를 checkout 밖의 임시 root에 복사한 뒤 그 복사본만 엽니다.

인증 provider는 Phase 7 범위입니다. 따라서 Phase 1 asynchronous API는 local synthetic-only 개발 기능이며 production-safe endpoint가 아닙니다. Policy Ledger, OCR 실행, 약관 연결, 보험 자격·금액 판정은 이 단계의 책임이 아닙니다.

## Inputs

- 절대경로인 `FAMILYCARE_DOCUMENT_ROOT` 환경변수
- request와 job payload에만 있는 상대 `source_key`
- 문서 종류 후보와 canonical JSON extraction settings
- 작업 ID와 멱등 키
- 아래의 고정된 파일·페이지·프로세스 자원 제한

`source_key`는 외부 파일명이나 절대경로를 대신하는 상대 식별자입니다. password는 Phase 1 API 입력·DB·job payload·로그에 존재하지 않습니다.

## Safety limits

| 제한 | 정확한 값 | 적용 위치 |
|---|---:|---|
| 입력 파일 | 25 MiB 이하 | child 실행 전 intake |
| PDF 페이지 | 500 이하 | `pypdf` structural validation |
| parent wall timeout | 120초 | parser supervisor |
| child CPU limit | 90초 | parser child `RLIMIT_CPU` |
| child address space | 1536 MiB | parser child `RLIMIT_AS` |
| output file | 64 MiB 이하 | parser child `RLIMIT_FSIZE` and result writer |
| open descriptors | 64 이하 | parser child `RLIMIT_NOFILE` |

작업 디렉터리는 mode `0700`, 그 안의 파일은 mode `0600`으로 생성합니다. 임시 평문과 페이지 산출물은 성공·실패·취소·강제 종료 경로에서 삭제를 시도하고, 삭제 실패는 성공으로 숨기지 않습니다.

## Path and content validation

1. Worker는 `FAMILYCARE_DOCUMENT_ROOT`가 존재하는 absolute directory인지 확인합니다. Phase 1 CI에서는 이 변수에 private path를 지정하지 않고, 합성 fixture를 checkout 밖의 `TemporaryDirectory`에 복사해 지정합니다.
2. 요청과 job은 relative `source_key`만 받습니다. absolute path, NUL byte, `..` component, root 밖으로 정규화되는 경로를 거부합니다.
3. root directory descriptor에서 시작해 각 상대 path component를 chained `openat`/`dir_fd`, `O_NOFOLLOW`, directory-only flags로 열고, 최종 source를 `O_RDONLY | O_CLOEXEC | O_NOFOLLOW` semantics로 한 번만 엽니다. 디렉터리 descriptor는 최종 파일을 열 때까지 유지합니다. 이 열린 file identity의 duplicate handles에 대해 `fstat` regular-file, size, PDF magic, pypdf structure, and SHA-256을 수행합니다. consumer 사이에는 offset을 reset하거나 descriptor를 duplicate하며, validate-path 후 path를 다시 여는 TOCTOU 경로를 만들지 않습니다.
4. Linux child에는 reopen 가능한 path를 전달하지 않습니다. parent가 연 read-only descriptor를 inherited 또는 duplicated descriptor로 전달하고, child는 그 descriptor를 통해서만 PDF를 읽습니다. source_key, descriptor metadata, errors와 logs는 sanitized form만 사용합니다.
5. 파일 크기를 25 MiB와 output 64 MiB 제한에 맞춰 검사합니다.
6. 원본 바이트의 SHA-256은 열린 descriptor에서 1 MiB(`1_048_576`) chunk로 streaming 계산합니다. 전체 원본을 메모리에 올리지 않습니다.

다음 인터페이스가 path validation과 opened-handle hash 경계를 고정합니다.

```python
class OpenedSource:
    fd: int
    source_key: str
    byte_size: int


def open_source(root: Path, source_key: str) -> OpenedSource: ...
def validate_pdf(source: OpenedSource) -> ValidatedPdf: ...
def stream_sha256(handle: BinaryIO, chunk_size: int = 1_048_576) -> str: ...
```

오류에는 실제 파일명, absolute path, document body, source descriptor path를 포함하지 않습니다. Windows descriptor passing과 `RLIMIT_*` 동작은 미검증입니다.

## Parser isolation

PDF parser는 API process와 분리된 dedicated child process에서 실행합니다. Supervisor는 child에 parent가 연 read-only descriptor와 canonical JSON settings만 전달합니다. child는 CPU·address-space·file-size·descriptor limits를 먼저 적용한 다음 parser를 lazy import합니다. child에는 network client, external URL resolution, embedded file 실행, PDF JavaScript 실행 경로를 제공하지 않습니다.

Supervisor와 child의 계약은 다음과 같습니다.

```python
def run_isolated_parser(
    source_fd: int,
    settings_json: str,
    *,
    wall_timeout_seconds: int = 120,
) -> ParseOutcome: ...


def parse_local_pdf(source_fd: int, settings_json: str) -> ExtractionResult: ...
```

child에는 CPU 90초, address space 1536 MiB, file size 64 MiB, open descriptors 64개의 OS resource limit을 적용하고, supervisor에는 120초 parent wall timeout을 적용합니다. OS-level egress enforcement는 production hardening 항목입니다. 승인된 runtime boundary가 마련되기 전에는 실제 private-data acceptance를 수행하지 않습니다. Windows descriptor passing과 `RLIMIT_*` 동작은 미검증입니다.

## Parser stack

Phase 1의 parser stack은 다음 고정 버전을 사용합니다.

- `pdfplumber==0.11.10`: primary text, word, coordinate, and table/cell candidate extractor
- `pypdf==6.16.2`: structural, page-count, and encryption validation
- `reportlab==5.0.1`: deterministic synthetic PDF fixture generator used by tests

PyMuPDF는 현재 선택하지 않습니다. AGPL/commercial dual license가 현재 저장소의 no-license/proprietary-distribution posture와 충돌할 수 있어 이 단계에서는 거부합니다. 이 문서는 법률 자문이 아니며, 미래에 명시적인 license decision이 승인되면 별도 ADR로 재검토합니다. 자세한 결정은 `docs/adr/0006-permissive-pdf-parser-stack.md`에 기록합니다.

## Pipeline

### 1. Intake and structural validation

경로·symlink·regular-file·magic·크기 검사를 먼저 수행합니다. 그 다음 `pypdf`로 PDF 구조, 페이지 수, encryption 상태를 확인합니다. 입력 검증 실패는 parser extraction 전에 종료합니다.

### 2. Content identity

API POST는 relative source_key만 검증하고 `documents` row를 source_key로 생성·재사용한 뒤 `analysis_jobs` row를 enqueue합니다. API는 파일을 열지 않으므로 DocumentVersion이나 content hash를 알 수 없습니다. Worker intake가 열린 source descriptor에서 1 MiB chunk SHA-256과 PDF structure를 계산하고 `document_versions` row를 `(document_id, content_sha256)`로 생성·재사용합니다. `extractions`는 `(document_version_id, extractor_config_hash) WHERE status = 'succeeded'` partial unique constraint로 성공 결과 하나를 보장합니다. DocumentVersion이 content hash를 대표하므로 `extractions`에 content_sha256를 중복 저장하지 않으며, 두 테이블을 가로지르는 불가능한 constraint도 사용하지 않습니다.

### 3. Password handling

Phase 1 asynchronous API는 unencrypted PDF만 받습니다. `pypdf`가 encryption을 보고하면 API와 job 결과는 `PASSWORD_REQUIRED`를 반환하고 password transport를 시작하지 않습니다. Parser adapter에는 queued payload 없이 one-shot runtime password를 전달하는 직접 테스트만 둡니다. 올바르지 않은 one-shot password는 `PASSWORD_INVALID`로 분류합니다. 어떤 경로에서도 password를 DB, job payload, log에 기록하지 않습니다.

### 4. Isolated workspace

작업별 random directory를 mode `0700`으로 만들고, 생성 파일을 mode `0600`으로 제한합니다. directory 이름에는 source key, 원본 파일명, 가족 식별자를 넣지 않습니다. 성공·실패·취소·child 종료 모두에서 cleanup을 실행합니다.

### 5. Native extraction

`pdfplumber`를 primary extractor로 사용합니다. `extract_words`가 제공하는 `x0`, `top`, `x1`, `bottom`은 이미 top-left PDF-point coordinates이므로 page height를 빼거나 bottom-origin으로 변환하지 않고 직접 bounds-check한 뒤 소수 셋째 자리까지 반올림합니다. 페이지마다 words를 읽어 `TextBlock` records로 만들고, table/cell candidates에는 bounding box를 보존합니다. page number는 1-based, `reading_order`는 0부터 시작합니다.

각 page의 result를 append/serialize한 뒤 page cache를 처리 직후 닫습니다. extractor는 DB에 직접 persist하지 않으며, 전체 문서를 한 번에 cache하거나 중복된 full-document output을 저장하지 않습니다. 표 후보와 cell 후보도 동일한 page coordinate contract를 사용합니다.

### 6. Versioned quality classification

품질 규칙은 `quality-v1`로 versioning합니다. 페이지는 다음 조건 중 하나라도 참이면 `OCR_REQUIRED`입니다.

- non-whitespace character count `< 20`
- alphanumeric ratio `< 0.25`
- replacement-character ratio `> 0.05`
- maximum repeated-character run `> 20`

위 조건을 모두 통과한 페이지는 `TEXT_SUFFICIENT`입니다. Phase 1은 `OCR_REQUIRED` 분류와 경고 저장까지만 하며 OCR 실행은 하지 않습니다. 품질 분류가 낮다는 이유만으로 실제 OCR 결과가 있는 것처럼 표시하지 않습니다.

### 7. Persistence and cleanup

`documents`, `document_versions`, `extractions`, `extraction_pages`, `extraction_blocks`, `extraction_tables`, `extraction_cells`, `analysis_jobs`의 최소 물리 모델과 Evidence coordinates를 repository transaction으로 저장합니다. 부분 추출은 succeeded 결과로 노출하지 않고 transaction을 취소합니다.

## Output contract

```python
class TextBlock(TypedDict):
    page_number: int  # 1-based
    text: str
    bbox: list[float]  # [x0, top, x1, bottom], PDF points, top-left origin
    reading_order: int  # starts at 0 per page


class PageQuality(TypedDict):
    rule_version: Literal["quality-v1"]
    classification: Literal["TEXT_SUFFICIENT", "OCR_REQUIRED"]
    non_whitespace_chars: int
    alphanumeric_ratio: float
    replacement_character_ratio: float
    maximum_repeated_character_run: int
```

`ExtractionTable`과 `ExtractionCell`은 page number와 bounding box를 필수로 갖습니다. Evidence는 `DocumentVersion` UUID, 1-based page, optional `[x0, top, x1, bottom]` bounding box, content hash, and review state를 가집니다. 원본 파일 자체와 password는 DB에 저장하지 않습니다.

## Job states

AnalysisJob은 다음 상태만 사용합니다.

- `queued`
- `running`
- `succeeded`
- `retryable_failed`
- `permanently_failed`
- `cancelled`

각 job은 lease owner, lease expiry, heartbeat, attempts를 갖습니다. lease가 만료되면 다른 worker가 가져갈 수 있으며, content hash와 extractor config hash가 동일한 succeeded extraction을 중복 생성하지 않게 합니다.

## Error codes

| Code | 의미 | 재시도 |
|---|---|---|
| `INVALID_REQUEST` | API request schema 또는 source key 형식 오류 | 입력 수정 후 |
| `DOCUMENT_NOT_FOUND` | root 아래 source key를 찾지 못함 | 입력 수정 후 |
| `ANALYSIS_JOB_NOT_FOUND` | status 조회의 job UUID가 없음 | 아니오 |
| `DOCUMENT_PATH_ESCAPE` | absolute path, symlink, 또는 root 밖 경로 | 아니오 |
| `UNSUPPORTED_FILE_TYPE` | PDF magic이 아님 | 아니오 |
| `DOCUMENT_TOO_LARGE` | 25 MiB 초과 | 정책 변경 후 |
| `PAGE_LIMIT_EXCEEDED` | 500 페이지 초과 | 정책 변경 후 |
| `PASSWORD_REQUIRED` | encrypted PDF이며 password transport 없음 | 아니오 |
| `PASSWORD_INVALID` | one-shot adapter password가 틀림 | 입력 수정 후 |
| `PDF_CORRUPT` | 구조 검증 또는 parser 실패 | 다른 합성 원본 필요 |
| `EXTRACTION_TIMEOUT` | parent 또는 child 시간 제한 초과 | 제한된 횟수 |
| `RESOURCE_LIMIT_EXCEEDED` | CPU, address space, descriptor, output 제한 초과 | 제한된 횟수 |
| `TEMP_CLEANUP_FAILED` | 임시 산출물 삭제 실패 | 자동 재시도 없이 보안 대응 |

## Invariants

1. Phase 1 implementation과 CI는 실제 PDF와 private external root를 열지 않습니다.
2. 모든 resolved source는 absolute `FAMILYCARE_DOCUMENT_ROOT` 아래 regular file이며 symlink traversal이 없습니다.
3. 모든 extraction block과 table/cell candidate는 DocumentVersion, 1-based page, PDF-point coordinates를 가집니다.
4. coordinates는 top-left origin과 소수 셋째 자리 반올림을 사용하고, reading order는 0부터 시작합니다.
5. `document_versions(document_id, content_sha256)`가 content identity를 유일하게 표현하고, `extractions(document_version_id, extractor_config_hash) WHERE status = 'succeeded'`가 성공 extraction을 하나만 허용합니다. DocumentVersion이 hash를 대표하므로 Extraction에는 content hash를 중복 저장하지 않습니다.
6. password는 DB, job payload, log에 들어가지 않습니다.
7. 임시 평문과 page cache는 page 처리 후 또는 job 종료 후 남지 않습니다.
8. Phase 1 API에는 인증 provider가 없으며 production-safe endpoint로 표시하지 않습니다.

## Tests

- reportlab으로 생성한 텍스트형·표형·저품질 합성 PDF
- 합성 PDF fixture를 checkout 밖 `TemporaryDirectory`로 복사한 source-root 테스트
- 잘못된 magic bytes와 잘린 xref
- encryption `PASSWORD_REQUIRED`와 one-shot wrong-password `PASSWORD_INVALID`
- absolute source key, `..`, symlink root escape, non-regular file
- 25 MiB·500 page·120초·90초·1536 MiB·64 MiB·64 descriptor limits
- 1 MiB chunk SHA-256과 동일 hash/config idempotency
- `documents`, `document_versions`, `extractions`, `extraction_pages`, `extraction_blocks`, `extraction_tables`, `extraction_cells`, `analysis_jobs` physical table mapping and unique constraints
- top-left coordinate, 3-decimal rounding, 1-based page, 0-based reading order
- table/cell bounding boxes와 page cache close after each page
- 네 가지 `quality-v1` threshold 경계와 `TEXT_SUFFICIENT`
- success·failure·cancelled cleanup, lease recovery, heartbeat, attempts
- API payload과 logs에 password·absolute path·document body가 없는지 검사

## Security considerations

- Parser child는 부모가 연 read-only source descriptor와 canonical JSON settings만 받고 network client나 URL resolver를 갖지 않습니다.
- Intake opens the final source with descriptor-based no-follow semantics after component checks and never validates then reopens a path. Linux child receives an inherited or duplicated read-only descriptor, not a reopenable path.
- 원본 root는 읽기 전용으로, work root는 작업별 mode `0700`으로 제공합니다.
- Child applies `RLIMIT_FSIZE=64 MiB` in addition to CPU, address-space, and descriptor limits; Windows descriptor passing and `RLIMIT_*` behavior remain unverified.
- OS egress enforcement는 production hardening이며, approved runtime boundary가 없으면 private-data acceptance를 수행하지 않습니다.
- 로그는 request/job ID, error code, duration, attempt 같은 allowlist만 사용합니다.
- 정적 코드 검사만 통과한 경우 malicious PDF 동적 공격 재현을 완료했다고 표현하지 않습니다.

## Deferred decisions

OCR engine 실행, production sandbox runtime, OS-level egress enforcement, private-data acceptance 표본, 인증 provider, 실제 외부 storage 연결은 해당 단계의 승인과 별도 검증이 필요합니다. Phase 1은 이 항목들을 구현하거나 실제 자료로 확인하지 않습니다.

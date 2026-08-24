# PDF ingestion design

- 상태: Phase 1 완료 기준, v0.1 확장 승인 및 문서 검토 대기
- 적용 단계: Phase 1 baseline과 Phase 8 encrypted private import
- 구현 계획: `docs/plan/002-synthetic-pdf-ingestion.md`
- 기술 결정: `docs/adr/0006-permissive-pdf-parser-stack.md`

## Scope

Phase 1은 공개 저장소와 CI에서 처음부터 만든 합성 PDF를 안전하게 검증하고, 텍스트·표·페이지 좌표를 근거와 함께 저장하는 최소 ingestion 경계를 구현했습니다. 실제 PDF, 실제 보험·의료 자료, private external root는 Phase 1 구현과 CI에서 열지 않았습니다. 테스트는 합성 fixture를 checkout 밖의 임시 root에 복사한 뒤 그 복사본만 열었습니다.

Phase 1 asynchronous API는 local synthetic-only 개발 기능이며 production-safe endpoint가 아닙니다. v0.1은 이 baseline을 변경해 실제 경로를 몰래 활성화하지 않고, 인증된 family-scoped batch import, runtime-only password, selective local OCR, managed encrypted archive를 별도 계약으로 추가합니다.

## v0.1 encrypted batch extension

`docs/design/private-data-runtime.md`가 Phase 8의 권위 있는 runtime 설계입니다. v0.1 extension은 다음 경계를 사용합니다.

1. 한 batch는 정확히 한 `FamilyMember`를 가집니다.
2. password는 인증된 request에서 batch runtime으로만 전달하고 process memory에서 동일 batch file에 재사용합니다.
3. password는 Phase 1 `AnalysisJob` payload를 확장해 저장하지 않으며 DB·log·response에 없습니다.
4. password failure file만 새 password를 요청하고 다른 성공 file은 계속 처리합니다.
5. native extraction 뒤 `OCR_REQUIRED` page만 local Korean/English OCR을 실행합니다.
6. 성공 source는 document별 data key로 암호화해 managed archive에 저장하고 key는 runtime master key로 wrap합니다.
7. 정상 import 뒤 재분석은 archive를 사용하므로 원본 password를 매번 요구하지 않습니다.
8. Google Drive source를 수정·삭제하거나 Drive API를 호출하지 않습니다.

Phase 1의 `PASSWORD_REQUIRED`는 기존 password-free synthetic endpoint의 정확한 결과로 유지합니다. v0.1 encrypted batch API는 이 endpoint에 password field를 추가하는 방식이 아니라 인증·batch 수명주기와 in-memory secret channel을 가진 별도 use case입니다.

## Local asynchronous API boundary

Task 5의 API는 합성 fixture를 사용하는 로컬 개발 경계입니다. 런타임에서 문서 router는 `FAMILYCARE_ENV=development`와 `FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true`가 모두 맞을 때만 등록합니다. `.env.example`의 기본값은 `false`이며 두 변수 중 하나라도 조건을 만족하지 않으면 문서 route를 등록하지 않고 POST와 status GET 모두 `404`를 반환합니다. `create_app(enable_synthetic_ingestion=True)`는 테스트와 canonical OpenAPI 생성처럼 명시적으로 opt-in하는 경우에만 사용하며, module-level runtime app의 기본 gate와 `/health/live`, `/health/ready` 계약은 바꾸지 않습니다.

| Method | Path | 성공 응답 | 의미 |
|---|---|---|---|
| `POST` | `/api/v1/documents/analysis` | `202 Accepted` | 요청을 검증하고 `AnalysisJob`을 enqueue한 뒤 `job_id`, `state`, 상대 `status_url`을 반환합니다. |
| `GET` | `/api/v1/analysis-jobs/{job_id}` | `200 OK` | `state`, `attempts`, sanitized `error_code`, extraction summary counts를 projection합니다. |

요청은 `schema_version: "1"`, relative `source_key`, contract enum의 `document_kind`, canonical `extractor_config`만 가집니다. Pydantic extra fields는 금지합니다. 잘못된 body, absolute 또는 parent-traversal source key, `password`, `absolute_path`, `raw_pdf`, `url` 같은 필드는 HTTP `422`와 안정적인 `error_code: "INVALID_REQUEST"` envelope로 거부합니다. validation 위치와 메시지는 sanitized form만 반환하며 원시 값, password, absolute path, PDF 본문을 echo하지 않습니다.

유효한 source key는 파일을 열거나 존재 여부를 확인하지 않고 항상 `202`로 queue에 들어갑니다. API는 `content_sha256`를 계산하거나 `DocumentVersion`·`Extraction`을 만들지 않으며, Worker가 `POST → Worker → GET` 순서에서 intake, 구조 검증, 격리 parser, persistence를 수행합니다. 따라서 missing, corrupt, encrypted PDF는 동기 POST 오류가 아니라 비동기 job 결과입니다. encrypted PDF는 `PASSWORD_REQUIRED`가 되며 queued password transport는 없습니다. 존재하지 않는 status UUID는 `404 ANALYSIS_JOB_NOT_FOUND`를 반환하고 `DOCUMENT_NOT_FOUND`로 혼동하지 않습니다. `PASSWORD_INVALID`는 queued payload가 없는 direct one-shot adapter 진단에만 해당합니다.

이 API에는 authentication·authorization이 없고 production-safe endpoint라고 주장하지 않습니다. Authentication provider는 Phase 7이며, Policy Ledger·OCR 실행·external URL·external AI·보험 판정은 이 ingestion boundary 밖입니다.

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
| canonical settings JSON | UTF-8 64 KiB 이하 | child 실행 전 supervisor |
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

`settings_json`은 intake 이후 Worker가 만드는 내부 계약입니다. 허용 필드는 `document_version_id`, `content_sha256`, `extractor_config_hash`, `quality_rule_version`, `table_strategy`뿐이며 exact canonical JSON object로 검증합니다. source path, source key, password, document body는 이 경계를 통과하지 않습니다. 이 내부 계약은 API request나 DB에 저장되는 pre-intake AnalysisJob settings와 구분됩니다.

child에는 CPU 90초, address space 1536 MiB, file size 64 MiB, open descriptors 64개의 OS resource limit을 적용하고, supervisor에는 120초 parent wall timeout을 적용합니다. Fork 후 parser를 호출하기 전에 source와 supervisor control pipe를 제외한 inherited application file·socket descriptor를 닫습니다. Child 결과는 JSON 값만 허용하고 canonical UTF-8 JSON으로 직렬화하며, parent는 64 MiB를 넘는 결과를 읽지 않습니다. 격리 경계를 넘어 임의 객체 생성을 허용하는 `pickle` 역직렬화는 사용하지 않습니다. OS-level egress enforcement는 production hardening 항목입니다. 승인된 runtime boundary가 마련되기 전에는 실제 private-data acceptance를 수행하지 않습니다. Windows descriptor passing과 `RLIMIT_*` 동작은 미검증입니다.

## Parser stack

Phase 1의 parser stack은 다음 고정 버전을 사용합니다.

- `pdfplumber==0.11.10`: primary text, word, coordinate, and table/cell candidate extractor
- `pypdf==6.16.2`: structural, page-count, and encryption validation
- `reportlab==5.0.1`: root development/test group에만 있는 deterministic synthetic PDF fixture generator; Worker runtime image에는 포함하지 않음

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

`ExtractionTable`과 `ExtractionCell`은 자신을 포함하는 `ExtractionPage`에서 1-based page number를 상속하고 bounding box를 필수로 갖습니다. `TextBlock`과 Evidence에는 1-based page number가 명시적으로 포함됩니다. Evidence는 `DocumentVersion` UUID, optional `[x0, top, x1, bottom]` bounding box, content hash, and review state도 가집니다. 원본 파일 자체와 password는 DB에 저장하지 않습니다.

## Job states

AnalysisJob은 다음 상태만 사용합니다.

- `queued`
- `running`
- `succeeded`
- `retryable_failed`
- `permanently_failed`
- `cancelled`

각 job은 lease owner, lease expiry, heartbeat, attempts를 갖습니다. Worker는 `FOR UPDATE SKIP LOCKED`로 due job 하나를 claim하고 attempt를 한 번 증가시킵니다. production 기본 lease는 180초이고 parser supervisor가 30초마다 현재 owner의 lease를 heartbeat합니다. lease가 만료되면 job은 `retryable_failed`로 회수되며 이미 max attempts에 도달한 job은 `permanently_failed`가 됩니다. retryable timeout/resource failure의 `available_at`은 `2 ** attempts`초 지수 backoff를 사용하되 최대 300초로 제한합니다.

Worker intake는 열린 descriptor에서 검증한 content identity에 대해 `document_versions`를 먼저 생성하거나 재사용합니다. parser child Evidence에 그 UUID가 필요하기 때문에 이 짧은 transaction은 extraction transaction보다 먼저 commit됩니다. child 결과는 exact shape, hash/config identity, 1-based page, 순차 reading order, 품질 규칙, 좌표 범위를 다시 검증한 뒤 `extractions`와 모든 page/block/table/cell, Evidence coordinate, job 성공 전이를 하나의 transaction에 기록합니다. 파싱이나 결과 검증이 실패하면 유효한 DocumentVersion identity는 남을 수 있지만 partial Extraction은 남지 않습니다. 같은 content hash/config의 succeeded extraction은 재사용합니다.

SIGTERM/SIGINT가 들어오거나 lease heartbeat가 실패하면 supervisor progress callback이 child 작업을 취소하고 bounded join/terminate/kill 순서로 회수합니다. 현재 job 종료 전에는 다음 job을 claim하지 않습니다.

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
9. Synthetic API route는 두 환경변수 gate가 모두 opt-in일 때만 등록되고, disabled runtime은 두 문서 path에 `404`를 반환합니다.
10. Valid POST는 source key와 request shape만 검사하여 `202`로 enqueue하며, 파일 상태 오류는 Worker가 비동기 job error로 projection합니다.
11. API validation failure는 HTTP `422 INVALID_REQUEST`이고, unknown status UUID는 `404 ANALYSIS_JOB_NOT_FOUND`입니다.

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
- success·failure·cancelled cleanup, concurrent `SKIP LOCKED` claim, lease recovery, owner-only heartbeat, max attempts, bounded retry backoff
- malformed child result의 transaction 전 거부와 duplicate succeeded extraction 재사용
- shutdown/lease-loss progress cancellation과 parser child 회수
- API payload과 logs에 password·absolute path·document body가 없는지 검사

v0.1 extension tests:

- 한 FamilyMember batch와 cross-member file 혼합 거부
- 한 번 입력한 in-memory password 재사용과 실패 file만 재입력
- password가 DB, persisted job, response, log에 없는지 검사
- encrypted archive round-trip, tamper, wrong/missing master key
- `OCR_REQUIRED` page만 local Korean/English OCR 실행
- native/OCR extraction provenance와 page Evidence 분리
- 성공·실패·취소·shutdown에서 decrypted PDF와 OCR image cleanup
- managed archive를 통한 password 없는 reanalysis

## Security considerations

- Parser child는 부모가 연 read-only source descriptor와 canonical JSON settings만 받고 network client나 URL resolver를 갖지 않습니다.
- Intake opens the final source with descriptor-based no-follow semantics after component checks and never validates then reopens a path. Linux child receives an inherited or duplicated read-only descriptor, not a reopenable path.
- 원본 root는 읽기 전용으로, work root는 작업별 mode `0700`으로 제공합니다.
- Child applies `RLIMIT_FSIZE=64 MiB` in addition to CPU, address-space, and descriptor limits; Windows descriptor passing and `RLIMIT_*` behavior remain unverified.
- OS egress enforcement는 production hardening이며, approved runtime boundary가 없으면 private-data acceptance를 수행하지 않습니다.
- 로그는 request/job ID, error code, duration, attempt 같은 allowlist만 사용합니다.
- 정적 코드 검사만 통과한 경우 malicious PDF 동적 공격 재현을 완료했다고 표현하지 않습니다.

## Deferred decisions

Phase 1은 OCR 실행, encrypted batch, authentication, private-data acceptance를 구현하거나 실제 자료로 확인하지 않았습니다. 이 항목의 v0.1 계약은 `docs/design/private-data-runtime.md`와 `docs/design/authentication.md`에서 승인되었습니다. OS-level egress enforcement, Google Drive 자동 연결, Windows descriptor behavior와 public production sandbox는 v0.1 이후로 남깁니다.

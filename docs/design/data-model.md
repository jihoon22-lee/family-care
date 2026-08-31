# Data model design

- 상태: native/encrypted ingestion, selective OCR, 업무 모델, private knowledge publication과
  advisory disposition까지 `main` 반영; migration head `0023_advisory_disposition`
- 적용 단계: Phase 1 ingestion 모델과 Phase 2~8 업무 모델
- 상위 기준: `docs/design/v0.1-product.md`

## Scope

보험 대상 가족, 계약, 문서, 가입 담보, 약관, 사건, 판정, 청구 이력을 서로 다른 수명주기와 증거 수준으로 관리하는 데이터 경계를 정의합니다. Phase 1은 문서 ingestion에 필요한 최소 물리 모델을 먼저 소유하고, Policy Ledger는 Phase 2에서 그 모델에 의존합니다. 이 문서는 물리 스키마보다 도메인 의미와 불변조건을 우선하지만 Phase 1 구현에 필요한 테이블·키·상태 경계는 명시합니다.

## Inputs

- 검수된 문서 메타데이터와 페이지 Evidence
- 관리자 두 계정의 인증 주체
- 가족, 계약 당사자, 가입 Rider의 검수값
- 약관 조항과 규칙 버전
- MedicalEvent와 ClaimHistory
- 사용자 입력 수동 `ReceiptLine`과 확인 수준

원시 추출값은 입력 후보이며 관리자 확정값과 구분합니다.

## Phase 1 native minimum physical model

Phase 1 migration은 native ingestion을 위해 다음 여덟 엔터티만 만듭니다. 원본 PDF bytes, password, absolute path, 문서 본문 전체의 비식별 복사본은 이 모델에 저장하지 않습니다. `source_key`는 `FAMILYCARE_DOCUMENT_ROOT`에 상대적인 값이며, API와 job payload에 absolute path가 들어가지 않습니다. v0.1의 selective OCR은 아래 별도 additive migration으로 native model을 확장합니다.

| Entity | Physical table | Minimum responsibility and fields |
|---|---|---|
| `Document` | `documents` | logical UUID, unique active synthetic/local `source_key`, document kind, nullable media type/byte size/page count until intake, current status, soft-delete timestamp |
| `DocumentVersion` | `document_versions` | document UUID, version number, content SHA-256, source metadata, created timestamp; same document content does not create a new version |
| `Extraction` | `extractions` | document-version UUID, extractor name/version, extractor-config hash, quality-rule version, status, succeeded timestamp |
| `ExtractionPage` | `extraction_pages` | extraction UUID, 1-based page number, page dimensions, quality metrics, `TEXT_SUFFICIENT` or `OCR_REQUIRED`, warning codes |
| `ExtractionBlock` | `extraction_blocks` | page UUID, `TextBlock` text, PDF-point top-left bounding box rounded to 3 decimals, reading order starting at 0 |
| `ExtractionTable` | `extraction_tables` | page UUID, table candidate bounding box, extraction metadata, review state |
| `ExtractionCell` | `extraction_cells` | table UUID, cell candidate bounding box, row/column coordinates, extracted text, review state |
| `AnalysisJob` | `analysis_jobs` | document UUID, relative source key, canonical settings and config hash, queued/running/succeeded/retryable_failed/permanently_failed/cancelled state, availability time, lease, heartbeat, attempts, error code |

`AnalysisJob`에는 available-at timestamp, lease owner, lease expiry, heartbeat timestamp, attempt count, max attempts, created/updated timestamps를 둡니다. job payload에는 password, absolute path, document body, private external identifier를 두지 않습니다. `documents`는 active `source_key`를 유일하게 유지합니다. `document_versions`는 `(document_id, version_number)`와 `(document_id, content_sha256)`를 각각 unique로 유지해 버전 순서와 content identity를 표현합니다. `extractions`는 `(document_version_id, extractor_config_hash)`에 대해 `status = 'succeeded'`인 행만 partial unique constraint를 적용합니다. DocumentVersion이 content hash를 대표하므로 이 두 키가 같은 document content와 extractor config에 성공 extraction 하나를 공동으로 보장하며, `extractions`에 `content_sha256`를 중복 저장하거나 두 테이블을 가로지르는 불가능한 constraint를 만들지 않습니다. `extraction_pages(extraction_id, page_number)`, `extraction_blocks(page_id, reading_order)`, `extraction_cells(table_id, row_index, column_index)`도 각각 unique로 유지합니다. 같은 hash/config의 queued 또는 failed job은 기존 succeeded result를 재사용하는 idempotency 경로를 사용합니다.

## v0.1 selective OCR physical model

Alembic `0013_selective_ocr.py` (`down_revision = 0012_encrypted_document_import`)은 native extraction을 UPDATE하지 않고 다음 세 테이블을 추가합니다.

| Entity | Physical table | Minimum responsibility and fields |
|---|---|---|
| `OcrLayer` | `ocr_layers` | native `extraction_id`, `source_layer='ocr'`, Tesseract engine/version, fixed `kor+eng` language configuration hash, `quality-v1`, succeeded status, warning codes, timestamps; one successful configuration per extraction |
| `OcrPage` | `ocr_pages` | OCR layer and same `DocumentVersion`, content SHA-256, 1-based selected page, fixed 300 DPI, rendered image dimensions, `OCR_REQUIRED` classification, completed/warning status, warning codes |
| `OcrBlock` | `ocr_blocks` | OCR page, normalized text, top-left PDF-point bbox, 0-based reading order, confidence, `source_layer='ocr'`, `review_state='candidate'` |

The migration constrains page numbers to 1..500, DPI to 300, image dimensions to bounded positive values, block confidence to 0..100, and block order to 0..9999. OCR rows contain no PDF bytes, image path, TSV path, password, or provider payload. `document_batch_items` also carries bounded `ocr_state`, `ocr_pages_processed` (0..500), and unique allowlisted warning codes for progress only; the batch projection never includes OCR text or coordinates.

Phase 1의 물리 상태는 Document `pending`/`ready`/`failed`, Extraction `running`/`succeeded`/`failed`, review `candidate`/`confirmed`/`rejected`, 그리고 아래의 여섯 AnalysisJob 상태로 고정합니다. 성공 Extraction만 `succeeded_at`을 가져야 합니다. `source_key`는 최대 512자이며 빈 값이 아니어야 하고, API/contract 경계에서 absolute·parent traversal·Windows/UNC 형태·개행을 거부합니다. AnalysisJob의 attempts는 0 이상이고 max_attempts 이하이며, error_code는 versioned contract의 허용 목록에 한정합니다.

Phase 1의 API POST는 source_key 형식만 검증하고 `documents` row를 source_key로 생성·재사용한 뒤 `analysis_jobs` row를 enqueue합니다. API는 아직 파일을 열지 않으므로 DocumentVersion이나 content hash를 만들 수 없습니다. Worker intake가 열린 source descriptor에서 hash와 PDF 구조를 확인한 뒤 `document_versions`를 생성·재사용하고, `extractions`를 생성·재사용합니다. Unknown job 조회는 `ANALYSIS_JOB_NOT_FOUND`를 반환합니다.

Worker는 parser Evidence에 필요한 UUID를 확정하기 위해 validated intake 직후 DocumentVersion을 별도 짧은 transaction에서 생성하거나 재사용합니다. 이후 child 결과의 전체 shape와 identity를 검증하고 Extraction, page/block/table/cell, Evidence coordinates, AnalysisJob 성공 전이를 하나의 transaction에 저장합니다. 따라서 parser 실패나 잘못된 child result는 유효한 content-identity DocumentVersion을 남길 수 있지만 partial Extraction을 남기지 않습니다. 동일 content/config의 기존 succeeded Extraction이 있으면 child 실행과 중복 row 생성을 건너뛰고 그 결과로 job만 성공 전이합니다.

Phase 1 API에는 인증이 없으므로 historical implementation은 local synthetic-only 개발 경계입니다. Phase 7은 `docs/design/authentication.md`의 두 로컬 관리자와 PostgreSQL session을 추가합니다. Phase 2 이후의 모든 business record는 `HouseholdSpace` scope를 소유하거나 명시적인 `household_space_id` foreign key를 가지며, 클라이언트가 보낸 household/user ID를 권위로 사용하지 않습니다.

## Phase 1 asynchronous API projection

문서 ingestion API는 위의 여덟 native 테이블에 대한 얇은 enqueue/status projection입니다. v0.1 authenticated batch status에는 OCR progress metadata가 additive bounded projection으로 포함될 수 있지만 OCR result payload는 포함하지 않습니다. 런타임 router는 `FAMILYCARE_ENV=development`와 `FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true`가 모두 설정된 경우에만 등록하며, 기본-disabled app은 두 문서 경로에 `404`를 반환합니다. OpenAPI 생성과 테스트의 `enable_synthetic_ingestion=True`는 이 runtime gate를 우회하는 운영 설정이 아니라 문서·테스트를 위한 명시적 opt-in입니다.

| Route | Projection |
|---|---|
| `POST /api/v1/documents/analysis` | Relative `source_key`, `document_kind`, canonical extractor settings를 strict하게 검증하고 `documents`를 생성·재사용한 뒤 `analysis_jobs`를 enqueue합니다. 성공은 `202`이며 `job_id`, queued `state`, relative `status_url`을 반환합니다. |
| `GET /api/v1/analysis-jobs/{job_id}` | queued/running/succeeded/retryable_failed/permanently_failed/cancelled state, attempts, sanitized error code, extraction summary counts를 반환합니다. 모르는 UUID는 `404 ANALYSIS_JOB_NOT_FOUND`입니다. |

Request model은 extra fields를 거부하므로 password, absolute path, raw PDF bytes, URL, arbitrary metadata를 저장 경계로 전달하지 않습니다. API validation 오류는 HTTP `422`와 `INVALID_REQUEST` envelope로 즉시 반환되며, validation message는 raw value와 document content를 echo하지 않습니다. 반면 source key가 형식상 유효한 POST는 파일을 열지 않으므로 missing/corrupt/encrypted 상태를 동기적으로 알 수 없습니다. Worker가 나중에 파일을 열고 encrypted input을 `PASSWORD_REQUIRED` job error로 전이합니다. 이 순서는 `POST → AnalysisJob → Worker intake/extraction → GET`이며, API는 content hash, DocumentVersion, Extraction을 직접 만들지 않습니다.

이 endpoint는 authentication·authorization이 없는 local synthetic-only 개발 기능이며 production-safe가 아닙니다. Authentication provider와 HouseholdSpace authorization은 Phase 7 및 이후 business record 범위에서 별도로 다룹니다. Policy Ledger, OCR, 외부 URL·AI, 실제 자료 acceptance는 이 projection의 책임이 아닙니다.

## Phase 1 Evidence coordinates

Evidence는 `DocumentVersion` UUID와 1-based PDF page를 필수로 가지며, 선택적 bounding box는 PDF points·top-left origin·소수 셋째 자리 반올림을 사용합니다. `ExtractionBlock`, `ExtractionTable`, `ExtractionCell`은 자신의 page 또는 table parent를 통해 이 좌표를 보존합니다. 사용자 화면의 page index와 내부 page number를 혼용하지 않습니다.

## Outputs

- 수명주기가 분리된 도메인 엔터티와 관계
- 필드별 Evidence와 검수 상태
- soft delete와 감사 가능한 상태 전이
- 판정 엔진이 읽을 시점 기준 계약·Rider 상태

## Identity boundary

### AppUser

앱에 로그인하는 로컬 계정입니다. 관리자 두 명까지만 생성하며 같은 `HouseholdSpace`에 동일 권한으로 연결합니다.

핵심 필드:

- 내부 UUID
- 정규화한 local username
- Argon2id password hash
- 표시 이름
- 활성 상태
- 생성·수정·비활성 시각

이메일과 외부 인증 provider subject는 v0.1 핵심 모델에 저장하지 않습니다. raw password는 어떤 모델에도 저장하지 않습니다.

### AppSession

opaque browser session의 서버 측 레코드입니다. session token hash, AppUser, 생성·마지막 활동·만료·폐기 시각, 최소 device label을 가집니다. 원본 session token은 DB에 저장하지 않습니다.

### HouseholdSpace

두 관리자가 공유하는 하나의 논리 공간입니다. 다중 가정 기능은 구현하지 않지만, 가족 데이터가 전역 singleton에 묶이지 않도록 명시적 소유 경계를 둡니다.

### FamilyMember

보험 계약과 사건의 대상입니다. AppUser와 일치할 수도 있지만 외래키로 동일 엔터티를 재사용하지 않습니다.

핵심 필드:

- 내부 UUID
- HouseholdSpace UUID
- 표시 이름
- 가족 내 구분용 합성·내부 별칭
- soft delete 시각

실제 주민번호와 상세 주소는 핵심 모델에 저장하지 않습니다.

## Document boundary

### Document

저장소 밖 import source와 application-encrypted managed archive의 논리 식별자입니다.

- 문서 종류: policy, terms, product_explanation, application, amendment, claim, supporting
- 원본 제공자와 비공개 외부 참조
- MIME, 크기, 페이지 수
- 문서 작성·수집·수정 시각
- 처리와 검수 상태
- soft delete 시각

v0.1의 archive metadata는 encrypted object key, encryption scheme/version, nonce, wrapped data key, ciphertext size와 integrity tag를 가집니다. archive master key와 PDF password는 저장하지 않습니다.

외부 참조는 API 응답과 일반 로그에 노출하지 않습니다.

`product_explanation`은 청약 전·계약 안내용 상품설명서를 뜻하며 증권이나 약관을 대체하지 않습니다. 이 문서만으로 PolicyContract나 Rider를 만들지 않습니다. 가족별 보유 문서와 계약 연결은 `docs/design/insurance-document-inventory.md`의 별도 읽기 모델을 따릅니다.

Document kind는 intake 시점의 source-level 분류입니다. 한 PDF 안에 서로 다른 보험의 증권이나 증권·약관·상품설명서·청약서가 함께 있을 수 있으므로 파일명이나 이 단일 값만으로 최종 역할을 확정하지 않습니다.

### DocumentVersion

같은 논리 문서가 교체되거나 재발급됐을 때 원본 버전을 보존합니다. `document_versions`는 `(document_id, content_sha256)`를 unique로 관리하며, 이 행이 content hash의 단일 대표입니다. 해시가 같으면 새 버전을 만들지 않습니다.

### Extraction

파서별 결과와 품질을 저장합니다.

- 추출기 이름·버전·설정 해시
- 페이지별 텍스트 블록과 좌표
- 표 셀과 읽기 순서
- 품질 지표와 경고 코드
- 생성 상태와 관리자 검수 상태

관리자 수정값은 원시 추출값을 덮어쓰지 않고 별도 확정 레코드로 보존합니다.

### Selective OCR layers

`OcrLayer`, `OcrPage`, `OcrBlock`은 native `Extraction`과 독립된 후보 provenance입니다. Worker는 native `quality-v1` 결과에서 `OCR_REQUIRED`인 page만 선택하고, fixed local `kor+eng` Tesseract result를 300 DPI render metadata와 함께 저장합니다. `TEXT_SUFFICIENT` page는 OCR row를 만들지 않으며, OCR text가 native `ExtractionBlock`을 덮어쓰지 않습니다. 각 OCR page Evidence는 같은 `DocumentVersion` UUID, 1-based page, content hash, and `source_layer='ocr'`를 가집니다.

The source descriptor and temporary PNG are runtime-only. PDFium reads bounded bytes from the already-open read-only descriptor; Tesseract is invoked directly as `/usr/bin/tesseract` with no shell and TSV on stdout; no `pytesseract` dependency or TSV artifact is persisted. Each PNG is removed immediately after its page is recognized, and the outer Worker workspace is removed on every terminal path.

### Private batch page Evidence

Successful private batch persistence creates one bbox-free `Evidence` row for every validated physical page in the same transaction as the `DocumentVersion`, successful `Extraction`, native/OCR provenance, managed archive metadata, and terminal batch state. The locked batch supplies `household_space_id`; the validated intake supplies content hash and expected page count; the newly created successful Extraction supplies `extraction_id`. A page-count mismatch, non-sequential page number, invalid identity, or missing household scope aborts the transaction. Initial state is always `NEEDS_REVIEW`.

같은 성공 transaction에서 `document_batch_items.processed_document_version_id`를 고정한다. 이후 page-range component는 이 값을 사용하므로 동일 `Document`에 새 version이 생겨도 과거 batch가 처리한 원본을 임의 최신 version으로 바꾸지 않는다. password/OCR/failed item은 version을 꾸며 내지 않고 nullable 상태로 남긴다.

For later candidate structuring, the Worker can resolve only those scoped page rows whose Evidence hash matches the same DocumentVersion and whose Extraction is successful. It reads at most 500 ordered rows and emits at most 64 non-empty in-memory slices of 240 characters each. An `OCR_REQUIRED` page prefers its successful OCR layer and falls back to native text only when OCR text is empty; `TEXT_SUFFICIENT` pages use native extraction. These slices are not stored in a new table and are not returned by batch APIs or logs.

### Private policy structuring jobs

`policy_structuring_jobs`는 성공한 private `policy` batch item 하나와 DocumentVersion, successful Extraction, 선택된 FamilyMember, HouseholdSpace를 연결하는 별도 leased queue다. import/archive 성공은 provider 결과와 분리되며 timeout·rate limit·일시 장애만 최대 5회 bounded backoff로 재시도한다. 인증·응답 검증·Evidence 부재 오류는 영구 실패가 된다. job에는 source path, document text, provider payload를 저장하지 않는다.

각 job은 하나의 `policy_aggregate_id`를 미리 예약한다. Worker가 만든 contract와 rider `AnalysisCandidateVersion`은 `structuring_job_id`와 provider candidate ID를 저장하고 모두 이 aggregate ID를 공유한다. candidate fields와 candidate Evidence, job 성공 전이는 한 transaction으로 저장된다. 초기 private page Evidence가 `NEEDS_REVIEW`이면 AI가 승인한 후보도 review 후보로 낮추며 policy, party, rider projection은 사용자 확인 전 생성하지 않는다.

## Policy boundary

### PolicyContract

보험 계약의 논리 단위입니다.

- FamilyCare 내부 계약 UUID
- 보험사와 상품의 표시용 정규화 값
- 계약일, 보장 시작·종료일
- 계약 상태와 상태 근거
- 증권 DocumentVersion
- 마지막 검수 시각과 검수자

증권번호는 기능상 필요성이 확인될 때 암호화·마스킹 정책과 함께 추가합니다. 공개 fixture에는 넣지 않습니다.

### PolicyParty

PolicyContract와 FamilyMember 사이의 역할 연결입니다.

- policyholder
- primary_insured
- additional_insured
- beneficiary

역할은 기간을 가질 수 있으며 한 가족 구성원이 여러 역할을 가질 수 있습니다.

### Rider

증권에서 실제 가입이 확인된 주계약 또는 특약입니다.

- 원문 표시명과 정규화 키
- 가입금액과 통화
- 납입기간과 보장기간
- 갱신형 여부
- 현재 상태와 상태 확인 시각
- 증권 페이지 Evidence
- 추출·검수 상태

약관에만 나타난 특약은 Rider를 만들 수 없습니다.

### PolicyStatusSnapshot

사고일 기준 계약과 Rider 유효성을 평가하기 위한 시점별 상태입니다. 최신 상태 근거가 없으면 현재 활성으로 추정하지 않습니다.

### Insurance document inventory associations

등록 보험과 보완 문서를 직접 연결하는 단일 link 대신 아래의 component와 document set 모델을 사용합니다.

### InsuranceDocumentComponent

하나의 immutable DocumentVersion 안에서 검수된 역할과 1-based inclusive page range를 보존합니다. 역할은 `policy`, `terms`, `product_explanation`, `application`, `supporting`이며 source-level Document kind와 다를 수 있습니다. 제안·사용자 확인·상충·거부 상태, Evidence, optimistic version과 soft delete를 보존합니다. 원시 extraction이나 batch 분류를 덮어쓰지 않습니다.

### InsuranceDocumentSet

같은 HouseholdSpace와 FamilyMember에 속하며 같은 보험 상품·계약으로 검토되는 component의 묶음입니다. 증권 근거가 없는 set도 만들 수 있고, 등록 보험 set만 nullable `policy_contract_id`를 가집니다. document set은 가입 authority가 아니며 PolicyContract를 생성하지 않습니다.

### InsuranceDocumentSetItem

document set과 component의 다대다 연결입니다. import batch item과 immutable DocumentVersion을 함께 참조하고, 제안·사용자 확인·상충·거부 상태와 optimistic version을 보존합니다. 사용자 확인 상태인 active terms item만 등록 set의 문서 완전성 계산에 포함합니다. 약관·상품설명서·청약서가 연결되지 않았다는 사실은 계약 불일치 판정이 아니라 보완할 문서 상태입니다.

## Terms and rules boundary

### TermsEdition

상품·특약의 특정 약관 판본입니다. 계약일과 판매 시기, DocumentVersion을 연결합니다.

### Clause

장·절·조·항·별표의 구조와 페이지 Evidence를 저장합니다. 같은 원문 조항이 여러 Rider에 연결될 수 있습니다.

### RiderClauseLink

실제 가입 Rider와 해당 약관 조항의 연결입니다. 자동 후보 점수, 관리자 확정 상태, 연결 근거를 분리합니다.

### CoverageRule

지급사유, 정의, 보장개시, 감액, 면책, 횟수 제한, 계산식을 명시적 구조로 표현합니다. 규칙 버전과 Clause Evidence가 필수입니다.

### AnalysisCandidateVersion

PolicyContract, Rider, Clause, RiderClauseLink, CoverageRule 후보의 생성·검증·사용자 수정 이력을 공통으로 표현합니다. generator/verifier/schema version, `AI_VERIFIED`·`NEEDS_REVIEW`·`USER_CONFIRMED`, source Evidence, parent version, created/published 시각을 가집니다. raw 추출과 사용자 확정값을 덮어쓰지 않습니다.

## Event and decision boundary

### MedicalEvent

한 가족 구성원의 사건 입력입니다.

- 사전 탐색 또는 사후 상세 모드
- 사건일과 방문일
- 질병·상해 분류
- 진단명과 분류코드
- 수술, 입원, 통원, 응급실 정보
- 원인과 비용 자료 가용성
- 입력 출처와 확인 수준

필드 부재는 null로 보존하고 임의 값으로 보완하지 않습니다. 의료 문서 file과 page image는 MedicalEvent에 저장하지 않습니다.

### ReceiptLine

MedicalEvent의 수동 비용 항목입니다. PR7의 `0008_benefit_calculations`가 다음 normalized fields를 `receipt_lines`에 저장합니다.

- `id`, server-derived `household_space_id`, `medical_event_id`
- `category`: `outpatient`/`inpatient`/`pharmacy`
- `coverage_category`: `covered`/`possible_excluded`/`excluded`/`unknown`
- non-negative `amount NUMERIC(18,2)`와 uppercase ISO `currency`
- `confirmation_level`: `user`/`ai_structured`/`unconfirmed`
- 선택적 `note_code`(최대 64자의 uppercase reason code만 허용)
- optimistic `version`, `created_at`, `updated_at`, `deleted_at`

ReceiptLine create/update는 수동 구조화 metadata만 받습니다. scoped active-list projection은 재접속 시 편집을 이어갈 ID·version과 구조화 필드만 반환합니다. 원본 영수증 image/PDF, OCR output, diagnosis, external file/path, 자유 형식 note는 저장하지 않습니다. update/delete는 server scope와 `expected_version`을 함께 확인하고, delete는 soft delete로 목록·계산 기본 조회에서 제외합니다.

### RuleEvaluation

하나의 CoverageRule을 평가한 결과입니다.

- `MATCH`, `NO_MATCH`, `UNKNOWN`
- 사용한 입력 사실
- 부족하거나 충돌한 정보
- 적용 규칙 버전
- Evidence 집합
- 평가기 버전과 시각

### ClaimCandidate

Rider별 평가 집계입니다. 지급 결정이나 ClaimCase가 아닙니다. 정액형 추정, 실손형 계산 보류, 추가 질문, 제외 근거를 분리합니다.

### BenefitCalculation

`benefit_calculations`는 한 ClaimCandidate와 하나의 executable rule version에 대한 immutable 계산 header입니다. `0008_benefit_calculations`는 `0007_coverage_decision_engine` 뒤에 추가되며 다음 값을 보존합니다.

- `calculation_kind`: `fixed` 또는 `indemnity`
- `status`: `computed`, `partial`, `unknown`
- 결과 통화와 `confirmed_amount`, `additional_amount`, `excluded_amount`
- indemnity의 `deductible_amount`, `applied_rate`, `applied_limit`
- 적용 `rounding_rule`, 첫 hold reason code, 최대 16개의 bounded `excluded_reason_codes`, `rule_version_id`, `engine_version`, optimistic `version`, `created_at`
- `household_space_id`와 `claim_candidate_id` foreign key

각 header의 `benefit_calculation_steps`는 `step_number`, `operation`, input/output NUMERIC(18,6)와 통화, rounding rule, bounded `reason_code`를 저장하며 `(benefit_calculation_id, step_number)`가 unique입니다. 계산기를 다시 실행할 때 기존 header/step을 갱신하지 않고 새 version row를 생성합니다. 동일 rule/input cutoff trace는 재사용할 수 있지만, 계산에 영향을 주는 Rider 또는 indemnity ReceiptLine 변경이나 새 rule version은 새 trace를 만듭니다.

`BenefitCalculation`은 실행 가능한 `AI_VERIFIED`/`USER_CONFIRMED` rule과 승인된 Policy/Clause Evidence를 다시 확인한 뒤에만 계산됩니다. 순수 계산기는 missing/stale Evidence, missing fact, invalid formula, no receipt, currency mismatch, overflow를 금액 0이 아닌 `UNKNOWN`과 hold reason으로 반환합니다. repository의 rule selector가 유효한 rule/evidence chain을 하나도 찾지 못하면 해당 candidate를 계산 projection에서 제외하며, 금액을 추정하지 않습니다. partial indemnity는 confirmed/additional/excluded를 분리하고, 복수 indemnity는 독립 금액을 합산하지 않으며 allocation을 `UNKNOWN`으로 남깁니다.

HTTP projection은 `BenefitCalculationsResponse` envelope로 `schema_version`, calculation metadata, Decimal-string Money objects, bounded steps, hold/exclusion reason codes, `evidence_ids`를 반환합니다. API는 household scope를 request field로 받지 않고 모든 응답을 `no-store`로 보냅니다.

## Claim boundary

### ClaimCase

실제 청구 단위입니다. `claim_cases`는 `household_space_id`, `medical_event_id`, `family_member_id`, `policy_contract_id`, non-null `rider_id`, 서버가 선택 Rider에서 파생한 `insurer_key`, 상태, receipt/submission metadata, claimed/paid 금액과 통화, outcome reason, optimistic `version`, 생성·수정 시각, soft-delete 시각을 보존합니다. 클라이언트는 정책·보험사·가구 범위를 보내지 않고 결과 카드에서 `rider_id`만 보내며, 서버가 같은 HouseholdScope 안에서 계약과 보험사를 확인합니다. `(household_space_id, medical_event_id, rider_id)` active unique 경계로 중복 지급 이력 생성을 막습니다.

상태는 preparing, submitted, supplementation_requested, paid, partially_paid, denied, closed를 사용합니다. submitted는 FamilyCare가 전송했다는 뜻이 아니라 사용자가 보험사 channel에서 접수한 사실을 수동 기록한 상태입니다. FamilyCare에는 보험사 제출 API·email/fax 연동이 없고, ClaimCase에 파일이나 원문을 저장하지 않습니다.

### ClaimCase snapshot

`claim_case_snapshots`는 ClaimCase 생성 시의 Candidate, Rule, Policy, Evidence와 선택한 후보에 연결된 모든 BenefitCalculation을 정규화한 allowlist JSON과 `snapshot_version`, SHA-256을 저장합니다. 계산은 exact decision run에 묶이고, stale run이나 생성 중 policy lineage 변경은 거부됩니다. 각 component는 별도 JSON object로 보존되고 `(claim_case_id, snapshot_version)`이 unique입니다. snapshot에는 ID·version·상태·reason code·content hash 같은 bounded lineage만 들어가며 diagnosis/situation, receipt note/number, 문서 본문·경로·파일·OCR·provider payload와 외부 식별자는 들어가지 않습니다. 이후 재분석이나 원본 행 변경은 기존 snapshot을 수정하지 않으며 DB trigger도 UPDATE/DELETE를 거부합니다.

HTTP 응답은 저장된 전체 JSON을 그대로 노출하지 않고 candidate/rules/policy/evidence/calculation의 ID·version·상태 중심 bounded projection과 snapshot hash를 반환합니다.

### ClaimChecklistItem and ClaimStatusEvent

`claim_checklist_items`는 `document_kind`, `requirement_code`, `required`, `conditional`, `prepared`, bounded `note_code`, source rule version/Evidence ID, version과 시각만 저장합니다. 파일, path, binary, image, OCR, document text, 외부 파일 ID는 없습니다. `claim_status_events`는 허용된 from/to status, occurred-at, bounded reason code와 metadata object를 append-only로 기록합니다.

### ClaimHistory

`claim_history`는 non-null `rider_id`, 지급일, 지급 결과, 횟수 제한에 필요한 최소 이력을 보존합니다. paid와 partially_paid는 같은 transaction에서 `counted_occurrence=true`인 사실로 기록하며, denied는 `counted_occurrence=false`인 감사 이력으로 보존합니다. 판정은 같은 Rider 이력만 횟수에 포함합니다. denied는 미래 판정의 `NO_MATCH`로 변환하지 않으며 누락·충돌 이력은 `UNKNOWN`입니다. 필요서류는 checklist metadata만 가지며 진단서·영수증·처방전 file이나 외부 file 참조를 관리하지 않습니다.

## Evidence

Evidence는 다음을 가집니다.

- DocumentVersion UUID
- PDF 기준 1부터 시작하는 페이지 번호
- 조항·별표 식별자
- 선택적 페이지 좌표
- 콘텐츠 해시
- 검수 상태

사용자 화면 페이지와 내부 0-based 인덱스를 혼용하지 않습니다.

## Invariants

1. Rider는 증권 Evidence 없이 verified 상태가 될 수 없습니다.
2. CoverageRule은 Clause Evidence와 규칙 버전 없이 활성화할 수 없습니다.
3. RuleEvaluation은 세 판정값 중 하나만 사용합니다.
4. `NO_MATCH`에는 결정적 불일치 사유가 필요합니다.
5. 갱신형 Rider는 최신 상태 근거가 없으면 current-active가 아닙니다.
6. AppUser 삭제가 FamilyMember·PolicyContract를 연쇄 삭제하지 않습니다.
7. soft-deleted 엔터티는 기본 조회에서 제외되고 명시적 휴지통 조회에서만 나타납니다.
8. 원시 추출과 관리자 확정 데이터는 서로 덮어쓰지 않습니다.
9. `NEEDS_REVIEW` candidate는 executable current version이 될 수 없습니다.
10. ReceiptLine과 ClaimCase에는 의료 document binary가 없습니다.
11. AppSession 원본 token, PDF password, archive master key는 저장되지 않습니다.
12. ReceiptLine 금액은 Decimal/통화 검증을 통과해야 하며 음수·overflow·통화 불일치는 저장·계산되지 않습니다.
13. BenefitCalculation과 step은 immutable trace이며 정액형과 실손형 금액 경로를 한 계산에서 혼합하지 않습니다.
14. 복수 indemnity의 독립 예상액을 합산하지 않고 최종 비례분담을 `UNKNOWN`으로 둡니다.
15. ClaimCase 생성은 `rider_id`를 서버가 검증한 PolicyContract·insurer에만 연결하며 클라이언트 scope를 권위로 사용하지 않습니다.
16. ClaimCase snapshot은 Candidate·Rule·Policy·Evidence·계산 전체의 immutable lineage이며 ClaimCase가 live result pointer만 보유하지 않습니다.
17. paid/partially_paid만 `counted_occurrence=true` ClaimHistory가 되고 denied는 audit-only입니다.
18. Claim 상태·checklist·삭제·복원 변경은 expected version과 허용된 transition을 거치며 soft-deleted 행은 기본 조회에서 숨깁니다.
19. ClaimCase snapshot과 status event는 application API뿐 아니라 database trigger에서도 update/delete가 거부됩니다.
20. stale decision result와 decision run 이후 변경된 policy lineage는 ClaimCase를 만들 수 없습니다.
21. `OCR_REQUIRED`만 OCR되고 `TEXT_SUFFICIENT`는 renderer/engine을 호출하지 않습니다.
22. native extraction과 OCR layer/page/block은 별도 provenance이며 OCR은 native row를 덮어쓰지 않습니다.
23. OCR progress는 bounded state/page count/warning codes만 노출하고 OCR text, image path, coordinates, TSV, stderr를 저장·응답하지 않습니다.

## Failure behavior

- 필수 근거가 누락되면 확정 전이 요청을 거부하고 안정적인 오류 코드를 반환합니다.
- Worker intake의 중복 content hash는 기존 DocumentVersion을 재사용하고, 동일 extractor config의 succeeded Extraction을 재사용합니다. API POST 자체는 source key가 유효하면 AnalysisJob을 enqueue합니다.
- 충돌하는 계약 상태는 최신 값을 임의 선택하지 않고 conflict와 `UNKNOWN`을 만듭니다.
- 삭제·복원 충돌은 상태 전이 버전으로 감지합니다.
- 계산에 필요한 rule/Evidence/fact가 없거나 stale이면 `UNKNOWN` hold를 반환하고 unsupported amount를 추정하지 않습니다.
- ReceiptLine stale update/delete는 `VERSION_CONFLICT`를 반환하며 private input 값을 error message에 echo하지 않습니다.

## Security considerations

- 내부 UUID와 외부 문서 참조를 분리합니다.
- API 응답은 증권번호와 외부 파일 경로를 기본 제외합니다.
- 민감 필드 추가는 데이터 분류, 암호화, 로그, 보존, 삭제 설계를 함께 변경해야 합니다.
- 감사 레코드는 실제 원문 대신 변경 필드와 행위자·시각을 저장합니다.

## Tests

- Phase 1 native minimum model migration creates exactly the eight ingestion entities and no Policy Ledger tables
- `0013_selective_ocr` adds only `ocr_layers`, `ocr_pages`, `ocr_blocks` plus bounded batch progress columns and leaves native rows unchanged
- `document_versions(document_id, content_sha256)` unique와 `extractions(document_version_id, extractor_config_hash) WHERE status = 'succeeded'` partial unique
- AnalysisJob state, lease, heartbeat, attempts, and cancellation transitions
- relative source key and absence of password or absolute path in persisted payloads
- AppUser와 FamilyMember 독립 수명주기
- 약관 전용 특약의 Rider 생성 거부
- 갱신 상태 미확인의 `UNKNOWN`
- Evidence 없는 검수 확정 거부
- AI candidate version과 publish 상태 전이
- AppSession hash, 7일 inactivity, 30일 absolute expiry
- ReceiptLine partial indemnity calculation input
- `0008_benefit_calculations`의 exact receipt/calculation/step tables, Decimal precision, FK/check/index/unique constraints
- fixed calculation trace와 partial indemnity confirmed/additional/excluded split
- multiple-indemnity allocation `UNKNOWN` 및 독립 금액 비합산
- household-scoped receipt CRUD, optimistic version, soft delete, Decimal-string response
- BenefitCalculation schema의 strict objects, bounded steps/holds, Evidence lineage, private-field exclusion
- ClaimCase checklist-only와 medical document field 부재
- ClaimCase의 rider-only create 요청과 서버 파생 policy/insurer scope
- Candidate/Rule/Policy/Evidence/all-calculation snapshot lineage와 immutable hash
- status transition, paid/partial counted history, denied audit-only history
- claim soft delete/trash/restore와 no-store/no-file/no-submission privacy boundary
- encrypted archive metadata와 key/password 비저장
- OCR_REQUIRED-only selection, fixed 300 DPI, native/OCR provenance separation, and OCR contract Evidence
- descriptor-derived PDFium, direct no-shell `/usr/bin/tesseract` stdout TSV, no pytesseract/artifact dependency, and per-page/outer-workspace cleanup
- bounded batch OCR progress with warning allowlist and Worker image `eng`/`kor` synthetic availability
- soft delete, 휴지통, 복원, 중복 복원
- 문서 해시 중복과 버전 교체
- 1-based 페이지 일관성

## Deferred decisions

Phase 1의 여덟 native ingestion table과 성공 extraction unique rule은 완료되었습니다. v0.1 `0013_selective_ocr` OCR layer와 bounded batch progress는 합성 branch tests 기준으로 구현되었습니다. v0.1 archive key metadata는 `docs/design/private-data-runtime.md`, session은 `docs/design/authentication.md`를 따릅니다. 실제 private PDF·Compose, provider, Windows·mobile·Tailscale acceptance와 private OCR 검증은 아직 이 모델의 범위 밖이며 private runtime PR 이후 별도로 수행합니다. 보존 기간, 실제 증권번호 저장 필요성, 운영 backup policy는 v0.1 이후 별도 결정으로 남깁니다.

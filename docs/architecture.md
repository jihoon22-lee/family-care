# FamilyCare architecture

이 문서는 FamilyCare의 장기 시스템 구조와 변경 경계를 설명합니다. Phase 0 Foundation은 PR #1에서 완료되었고, 현재 Phase 1은 합성 PDF ingestion 계획 단계입니다. 현재 구현 범위와 세부 버전은 `docs/design/project-foundation.md`, 단계별 순서는 `docs/plan/000-project-roadmap.md`가 기준입니다.

## Architectural goals

- 실제 가입 담보와 약관 조건을 분리해 정확히 연결합니다.
- 모든 판정 결과를 입력 사실, 명시적 규칙, 원문 페이지까지 추적합니다.
- 정보 부족을 실패로 숨기지 않고 `UNKNOWN`으로 표현합니다.
- 실제 문서와 개인정보를 공개 소스·CI 경계 밖에 둡니다.
- 소규모 가족용 서비스에 맞는 단순한 운영 구조로 시작합니다.
- 하위 시스템을 독립적으로 테스트하고 교체할 수 있게 계약을 명확히 합니다.

## System context

```text
관리자 두 명
    |
    v
FamilyCare PWA
    |
    v
FamilyCare API ----------------------+
    |                                |
    v                                v
PostgreSQL <---- Analyzer Worker   원본 문서 저장소
    |                                저장소 밖 / 향후 Drive 읽기 전용
    v
구조화 계약·담보·조항·판정·청구 이력
```

Foundation에서는 원본 문서 저장소, 인증 제공자, 외부 AI가 연결되지 않았습니다. Phase 1도 실제 PDF나 private external root를 열지 않고, checkout 밖 임시 root의 합성 fixture만 사용합니다. 인증 provider는 Phase 7 범위이므로 Phase 1 endpoint는 local synthetic-only 개발 경계이며 production-safe endpoint가 아닙니다.

## Trust boundaries

### Public source boundary

Git 저장소, GitHub Actions, 공개 컨테이너 빌드 컨텍스트에는 코드, 문서, 합성 fixture만 들어갑니다. 실제 PDF와 파생 데이터, 데이터베이스, 비밀값은 금지됩니다.

### Browser boundary

브라우저는 최소 화면 데이터만 받습니다. 서비스 워커는 앱 셸만 캐시하며 API 응답, PDF, 의료정보, 판정 결과를 오프라인 캐시에 저장하지 않습니다. 장기 인증 토큰은 브라우저 저장소에 두지 않습니다.

### API boundary

API는 입력 검증, 인증·인가, 유스케이스 실행, 외부 계약을 소유합니다. 로그에는 요청 본문이나 도메인 식별자를 남기지 않고 요청 ID와 오류 코드만 사용합니다.

### Worker boundary

Worker는 문서 분석 작업을 격리된 임시 디렉터리에서 수행합니다. 작업은 멱등하며 lease 만료 후 재처리할 수 있습니다. 중간 평문과 페이지 이미지는 종료 경로에서 삭제합니다.

Phase 1 parser는 전용 child process에서 실행하며 parent wall 120초, child CPU 90초, address space 1536 MiB, output file 64 MiB, open descriptor 64개의 제한을 적용합니다. intake는 root directory descriptor부터 각 상대 component를 chained no-follow open으로 순회하고 final source를 한 번만 열어, 그 file identity의 duplicate handles로 magic·hash·구조를 검증합니다. Linux child에는 reopen 가능한 path가 아니라 inherited 또는 duplicated read-only descriptor와 canonical JSON settings만 전달하고, child는 limits 적용 뒤에 parser를 lazy import합니다. network client나 external URL resolution을 제공하지 않습니다. OS egress enforcement는 production hardening 항목이며 approved runtime boundary 전까지 private-data acceptance를 수행하지 않습니다. Windows descriptor passing과 RLIMIT 동작은 미검증입니다.

### External-provider boundary

Drive와 AI는 후속 선택 연동입니다. Drive는 지정 폴더 읽기 전용, AI는 필요한 최소 조항과 비식별 구조화 입력만 허용합니다. 제공자 장애가 핵심 규칙 판정을 중단시키지 않게 합니다.

## Runtime components

### Web PWA

책임:

- 사건 입력과 선택형 추가 질문
- 후보·근거·미확인 조건 표시
- 문서 페이지 탐색 요청
- 청구 준비 상태 UI

비책임:

- 보험 자격 규칙과 금액 계산
- PDF 원본 영구 저장
- 계약 유효성 추정

### FastAPI modular monolith

모듈 경계:

- `identity`: 앱 사용자와 공동 관리자 공간
- `documents`: 문서 메타데이터와 추출 상태
- `policies`: 계약, 당사자, 실제 가입 Rider
- `clauses`: 약관 조항, 별표, 버전, 검색
- `decisions`: 사건 구조, 규칙 평가, 근거 추적
- `claims`: 준비·접수·보완·지급 이력

모듈은 다른 모듈의 내부 테이블을 직접 조작하지 않고 공개 유스케이스나 명시적 읽기 모델을 사용합니다.

### Analyzer Worker

책임:

- 파일 검증과 콘텐츠 해시
- 텍스트·표·좌표 추출
- 품질 측정과 OCR 필요성 분류
- 문서 구조 후보 생성
- 임시 파일 수명주기와 작업 상태 전이

Phase 1에서 Worker가 소유하는 최소 영속 모델과 physical table mapping은 `Document` → `documents`, `DocumentVersion` → `document_versions`, `Extraction` → `extractions`, `ExtractionPage` → `extraction_pages`, `ExtractionBlock` → `extraction_blocks`, `ExtractionTable` → `extraction_tables`, `ExtractionCell` → `extraction_cells`, `AnalysisJob` → `analysis_jobs`입니다. Policy Ledger의 `PolicyContract`, `PolicyParty`, `Rider`와 보험 판정은 후속 단계에 남깁니다.

Worker는 가입 확정이나 보험금 판정을 수행하지 않습니다. 추출 결과는 검수 전 상태로 저장됩니다.

### PostgreSQL

초기에는 다음 역할을 함께 담당합니다.

- 정규화된 업무 데이터
- 전문검색 인덱스
- 비동기 작업 큐와 lease
- 감사와 상태 전이

Phase 1은 이 계층에 위 문서 ingestion tables와 AnalysisJob lease를 추가합니다. `document_versions(document_id, content_sha256)`가 content identity를 표현하고, `extractions(document_version_id, extractor_config_hash) WHERE status = 'succeeded'`가 성공 extraction uniqueness를 보장합니다. 이후 Policy Ledger의 business records는 `HouseholdSpace` scope를 가져야 합니다.

Redis, 별도 검색엔진, 벡터 DB는 측정된 병목이나 검색 개선 근거가 있을 때만 추가합니다. 벡터 검색이 필요하면 먼저 PostgreSQL 확장으로 평가합니다.

### Contracts

FastAPI OpenAPI가 동기 HTTP 계약의 기준입니다. Worker 작업 envelope와 생성물은 버전이 있는 JSON Schema를 사용합니다. TypeScript와 Python 소비자는 이 계약에서 생성하거나 검증하며 같은 구조를 수동으로 복제하지 않습니다.

## Core data flow

### Document ingestion

```text
상대 `source_key`와 승인된 외부 root
  -> 파일·크기·암호·해시 검사
  -> 작업별 임시 공간
  -> 텍스트/표 추출
  -> 품질 평가
  -> versioned quality classification
  -> 페이지 근거가 있는 추출 결과
  -> 관리자 검수
  -> 임시 산출물 삭제
```

### Coverage discovery

```text
MedicalEvent
  -> 대상 FamilyMember
  -> 사고일 기준 실제 가입·유효 Rider
  -> 연결된 Clause와 CoverageRule
  -> 각 조건의 3값 평가
  -> 정액형/실손형 분리
  -> 근거와 부족 정보가 있는 ClaimCandidate
```

### Claim tracking

판정 후보와 실제 청구 결과는 다른 엔터티입니다. 후보는 가능성과 근거를 나타내고, ClaimCase는 준비·접수·심사·지급의 실제 이력을 기록합니다. 과거 지급 이력은 최초 1회와 횟수 제한 판정에 다시 사용됩니다.

## Data ownership and lifecycle

- `AppUser`는 로그인 주체입니다.
- `FamilyMember`는 보험 대상 가족입니다.
- `PolicyParty`는 특정 계약에서 계약자·피보험자·수익자 역할을 연결합니다.
- `Document`는 원본 자체가 아니라 relative source key와 logical 상태를 가집니다.
- `DocumentVersion`은 content hash와 version을 대표하며, `document_versions(document_id, content_sha256)`를 unique로 관리합니다.
- `Extraction`은 파서 출력과 검수 상태를 분리하고, `extractions(document_version_id, extractor_config_hash) WHERE status = 'succeeded'`를 unique로 관리합니다.
- 핵심 엔터티는 `deleted_at`을 사용합니다.
- 물리 삭제와 백업 보존은 운영 배포 설계에서 결정합니다.

## Decision semantics

`MATCH`, `NO_MATCH`, `UNKNOWN`은 후보 전체가 아니라 개별 규칙 평가에도 적용합니다. 후보 집계는 결정적인 제외 규칙, 필수 조건의 미확인, 충족 조건을 보존하며 단순 점수 하나로 근거를 숨기지 않습니다.

AI가 생성한 설명은 판정 레코드의 입력·규칙·근거를 벗어날 수 없습니다. AI 응답을 저장할 때는 모델·프롬프트 버전과 검증 결과를 기록하지만 AI 텍스트를 권위 있는 보험 규칙으로 저장하지 않습니다.

## Failure and recovery

- API는 안정적인 오류 코드와 재시도 가능 여부를 반환합니다.
- Worker 작업은 `queued`, `running`, `succeeded`, `retryable_failed`, `permanently_failed`, `cancelled` 상태를 사용합니다.
- lease가 만료된 작업은 다른 Worker가 가져갈 수 있습니다.
- 콘텐츠 해시와 멱등 키가 중복 결과를 방지합니다.
- 일부 문서 분석 실패가 다른 계약 조회를 차단하지 않습니다.
- 임시 파일 삭제 실패는 보안 이벤트이며 성공으로 숨기지 않습니다.
- Phase 1 암호화 PDF는 asynchronous API에서 `PASSWORD_REQUIRED`로 거부하며, password는 DB·job payload·log에 들어가지 않습니다.

API POST는 source key만 검증하고 `documents`와 `analysis_jobs`를 생성·재사용합니다. 파일 descriptor를 연 뒤 hash, 구조, `document_versions`, `extractions`를 결정하는 책임은 Worker에 있습니다. Unknown job 조회는 `ANALYSIS_JOB_NOT_FOUND`입니다.

## Deployment boundaries

Foundation CD는 `vMAJOR.MINOR.PATCH` 태그에서 Web/API/Worker 이미지를 GHCR에 게시하는 데서 끝납니다. 운영 데이터베이스, 도메인, 인증 리디렉션, 비밀 관리, 백업, Cloud Run은 전체 기능 개발 후 별도 설계합니다.

GHCR 이미지 성공은 운영 배포 또는 실제 데이터 수용 검증 성공을 뜻하지 않습니다.

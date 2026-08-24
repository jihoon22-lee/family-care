# FamilyCare 프로젝트 기반 설계

- 상태: 승인됨, Phase 0 완료
- 작성일: 2026-08-23
- 적용 범위: 공개 저장소의 문서, 개발환경, 최소 실행 골격, CI, GHCR 릴리스
- 완료 근거: PR #1, merge commit `0f632989df891ae944c012bfcce6c838009867a9`, PR 및 post-merge CI 일곱 required job 성공

## 1. 목적

FamilyCare는 가족의 실제 보험 가입 내역과 약관을 연결해 상황별 청구 후보를 찾고, 각 결과에 원문 근거와 미확인 조건을 제시하는 개인용 PWA다. 이 문서는 제품 기능을 구현하기 전에 저장소가 따라야 할 구조, 보안 경계, 실행 환경, 검증 및 릴리스 기준을 고정한다.

첫 기반 단계의 목표는 보험 판정 기능을 서둘러 만드는 것이 아니다. 후속 기능을 안전하게 추가할 수 있도록 다음 조건을 먼저 충족하는 것이다.

1. 공개 저장소에 실제 보험 문서와 개인정보가 들어가지 않는다.
2. Web, API, Worker의 최소 실행 단위와 책임 경계가 명확하다.
3. 로컬과 CI가 같은 잠금 파일과 명령을 사용한다.
4. 모든 변경이 자동 검증되고, 태그 릴리스가 재현 가능한 컨테이너를 생성한다.
5. 후속 기능은 독립적으로 검토하고 검증할 수 있는 작은 단계로 나뉜다.

## 2. 확정된 제품 원칙

### 2.1 사용자와 데이터 모델

- 실제 앱 사용자는 동일 권한을 갖는 관리자 두 명이다.
- 앱 사용자 계정과 보험 대상 가족 구성원은 서로 다른 개념으로 모델링한다.
- 다중 가정, 공개 회원가입, 초대, 역할별 권한, 결제는 범위에서 제외한다.
- 삭제는 즉시 물리 삭제가 아니라 soft delete와 휴지통 복구 흐름을 사용한다.

### 2.2 보험 판정

- 증권에서 실제 가입 담보를 확인한 후에만 약관 조건을 연결한다.
- 조건 판정은 `MATCH`, `NO_MATCH`, `UNKNOWN` 세 값으로 표현한다.
- 현재 입력만으로 후보를 먼저 반환하고, 추가 질문은 정확도를 높이는 선택 단계로 제공한다.
- 병원 방문 전의 불완전한 입력과 치료 후의 상세 입력을 모두 지원한다.
- 정액형과 실손형을 분리하고 서로 다른 계산·표시 규칙을 적용한다.
- 갱신형 특약은 최신 계약 상태를 확인하지 못하면 `UNKNOWN`으로 남긴다.
- AI는 자연어 구조화와 설명을 보조하며, 핵심 자격 판정과 금액 계산은 명시적 규칙이 담당한다.
- 결과에는 증권과 약관의 문서 식별자, 페이지, 조항 등 재확인 가능한 근거가 있어야 한다.
- 근거가 없거나 계약 상태가 불명확한 결과를 지급 확정처럼 표현하지 않는다.

### 2.3 외부 연동

- 초기 개발과 기본 CI는 외부 AI API, Google Drive, 운영 인증정보를 사용하지 않는다.
- 실제 원본 문서는 저장소 외부 디렉터리에서만 읽는다.
- Google Drive 연동은 후속 단계에서 특정 폴더를 읽기 전용으로 연결한다.
- Cloud Run을 포함한 운영 배포는 전체 기능 개발 후 별도 설계한다.

## 3. 범위

### 3.1 이번 기반 단계에 포함

- 프로젝트 문서 체계와 ADR
- 강한 `.gitignore`, 공개 가능한 `.env.example`, 저장소 안전 검사
- `pnpm` 기반 Web 워크스페이스
- `uv` 기반 API 및 Worker Python 워크스페이스
- PostgreSQL을 포함한 Docker Compose 로컬 환경
- Web, API, Worker의 최소 헬스체크와 테스트
- 합성 fixture만 사용하는 테스트 기반
- PR 및 `main` 브랜치 CI
- `v*` 태그에서 Web, API, Worker 이미지를 GHCR에 게시하는 릴리스 워크플로
- Dependabot, PR 템플릿, 기여 및 보안 정책

### 3.2 이번 기반 단계에서 제외

- 실제 보험 PDF 수집·추출·OCR
- 보험 계약과 담보의 실제 구조화
- 약관 검색과 보험금 계산
- 로그인과 세션 관리
- Google Drive 및 외부 AI 연동
- 실제 개인정보를 사용하는 검증
- 운영 인프라 프로비저닝과 Cloud Run 배포

제외 항목은 생략한 요구사항이 아니라, 각각 별도 설계와 수용 기준을 거쳐 구현할 후속 단계다.

## 4. 저장소 구조

```text
family-care/
├── apps/
│   ├── web/                  # React PWA와 브라우저 테스트
│   └── api/                  # FastAPI 진입점과 API 모듈
├── workers/
│   └── analyzer/             # 문서 분석 작업자 진입점
├── packages/
│   └── contracts/            # OpenAPI 및 언어 중립 이벤트 스키마
├── fixtures/
│   └── synthetic/            # 공개 가능한 완전 합성 데이터
├── infra/
│   ├── compose/              # 로컬 서비스 구성
│   └── containers/           # Web, API, Worker 이미지 정의
├── scripts/                  # 안전 검사와 공통 개발 명령
├── docs/
│   ├── adr/                  # 변경되지 않는 기술 결정 기록
│   ├── design/               # 기능·하위 시스템 상세 설계
│   └── plan/                 # 단계별 실행 계획과 수용 기준
├── .github/
│   ├── workflows/            # CI와 GHCR 릴리스
│   └── PULL_REQUEST_TEMPLATE.md
├── README.md
├── AGENTS.md
├── CHANGELOG.md
├── CONTRIBUTING.md
└── SECURITY.md
```

루트는 조정과 공통 정책만 담당한다. 제품 코드는 실행 단위별 디렉터리에 두고, 실제 비즈니스 로직은 API와 Worker에서 재사용할 수 있도록 작은 Python 패키지 경계로 분리한다. 언어 간 계약은 구현 코드를 복제하지 않고 OpenAPI와 JSON Schema로 전달한다.

## 5. 실행 단위와 책임

### 5.1 Web

- React, TypeScript, Vite 기반의 설치 가능한 PWA다.
- 초기 골격은 앱 셸과 빌드 정보만 제공한다.
- 보험 PDF, API 응답, 의료정보를 서비스 워커 캐시에 저장하지 않는다.
- 후속 API 클라이언트는 커밋된 OpenAPI 계약에서 생성한다.
- 컨테이너는 정적 산출물을 비특권 웹 서버로 제공한다.

### 5.2 API

- FastAPI 기반 모듈형 모놀리스다.
- 초기 골격은 `/health/live`, `/health/ready`와 버전 메타데이터만 제공한다.
- 기능 모듈은 `identity`, `documents`, `policies`, `clauses`, `decisions`, `claims` 경계를 따른다.
- HTTP 오류는 안정적인 오류 코드와 민감정보가 없는 메시지를 반환한다.
- OpenAPI는 외부 계약의 기준이며 CI가 커밋된 계약과 구현의 차이를 검사한다.

### 5.3 Analyzer Worker

- PostgreSQL 작업 큐에서 분석 작업을 가져오는 별도 프로세스다.
- 초기 골격은 프로세스 시작과 의존성 상태만 검증한다.
- 후속 작업 처리는 멱등 키를 사용하고, 재시도 횟수와 최종 실패 상태를 기록한다.
- PDF 암호 해제본, 페이지 이미지, OCR 중간 산출물은 저장소 밖의 작업별 임시 디렉터리에 만들고 종료 시 삭제한다.
- 로그에는 문서 본문, 검색어, 진단명, 증권번호, 파일 경로를 기록하지 않는다.

### 5.4 Contracts

- API OpenAPI 문서와 비동기 작업 JSON Schema를 보관한다.
- 계약 버전은 URL 및 스키마의 명시적 버전 필드로 관리한다.
- 호환성을 깨는 변경은 새 계약 버전과 CHANGELOG 항목이 필요하다.
- TypeScript와 Python의 생성 산출물은 계약에서 재생성할 수 있어야 한다.

## 6. 기술 기준

기준 버전은 2026-08-23의 공식 지원 상태를 따른다.

| 영역 | 기준 | 선택 이유 |
|---|---|---|
| Node.js | 24 LTS | 운영용 LTS 계열이며 최신 도구와 호환된다. |
| JavaScript 패키지 | pnpm 11.22.0 | 워크스페이스와 엄격한 의존성 경계를 제공한다. |
| Python | 3.14 | 최신 안정 기능 계열이며 장기 지원 창을 확보한다. |
| Python 패키지 | uv | 잠금 파일, 가상환경, 실행 명령을 하나로 통일한다. |
| 데이터베이스 | PostgreSQL 18 | 지원 중인 최신 주 버전이며 전문검색과 확장 기반을 제공한다. |
| 로컬 오케스트레이션 | Docker Compose v2 | 개발 서비스의 재현 가능한 실행 경계를 제공한다. |

Node.js와 Python은 주·부 버전을 저장소 설정과 CI에서 일치시킨다. 애플리케이션 라이브러리는 범위를 선언하고 잠금 파일에 정확한 해석 결과를 기록한다. 컨테이너 베이스 이미지는 변경 가능한 별칭만 사용하지 않고 검토 가능한 버전으로 고정한다.

## 7. 데이터 흐름과 신뢰 경계

```text
[저장소 외부 원본]
        |
        v
[격리된 임시 작업공간] -- 실패/완료 후 삭제
        |
        v
[텍스트·표·페이지 근거 추출]
        |
        +--> [Document / Extraction 검수 상태]
        |
        v
[실제 가입 계약·담보 원장]
        |
        v
[약관 조항·별표 연결]
        |
        v
[명시적 규칙 판정: MATCH / NO_MATCH / UNKNOWN]
        |
        v
[근거, 부족 정보, 확정 불가 사유가 포함된 결과]
```

원본과 중간 산출물은 공개 저장소 경계 밖에 있다. 구조화 데이터도 실제 자료를 사용하면 로컬 또는 운영 데이터 영역에만 저장한다. 공개 fixture는 실제 문서의 문구·상품명·금액·식별자를 변형한 것이 아니라 처음부터 만든 합성 데이터여야 한다.

## 8. 영속성 및 작업 처리

- PostgreSQL을 단일 영속성 계층으로 시작한다.
- 초기 비동기 작업 큐는 PostgreSQL 행 잠금과 상태 전이를 사용하며 Redis를 추가하지 않는다.
- 작업 상태는 `queued`, `running`, `succeeded`, `retryable_failed`, `permanently_failed`, `cancelled`로 구분한다.
- 소비자는 `FOR UPDATE SKIP LOCKED` 방식으로 작업을 독점하고 lease 만료 후 복구할 수 있어야 한다.
- 동일 문서는 SHA-256 콘텐츠 해시로 중복 분석을 막는다.
- 핵심 엔터티는 `deleted_at`을 사용해 soft delete하고, 별도 정리 정책이 승인된 뒤에만 물리 삭제한다.
- 전문검색을 먼저 구현하고, 벡터 검색은 측정 가능한 검색 품질 개선이 확인될 때 pgvector로 추가한다.

## 9. 오류 처리와 관측성

- 사용자에게는 안정적인 오류 코드, 다음 행동, 재시도 가능 여부를 제공한다.
- 내부 예외와 사용자 메시지를 분리한다.
- 구조화 로그의 허용 필드는 요청 ID, 작업 ID, 모듈, 오류 코드, 소요 시간, 재시도 횟수다.
- 입력 본문, 문서 텍스트, 의료 사건 설명, 개인 식별자, 인증 토큰은 로그 금지 필드다.
- Worker 종료 시 진행 중 작업은 lease로 복구하며, 중복 처리되어도 결과가 달라지지 않게 한다.
- 임시 파일 삭제 실패는 숨기지 않고 보안 이벤트로 기록하되 실제 경로와 파일명은 마스킹한다.

## 10. 보안 및 개인정보 경계

### 10.1 저장소에 허용

- 프로그램 코드와 공개 문서
- 완전 합성된 가족, 계약, 담보, 약관, 의료 사건 fixture
- 비밀값이 없는 환경변수 예시
- 로컬 개발 및 CI용 빈 데이터베이스 스키마

### 10.2 저장소에 금지

- 실제 보험증권, 약관, 가입제안서, 병원 문서
- 실제 문서에서 추출한 텍스트, 표, 이미지, OCR 결과, 임베딩
- PDF 암호, API 키, OAuth 비밀값, 서비스 계정 파일, 세션 키
- 실제 인명, 이메일, 주소, 전화번호, 생년월일, 증권번호, 보험금액
- PostgreSQL 데이터 디렉터리, 덤프, SQLite 파일, 로그, 코어 덤프
- 실제 Google Drive 폴더·파일 식별자

`.gitignore`만을 보안 경계로 간주하지 않는다. 로컬 검사, CI 금지 파일 검사, secret scanning, PR 체크리스트를 겹쳐 적용한다. 안전 검사는 실제 개인정보 값을 패턴 목록에 넣지 않고 확장자, 파일 위치, 비밀 형식, 허용 목록을 기준으로 동작한다.

## 11. 검증 전략

### 11.1 빠른 검증

- Web: formatter 검사, ESLint, TypeScript, 단위 테스트, 프로덕션 빌드
- Python: Ruff formatter·lint, 정적 타입 검사, 단위 테스트
- 저장소: 금지 파일, 비밀값, 대용량 파일, lockfile 변경 일관성
- 문서: Markdown 구조와 내부 링크

### 11.2 통합 검증

- PostgreSQL 18에서 마이그레이션을 빈 DB에 적용하고 되돌릴 수 있는지 확인한다.
- API readiness와 Worker DB 연결을 합성 설정으로 확인한다.
- OpenAPI와 JSON Schema 예제를 검증한다.
- 합성 fixture로만 API부터 Worker까지 최소 경로를 검증한다.

### 11.3 빌드 검증

- Web, API, Worker 이미지를 실제로 빌드한다.
- 컨테이너가 root 사용자로 실행되지 않는지 검사한다.
- 태그가 없는 PR에서는 이미지를 게시하지 않는다.
- 모든 기능 단계는 테스트 실패를 먼저 확인하고 최소 구현 후 통과시키는 흐름을 따른다.

실제 보험 자료를 사용하는 로컬 검증은 공개 CI의 완료 조건이 아니다. 해당 검증은 저장소 외부 절차로 별도 기록하고, 실행하지 못한 플랫폼·데이터 검증을 통과한 것으로 보고하지 않는다.

## 12. CI와 릴리스

### 12.1 CI

PR과 `main` push에서 다음 작업을 독립적으로 실행한다.

1. 저장소 안전·비밀·문서 검사
2. Web lint, typecheck, unit test, build
3. API와 Worker format, lint, typecheck, unit test
4. PostgreSQL 통합·마이그레이션·계약 검사
5. Web, API, Worker 컨테이너 build-only 검사

워크플로 권한은 기본 `contents: read`로 제한한다. 외부 기여자의 PR은 비밀값 없이 동일한 검증을 실행할 수 있어야 한다.

### 12.2 GHCR 릴리스

- `vMAJOR.MINOR.PATCH` 형식 태그만 릴리스를 시작한다.
- CI와 같은 전체 검증이 통과한 뒤 이미지를 빌드한다.
- 이미지는 `ghcr.io/<owner>/<repository>-web`, `-api`, `-worker`에 게시한다.
- 각 이미지는 전체 버전, 주·부 버전, 커밋 SHA 태그를 갖는다.
- 릴리스 작업만 `packages: write`를 사용하고 `GITHUB_TOKEN` 이외의 장기 비밀값을 요구하지 않는다.
- 실패한 구성 요소가 있으면 어떤 이미지도 완료된 릴리스로 선언하지 않는다.
- GHCR 게시 성공은 운영 배포 성공을 의미하지 않는다.

### 12.3 Protect main ruleset

GitHub의 활성 `Protect main` ruleset은 다음 저장소 정책을 적용합니다.

- PR required
- Required strict checks: `Repository safety`, `Web`, `Python`, `PostgreSQL integration`, `Container (web)`, `Container (api)`, `Container (worker)`
- Branch deletion blocked
- Force-push blocked
- Merge commit only
- Review threads must be resolved
- No bypass actors are configured

Required approving review count는 `0`입니다. Ruleset 상태와 required check display name은 GitHub에서 별도로 확인하며, 로컬 문서·코드 검증 결과를 ruleset 적용 증거로 확대하지 않습니다.

## 13. 문서 체계

- `README.md`: 프로젝트 목적, 안전 경고, 빠른 시작, 현재 범위
- `AGENTS.md`: 작업 순서, 보안 금지사항, 디렉터리 책임, 필수 검증, 완료 보고 기준
- `CHANGELOG.md`: Keep a Changelog 형식의 사용자 영향 변경 내역
- `docs/architecture.md`: 장기 시스템 구조와 주요 흐름
- `docs/guide.md`: 개발·관리 사용법과 안전한 로컬 데이터 연결 방법
- `docs/design/*.md`: 하위 시스템별 계약과 상세 설계
- `docs/plan/*.md`: 구현 순서, 정확한 파일, 테스트, 커밋 단위, 수용 기준
- `docs/adr/*.md`: 선택한 대안과 결과가 바뀌지 않는 결정 기록
- `SECURITY.md`: 취약점 제보와 민감정보 사고 대응
- `CONTRIBUTING.md`: 공개 저장소 기여 규칙과 합성 데이터 원칙

문서와 구현이 충돌하면 구현을 그대로 정당화하지 않고, 어떤 쪽을 바꿀지 ADR 또는 PR 설명에서 명시한다.

## 14. 단계별 전달 계획

1. **Foundation (Phase 0, 완료)**: 문서, 안전장치, 실행 골격, CI, GHCR 릴리스
2. **Synthetic ingestion (Phase 1, 계획)**: 합성 PDF 추출, 페이지 근거, 임시 파일 수명주기
3. **Policy ledger**: 계약 당사자, 실제 가입 담보, 계약 상태, 관리자 검수
4. **Clause linking**: 담보와 약관 조항·별표 연결, 검색 인덱스
5. **Decision engine**: 3값 판정, 부족 정보, 정액·실손 분기, 근거 추적
6. **Event and result UX**: 사전·사후 입력, 선택형 추가 질문, 결과 카드
7. **Claim workflow**: 서류 체크리스트, 접수·보완·지급 이력, soft delete
8. **Authentication**: 동일 권한 관리자 두 명, 허용 계정, 세션 보안
9. **Private-data acceptance**: 저장소 밖 실제 문서로 수동 승인된 검증
10. **Optional integrations**: 읽기 전용 Drive와 최소 공개 원칙의 AI 보조
11. **Production deployment**: Cloud Run 포함 운영 대상, 비용, 백업, 키 관리 재설계

각 단계는 별도의 상세 설계와 구현 계획을 승인받은 뒤 시작한다. 이전 단계의 테스트와 데이터 경계를 깨뜨리는 변경은 다음 단계의 일부로 묵시적으로 포함하지 않는다.

## 15. Foundation 완료와 Phase 1 경계

Foundation은 PR #1에서 merge commit `0f632989df891ae944c012bfcce6c838009867a9`로 `main`에 병합되었고, PR 및 post-merge GitHub Actions의 일곱 required job이 성공했다. 이 증거는 Foundation 코드와 CI 검증을 의미한다. 태그 생성과 GHCR publish, Cloud Run 배포, Windows·실제 기기 확인, 실제 보험 자료와 private external root 확인은 수행하지 않았으며 Foundation 완료 주장에 포함하지 않는다.

Phase 1은 `docs/design/pdf-ingestion.md`와 `docs/plan/002-synthetic-pdf-ingestion.md`의 합성 전용 계획을 따른다. 구현과 CI는 실제 PDF와 private external root를 열지 않고, 합성 fixture를 checkout 밖의 임시 root에 복사해서만 실행한다. 인증 provider는 Phase 7에 남아 있으므로 Phase 1 API는 local synthetic-only 개발 경계이며 production-safe endpoint가 아니다. Phase 1이 만드는 최소 Document·DocumentVersion·Extraction·ExtractionPage·ExtractionBlock·ExtractionTable·ExtractionCell·AnalysisJob 모델은 Policy Ledger(Phase 2)의 선행 의존성이다.

## 16. 완료 조건

Foundation 단계는 다음 증거가 모두 있을 때 완료된다.

- 요구사항, 아키텍처, 보안 경계, 기능별 설계와 단계별 계획이 저장소에 있다.
- 새 개발자가 문서만으로 로컬 Web, API, Worker, PostgreSQL을 실행할 수 있다.
- 외부 비밀값과 실제 데이터 없이 전체 CI가 통과한다.
- 금지 파일과 대표적인 테스트 비밀값을 안전 검사가 거부한다.
- Web, API, Worker 이미지가 로컬에서 빌드되고 비특권 사용자로 실행된다.
- 테스트용 태그가 아닌 검증 절차로 GHCR 워크플로 구문과 권한 경계를 확인한다.
- 실제 보험 자료와 개인정보가 Git 인덱스 및 커밋에 포함되지 않았음을 확인한다.
- Cloud Run과 실제 데이터 검증은 명시적으로 미실행 상태로 보고한다.

## 17. 명시적 결정

- 공개 저장소지만 `LICENSE` 파일을 두지 않으며 재사용 권한을 자동으로 부여하지 않는다.
- 초기 구조는 마이크로서비스가 아닌 모듈형 모놀리스와 별도 Worker다.
- 초기 작업 큐는 PostgreSQL을 사용하며 Redis나 별도 브로커를 추가하지 않는다.
- CI/CD는 GHCR 이미지 게시까지이며 운영 배포를 포함하지 않는다.
- 실제 데이터가 필요한 기능은 합성 데이터로 먼저 완성하고, 비공개 검증은 별도 완료 경계로 남긴다.

## 18. 기술 기준 참고 자료

- [Node.js 릴리스와 LTS 상태](https://nodejs.org/en/about/previous-releases)
- [Python 3.14.7 릴리스](https://www.python.org/downloads/release/python-3147/)
- [PostgreSQL 버전 지원 정책](https://www.postgresql.org/support/versioning/)

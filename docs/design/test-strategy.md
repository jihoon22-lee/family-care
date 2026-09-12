# Test strategy

- 상태: Phase 0~8, private knowledge/advisory·원장 대사 구현과 `v0.1.0`~`v0.4.0` release 검증 반영
- 원칙: 최신 실행 증거 없는 완료 주장을 하지 않음

## Scope

문서·보안 정책, Web, API, Worker, 데이터베이스, 계약, 컨테이너, workflow, 향후 보험 판정까지 각 계층의 검증 책임과 완료 경계를 정의합니다.

## Inputs

- 승인된 설계와 구현 계획의 수용 조건
- 변경된 코드·계약·migration·문서
- 합성 fixture와 고정된 toolchain
- 로컬 및 GitHub Actions 실행 결과

## Outputs

- 재현 가능한 pass/fail 결과
- 실패 원인과 회귀 검사
- 실행 명령, test 수, 버전이 포함된 증거
- 외부·플랫폼·실제 자료의 미검증 목록

## Test data policy

- 공개 테스트는 처음부터 만든 합성 데이터만 사용합니다.
- 실제 문서의 문구·레이아웃·금액을 복사한 fixture는 금지합니다.
- 합성 PDF는 `fixtures/synthetic/`에서만 허용하고 작성 의도를 기록합니다.
- 무작위 값은 재현 가능한 seed를 사용합니다.
- 비밀 검사는 실제 credential이 아닌 분할·조립한 합성 문자열을 사용합니다.
- Phase 1 테스트는 reportlab으로 만든 wholly synthetic PDF를 checkout 밖 임시 root에 복사하고, `FAMILYCARE_DOCUMENT_ROOT`를 그 임시 root로만 설정합니다.
- Phase 1 구현과 CI는 실제 PDF와 private external root를 열지 않습니다.
- 공개 CI는 OpenAI를 호출하지 않고 처음부터 만든 request/response fixture로
  structurer·verifier·assistance 계약을 검증합니다.
- 보호된 package/runtime acceptance는 사용자 승인 범위의 저장소 밖 source와 local key로만
  실행하고 CI 결과와 분리합니다. 실제 document-structuring provider acceptance는 아직 별도
  미검증 경계입니다.

## Test layers

### Policy and structure checks

- 필수 문서와 heading
- 미완성 표기
- 금지 확장자·경로·크기
- 브랜치·커밋 convention
- workflow event·권한·action pin
- 계약 생성 drift

빠르고 외부 서비스가 없어야 하므로 모든 PR의 첫 관문으로 실행합니다.

`scripts/check_workflows.py`는 release workflow의 job-level `env`에서 `runner` context를
사용하는 회귀를 명시적으로 거부합니다. 모든 GitHub expression 위치 규칙을 자체 구현하지는
않으므로 release workflow 변경과 새 tag 전에는
`actionlint -oneline .github/workflows/*.yml`도 실행합니다.
릴리스 단위 테스트에는 DB 환경을 상속하지 않는다. PostgreSQL 통합 step은 전용 DB URL·
파괴적 시험 동의와 해당 서비스의 `FAMILYCARE_TEST_POSTGRES_CONTAINER`를 함께 받는다.
게시 전 foundation에서 기존 browser E2E와 Worker OCR 언어·한글 glyph smoke도 실행한다.
브라우저 의존성 설치 전에는 CI runner의 사용하지 않는 Chrome 전용 `.list`/`.sources`만
제외하며 Ubuntu 저장소·무결성 검사·`--with-deps chromium`을 유지한다. Python의 현재
CHANGELOG 회귀는 모든 버전 섹션과 현재 package 버전의 존재를 검사해 범주 순서 오류를
이미지 게시 전에 거부한다.
이 환경 바인딩과 실행 검사가 빠지면 workflow 정책 검사에서 거부한다.

### Unit tests

Web component, Python 함수·서비스, 규칙 연산자, 경로 검증, 계산을 격리해 검증합니다. mock보다 실제 작은 값을 사용하고, 외부 I/O는 명시적 인터페이스로 주입합니다.

새 동작은 다음 순서를 지킵니다.

1. 실패하는 하나의 테스트 작성
2. 기능 부재로 실패하는지 확인
3. 최소 구현
4. 해당 테스트와 관련 전체 suite 통과
5. 동작을 바꾸지 않는 정리

### Contract tests

- FastAPI 생성 OpenAPI와 커밋된 계약 비교
- JSON Schema와 예제
- 오류 코드와 response 형태
- 호환성을 깨는 변경 탐지

### Database tests

- 빈 PostgreSQL 18에서 head까지 migration
- downgrade 가능한 revision의 base 전환
- transaction과 constraint
- soft delete와 복원
- 작업 lease와 동시 소비

SQLite로 PostgreSQL 행 잠금·전문검색 동작을 대체 검증하지 않습니다.

### Integration tests

합성 설정으로 API, PostgreSQL, Worker의 실제 경계를 검증합니다. 테스트는 독립 schema 또는 transaction을 사용하고 공개 CI에서 외부 AI·Drive를 호출하지 않습니다. AI adapter는 합성 structurer/verifier response를 사용해 동일한 schema, retry, publish 경계를 통과합니다.

integration marker가 선택되면 root pytest hook는 fixture setup 전에 destructive database guard를 실행한다. `FAMILYCARE_DATABASE_URL`은 fallback으로 사용하지 않으며, 별도 `FAMILYCARE_TEST_DATABASE_URL`, 정확한 `FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true`, 접속 후 `current_database()` 이름의 standalone `test` 또는 `ci` marker가 모두 필요하다. 검증이 끝난 뒤에만 legacy fixture가 읽는 runtime URL을 test URL로 덮어쓴다.

```bash
FAMILYCARE_TEST_DATABASE_URL=postgresql+psycopg://familycare:synthetic-only@127.0.0.1:55439/familycare_review_test \
FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true \
TMPDIR=/tmp uv run pytest -m integration apps/api/tests workers/analyzer/tests -q
```

이 예시는 disposable 합성 test database의 형식일 뿐이다. shared 개발·private·운영 database나 이름 marker만 닮은 database를 재사용하지 않는다.

### Container tests

- 실제 image build
- 비특권 runtime UID
- healthcheck
- Worker의 비내장 한글 PDF 렌더링: 합성 CID-font fixture의 한글 각 글자 영역과 영문 대조군을 실제 이미지 안에서 검사하며, 글꼴 누락을 skip으로 숨기지 않는다.
- Dockerfile build context
- `.env`와 Git metadata 미포함
- Web static cache header
- Uvicorn/Web request access logs disabled; Web upstream errors do not persist request URLs.
- Dockerfile의 정확한 stage 수·순서와 완전한 patch 태그: Node 24 Alpine, Python 3.14 slim, uv 0.12
- Web runtime image pin `nginxinc/nginx-unprivileged:1.31.2-alpine3.23`과 `scripts/check_containers.py` exact expectation

`scripts/check_containers.py`는 Dockerfile을 image version의 단일 source of truth로 사용한다. 승인된 지원 계열 안의 Dependabot patch/minor 갱신은 별도 상수 수정 없이 허용하지만, `latest`, `node:24-alpine`, uv 0.13, Node 25, Python 3.15, stage 추가·누락·순서 변경은 명시적인 정책 변경 없이는 거부한다. 실제 PR CI는 이 정적 검사와 별도로 Web, API, Worker 이미지를 각각 build-only로 검증한다.

### Browser and PWA tests

- 주요 입력·결과 흐름
- 접근성 role과 keyboard
- 작은 화면
- 서비스 워커 cache key
- API/PDF no-store
- installability
- 두 관리자 login과 session expiry
- 자연어 input, editable facts, manual receipt lines
- 행동 우선 result cards와 Evidence viewer
- ClaimCase checklist와 수동 상태 기록

CI 브라우저 자동화와 Windows·모바일 실제 기기 검증을 별도 결과로 보고합니다.

### Security tests

- source-to-sink 경로를 확인하는 권한 테스트
- malicious PDF 제한
- 경로 탈출
- 민감 로그 부재
- secret scan
- dependency·container scan

PDF security tests additionally verify 128 MiB source input, 128 MiB decrypted plaintext/archive payload bounds, 500 pages, 120초 parent wall, 90초 child CPU, 1536 MiB address space, 64 MiB parser output/`RLIMIT_FSIZE`, 64 open descriptors, mode `0700` work directories, mode `0600` files, 1 MiB streaming SHA-256, `%PDF-` magic, password absence, and symlink rejection.

v0.1 security tests additionally verify family-scoped batch password reuse and disposal, encrypted archive round-trip and tamper detection, selective OCR cleanup, Worker-only OpenAI key injection, external payload allowlist, local session/CSRF/object scope, and gateway-only host exposure.

정적 검사만 수행한 경우 동적 공격 재현을 수행했다고 보고하지 않습니다.

WSL의 측정된 메모리 압력에서는 Vitest worker 시작 timeout을 피하기 위해 Web `test` script가 `vitest run --maxWorkers=1`을 사용합니다. 이는 테스트 범위를 줄이지 않고 worker 동시성만 직렬화하며, Web 검증은 Python·컨테이너 검증과 함께 직렬로 실행합니다.

## Verification by change

2026-09-12 사용자 요청에 따라 커밋의 경량 확인과 PR 완료의 상세 검증을 분리한다.

| 작업 유형/시점 | 필요한 검증 |
|---|---|
| 읽기 전용 검토 | 질문에 필요한 조회·근거 확인. 불필요한 코드 검증은 실행하지 않음 |
| 구현 중·각 커밋 | 변경 파일의 문법·형식 확인과 계획 대비 diff 검토. 테스트·통합·빌드는 커밋 조건이 아님 |
| 개발을 막는 구체적 문제 | 원인이나 구현 선택을 해결할 최소 재현 검사만 실행 |
| 문서·AGENTS·스킬만 바꾸는 PR 완료 | 문서·저장소 안전·diff 검사; 스킬 구조와 링크·실행 조건 확인 |
| 코드·실행 설정 PR의 계획 작업 완료 | 전체 diff를 검토하고 필수 검사와 변경 영역의 통합·수용 검증을 한 번 집중 수행 |
| 완료 검증의 실패 수정 | 실패 원인을 고친 뒤 결과가 달라질 수 있는 검사만 재실행 |
| 릴리스·보호된 자료 적용 | 코드 PR에서 확인하지 못한 대상 소스·schema·이미지·환경의 승인된 수용 검증 |

PR의 구현·필요 테스트·문서가 모두 준비된 뒤 상세 검증을 시작한다. 테스트를 작성하는
시점과 실행하는 시점은 구분하며 매 기능·커밋마다 RED/GREEN 실행을 의무화하지 않는다.
검증 횟수를 늘리기 위해 작업을 더 작은 검증 묶음으로 나누지 않는다.

상세 검증은 동일 입력의 CI와 로컬 결과를 합쳐 한 번 충족한다. CI에서 실행하는 무거운
전체 테스트·통합·이미지 빌드를 로컬에서 그대로 중복 실행하지 않는다. 로컬 실행은
CI가 다루지 않는 수용 범위나 구체적 실패 재현에 사용한다. 필수 CI check는 유지하며,
중간 확인을 위해 반복 push하거나 성공한 workflow를 수동 재시작하지 않는다.

혼합 변경은 필요한 범위의 합집합을 한 번 검증한다. 문서가 실행 설정·CI 정책을 바꾸면
실제 설정과 소비 명령도 검토한다. 문서 전용 변경 역시 required CI를 우회하지 않는다.

결과는 명령·시점·소스 SHA와 관련 미커밋 변경·잠금 파일·설정·fixture·환경에 연결한다.
수정 후에는 영향받는 검사만 다시 실행한다. 전체 재실행은 공용 계약·공통 fixture 등
영향이 넓다는 구체적 근거가 있을 때만 선택하고 이유를 기록한다. 문서 수정·새 SHA·
세션 재개·상태 보고만으로 기존 코드 검증을 무효화하지 않는다. 실제 자료 진단도 구현
결정을 바꾸는 미확인 질문이 있을 때만 추가하며 같은 가설의 관찰을 반복하지 않는다.

## Required completion commands

아래는 PR 계획 완료 시 충족할 검사 목록이며 커밋마다 실행하는 목록이 아니다.
같은 입력을 검증한 CI 결과를 우선 활용하고 로컬에서는 미충족 항목만 실행한다.
로컬에서 여러 검사가 필요하면 다음 순서로 직렬 실행한다. 문서 전용 PR의 로컬 범위는
앞의 두 검사와 마지막 diff 검사다. 추가 범위는 변경된 경계에 따라 선택한다.

```bash
python3 scripts/check_documentation.py
python3 scripts/check_repository_safety.py
corepack pnpm web:check
TMPDIR=/tmp uv run ruff format --check .
TMPDIR=/tmp uv run ruff check .
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run python scripts/check_containers.py
TMPDIR=/tmp uv run python scripts/check_workflows.py
git diff --check
```

pnpm 버전은 루트 `package.json`의 `packageManager`를 사용한다. Corepack을 거쳐
잠금 버전을 선택하며 임의 최신 버전으로 실행하지 않는다.

- 기본 pytest는 `pyproject.toml`에 따라 integration을 제외한다. migration/repository/queue 변경은 위의 전용 PostgreSQL 통합 검증을 추가한다.
- `web:check`는 format/lint/type/unit/build를 포함한다. browser E2E는 별도 `corepack pnpm --filter @familycare/web test:e2e`로 실행하며 mock/실제 backend 연결을 구분한다.
- `check_containers.py`는 정적 정책 검사다. 이미지 빌드·runtime 검증과 동등하지 않으며 Docker 변경은 해당 이미지와 Compose 구성을 추가 확인한다.
- workflow 변경은 `actionlint -oneline .github/workflows/*.yml`도 실행한다.
- 커밋 전 `TMPDIR=/tmp uv run python scripts/check_git_conventions.py`는 브랜치만 검사한다. 커밋 후에는 확인한 PR 기준 SHA를 사용해 `TMPDIR=/tmp uv run python scripts/check_git_conventions.py --range <base-sha>..HEAD`로 실제 커밋 제목도 검사한다. 빈 범위를 커밋 검증 성공으로 처리하지 않는다.
- 실제 자료·외부 provider·운영·Windows/모바일 확인은 공개 합성 CI와 별도 상태로 남긴다.

## Foundation command matrix

| Area | Command | External secret |
|---|---|---|
| Documentation | `python3 scripts/check_documentation.py` | 없음 |
| Repository safety | `python3 scripts/check_repository_safety.py` | 없음 |
| Web | `corepack pnpm web:check` (루트 `packageManager` 버전) | 없음 |
| Python style | `uv run ruff format --check ...` | 없음 |
| Python lint | `uv run ruff check ...` | 없음 |
| Python types | `uv run mypy ...` | 없음 |
| Python tests | `uv run pytest ...` | 없음 |
| Contracts | `uv run python scripts/check_contracts.py` | 없음 |
| Migrations | `uv run alembic ... upgrade head` | 합성 로컬 DB |
| Containers | 개별 `docker compose ... build` | 없음 |
| Workflows | `uv run python scripts/check_workflows.py` (Dependabot ignore policy 포함) | 없음 |
| Release parser preflight | `actionlint -oneline .github/workflows/*.yml` | 없음 |

Phase 1 feature branches also run:

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_intake.py workers/analyzer/tests/test_pdf_extraction.py -q
TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_api.py -q
```

These commands use synthetic fixtures only. They do not discover, open, or copy any private external root.

## Resource policy

현재 WSL 환경에서는 frontend, Python, Docker 검증을 직렬로 실행합니다. 컨테이너도 Web, API, Worker 순서로 하나씩 빌드합니다. Docker 작업 전 `free -h`, `docker ps`로 자원과 소유 대상을 확인합니다. v0.1 검증을 위해 WSL swap 설정을 변경하지 않습니다.

메모리 부족으로 중단된 검사는 실패 또는 미완료이며 통과로 보고하지 않습니다.

## Invariants

1. 새 동작은 수용 조건과 실패 경계를 검증하는 테스트를 함께 가지며, PR 계획 완료 시 실행합니다.
2. 공개 CI는 외부 비밀값과 실제 자료 없이 실행됩니다.
3. PostgreSQL 동작을 SQLite 통과로 대체하지 않습니다.
4. 일부 검사 성공을 전체 완료로 보고하지 않습니다.
5. flaky 재실행 성공을 원인 해결로 간주하지 않습니다.

## Failure behavior

- 예상과 다른 이유로 실패한 테스트는 환경 문제와 제품 문제를 구분하고 해당 원인만 수정합니다.
- 필수 검사가 중단·취소·skip되면 완료 조건을 충족하지 못합니다.
- CI 전용 실패는 로그를 확인하고 가능한 경우 같은 명령을 로컬에서 재현합니다.
- 접근할 수 없는 Windows·실제 자료·운영 검증은 실패가 아니라 명시적인 미검증 경계로 보고합니다.

## Security considerations

- 테스트 출력과 snapshot에 실제 데이터가 없는지 검사합니다.
- secret 탐지 fixture는 실제 credential을 사용하지 않습니다.
- 실패 로그가 요청 본문·문서 경로를 노출하지 않게 합니다.
- 공격 입력 테스트는 격리된 합성 파일과 제한된 자원에서 실행합니다.

## Coverage expectations

숫자 하나의 전체 coverage 목표보다 위험 기반 기준을 사용합니다.

- 판정 규칙 연산자와 계산: 모든 branch와 경계값
- 권한·경로·secret 방어: 성공·거부 쌍
- 임시 파일 수명주기: 성공·실패·취소·강제 종료
- API 계약: 모든 endpoint status와 오류 envelope
- UI: 핵심 사용자 흐름과 접근성 role
- PDF ingestion: parser boundary, path safety, coordinate normalization, quality-v1 classification, cleanup, and AnalysisJob lease transitions

Coverage 감소는 누락 테스트를 확인하는 신호이며, 생성 코드·불가능 branch 제외는 근거를 문서화합니다.

## Flaky test policy

- 재실행 통과만으로 성공 처리하지 않습니다.
- 시간, seed, 포트, 비동기 대기, 외부 네트워크 원인을 확인합니다.
- flaky test를 무조건 skip하거나 required check에서 제거하지 않습니다.
- 격리가 필요하면 담당 issue와 만료 조건을 기록하되 개인정보·기능 핵심 경로는 격리하지 않습니다.

## Completion evidence

각 PR은 다음을 기록합니다.

- 실행한 정확한 명령
- 실행 시점, 소스 SHA, 관련 미커밋 변경과 사용한 설정/fixture 범위
- test 수와 failure 수
- lint/type/build exit 결과
- 사용한 PostgreSQL·Node·Python 주 버전
- 컨테이너 build와 runtime UID 확인
- 실행하지 않은 Windows, 실제 모바일, 실제 자료, 외부 제공자 검증

CI 성공은 실제 보험 판정 정확도나 운영 배포를 증명하지 않습니다.

## Future acceptance suites

- 합성 PDF golden extraction
- Rider와 Clause linking benchmark
- 규칙별 decision table
- 사전·사후 사건 end-to-end
- 청구 상태 전이
- 인증과 객체 scope
- 비공개 실제 자료 수동 검수 보고

## Current acceptance matrix

- synthetic contract-to-claim E2E without external secrets
- encrypted synthetic batch with one-time password and partial failure
- native extraction plus local Korean/English OCR only for classified pages
- AI structurer/verifier success, disagreement, invalid Evidence and provider failure
- fixed and partial indemnity decision tables
- two-admin login, CSRF, session expiry and device revoke
- Docker Compose migration, restart, DB/archive persistence and job recovery
- Tailscale HTTPS and authenticated local-browser checks, reported separately from CI
- protected package validation, restored-database/apply checks and catalog/result acceptance without
  committing source, extraction or result
- Windows browser, mobile PWA, other-device and full disaster-recovery checks explicitly left
  unverified until executed

## Tests

이 전략 자체는 `scripts/check_documentation.py`의 필수 heading 검사, CI command matrix와 로컬 Make target의 동등성 검사, PR 템플릿의 증거 필드 검사로 검증합니다. 각 기능 설계는 위 계층 중 적용 가능한 테스트를 구체적인 사례와 명령으로 다시 정의합니다.

## Deferred decisions

검색 품질 지표 목표, 남은 실제 형식 acceptance와 운영 부하 목표는 각 feature plan에서 합성
baseline을 먼저 정합니다. Google Drive, public deployment, multi-provider AI, Windows/mobile과
전체 재해 복구는 별도 승인 전까지 남깁니다.

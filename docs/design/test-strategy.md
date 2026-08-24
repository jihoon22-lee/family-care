# Test strategy

- 상태: Foundation·Phase 1 완료, v0.1 검증 기준 승인
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
- v0.1 공개 CI는 OpenAI를 호출하지 않고 처음부터 만든 request/response fixture로 structurer·verifier 계약을 검증합니다.
- 실제 PDF와 OpenAI acceptance는 사용자가 지정한 저장소 밖 source와 local key로만 실행하고 CI 결과와 분리합니다.

## Test layers

### Policy and structure checks

- 필수 문서와 heading
- 미완성 표기
- 금지 확장자·경로·크기
- 브랜치·커밋 convention
- workflow event·권한·action pin
- 계약 생성 drift

빠르고 외부 서비스가 없어야 하므로 모든 PR의 첫 관문으로 실행합니다.

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

### Container tests

- 실제 image build
- 비특권 runtime UID
- healthcheck
- Dockerfile build context
- `.env`와 Git metadata 미포함
- Web static cache header
- Web runtime image pin `nginxinc/nginx-unprivileged:1.31.2-alpine3.23`과 `scripts/check_containers.py` exact expectation

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

Phase 1 security tests additionally verify 25 MiB input, 500 pages, 120초 parent wall, 90초 child CPU, 1536 MiB address space, 64 MiB output, 64 open descriptors, mode `0700` work directories, mode `0600` files, 1 MiB streaming SHA-256, `%PDF-` magic, password absence, and symlink rejection.

v0.1 security tests additionally verify family-scoped batch password reuse and disposal, encrypted archive round-trip and tamper detection, selective OCR cleanup, Worker-only OpenAI key injection, external payload allowlist, local session/CSRF/object scope, and gateway-only host exposure.

정적 검사만 수행한 경우 동적 공격 재현을 수행했다고 보고하지 않습니다.

WSL의 측정된 메모리 압력에서는 Vitest worker 시작 timeout을 피하기 위해 Web `test` script가 `vitest run --maxWorkers=1`을 사용합니다. 이는 테스트 범위를 줄이지 않고 worker 동시성만 직렬화하며, Web 검증은 Python·컨테이너 검증과 함께 직렬로 실행합니다.

## Foundation command matrix

| Area | Command | External secret |
|---|---|---|
| Documentation | `python3 scripts/check_documentation.py` | 없음 |
| Repository safety | `python3 scripts/check_repository_safety.py` | 없음 |
| Web | `corepack pnpm@11.22.0 web:check` | 없음 |
| Python style | `uv run ruff format --check ...` | 없음 |
| Python lint | `uv run ruff check ...` | 없음 |
| Python types | `uv run mypy ...` | 없음 |
| Python tests | `uv run pytest ...` | 없음 |
| Contracts | `uv run python scripts/check_contracts.py` | 없음 |
| Migrations | `uv run alembic ... upgrade head` | 합성 로컬 DB |
| Containers | 개별 `docker compose ... build` | 없음 |
| Workflows | `uv run python scripts/check_workflows.py` (Dependabot ignore policy 포함) | 없음 |

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

1. 새 동작은 해당 동작이 없을 때 실패하는 테스트를 먼저 가집니다.
2. 공개 CI는 외부 비밀값과 실제 자료 없이 실행됩니다.
3. PostgreSQL 동작을 SQLite 통과로 대체하지 않습니다.
4. 일부 검사 성공을 전체 완료로 보고하지 않습니다.
5. flaky 재실행 성공을 원인 해결로 간주하지 않습니다.

## Failure behavior

- 예상과 다른 이유로 실패한 테스트는 구현 전에 테스트 환경을 수정합니다.
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

## v0.1 acceptance matrix

- synthetic contract-to-claim E2E without external secrets
- encrypted synthetic batch with one-time password and partial failure
- native extraction plus local Korean/English OCR only for classified pages
- AI structurer/verifier success, disagreement, invalid Evidence and provider failure
- fixed and partial indemnity decision tables
- two-admin login, CSRF, session expiry and device revoke
- Docker Compose migration, restart, DB/archive persistence and job recovery
- Tailscale address and real mobile PWA as separately reported manual checks
- user-approved real PDF review without committing source, extraction or result

## Tests

이 전략 자체는 `scripts/check_documentation.py`의 필수 heading 검사, CI command matrix와 로컬 Make target의 동등성 검사, PR 템플릿의 증거 필드 검사로 검증합니다. 각 기능 설계는 위 계층 중 적용 가능한 테스트를 구체적인 사례와 명령으로 다시 정의합니다.

## Deferred decisions

검색 품질 지표 목표, 실제 자료 acceptance 표본 수, 운영 부하 목표는 각 feature plan에서 합성 baseline을 먼저 정합니다. Google Drive, public deployment와 multi-provider AI acceptance는 v0.1 이후로 남깁니다.

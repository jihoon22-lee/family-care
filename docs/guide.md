# FamilyCare guide

이 문서는 완료된 Foundation·Phase 1, 구현·merge된 Phase 2 core ledger와 candidate review, 그리고 Phase 4 Clause search 개발환경의 경계를 설명합니다. Clause search는 합성 corpus와 인증 연결 전 fail-closed scope 경계까지만 검증되었으며 실제 보험 자료 분석 기능으로 사용할 수 없습니다. 구현·검증에 실제 문서를 연결하지 않습니다.

## Local development

### Prerequisites

- Git
- Node.js 24 LTS
- Corepack과 pnpm 11.22.0
- Python 3.14
- uv 0.12.x
- Docker Engine과 Docker Compose v2

버전을 확인합니다.

```bash
node --version
corepack pnpm@11.22.0 --version
python3 --version
uv --version
docker --version
docker compose version
```

### Setup

```bash
cp .env.example .env
corepack pnpm@11.22.0 install --frozen-lockfile
TMPDIR=/tmp uv sync --all-packages --group dev
```

`.env`는 로컬 전용이며 commit하지 않습니다. `.env.example`의 개발용 placeholder를 운영 비밀값으로 사용하지 않습니다.

Codex/WSL에서 `TEMP`와 `TMP`가 `/mnt/c` 아래 Windows 임시 디렉터리를 가리키면 Python의 anonymous temporary file이 올바르게 동작하지 않을 수 있습니다. 프로젝트의 Python 검증은 `TMPDIR=/tmp`를 사용하며 Make target도 같은 값을 적용합니다.

### Run checks

전체 검증:

```bash
make check
```

WSL 메모리 압력을 피하기 위해 Web, Python, 컨테이너 검사를 직렬로 실행합니다. 실패한 단계만 다시 실행할 때도 다른 대규모 빌드와 동시에 실행하지 않습니다.

### Start services

```bash
make up
```

Foundation 서비스:

- Web: `http://127.0.0.1:8080`
- API liveness: `http://127.0.0.1:8000/health/live`
- API readiness: `http://127.0.0.1:8000/health/ready`
- PostgreSQL: 로컬 개발 포트 5432

Phase 1의 문서 endpoint와 analyzer는 인증이 없는 local synthetic-only 개발 기능입니다. production-safe endpoint가 아닙니다. Phase 7은 외부 provider가 아니라 `docs/design/authentication.md`의 두 로컬 관리자와 server-side session을 추가합니다.

### Phase 2 policy ledger boundary

Migration `0003_policy_ledger`는 `HouseholdSpace`, `FamilyMember`, Evidence, `PolicyContract`, `PolicyParty`, 실제 가입 Rider, 시점별 상태 snapshot을 추가합니다. 가족과 계약 API는 soft delete·휴지통·복원, optimistic version 충돌, household object scope를 적용하며 계약과 당사자·Rider 응답은 검증된 증권 page Evidence만 노출합니다.

Policy route는 항상 등록되지만 클라이언트가 `household_space_id`를 보내 권한을 선택할 수 없습니다. Phase 7 인증이 PostgreSQL session에서 `HouseholdScope`를 제공하기 전까지 기본 resolver는 모든 Policy route를 `401 AUTHENTICATION_REQUIRED`로 닫습니다. 현재는 합성 테스트가 resolver를 주입할 때만 lifecycle을 실행하며, 인증을 우회하는 로컬 household 환경변수나 header는 제공하지 않습니다.

계약 원장 발행에 사용하는 Evidence는 같은 household의 성공 extraction과 실제 policy 문서 버전, 1-based physical page, page 범위 안의 선택 좌표, 일치하는 content SHA-256을 가져야 합니다. `AI_VERIFIED` 또는 `USER_CONFIRMED`만 현재 원장에 발행할 수 있고 `NEEDS_REVIEW`와 terms-only Evidence는 거부됩니다.

### Phase 4 Clause search boundary

Phase 2 candidate review는 main PR #16에 merge되었습니다. Phase 4 Clause search는 `TermsEdition`과 parent-child `Clause` hierarchy, PostgreSQL `simple` full-text search와 `pg_trgm` title relevance, household/date/edition/insurer/product scope, bounded Evidence-backed results를 제공합니다. 검색은 가입 Rider나 지급 가능액을 확정하지 않는 조사 도구이며, Evidence는 항상 1-based PDF physical page를 가리킵니다.

검색 API는 다음과 같습니다.

- `GET /api/v1/terms-editions`
- `GET /api/v1/terms-editions/{id}/clauses`
- `POST /api/v1/clauses/search`

검색어는 no-store JSON POST body로만 전송하고 URL, browser history, 일반 log 또는 Web Storage에 저장하지 않습니다. 모든 route는 server-derived `HouseholdScope`를 사용하며 클라이언트가 household를 선택할 수 없습니다. 기본 scope resolver는 인증 연결 전까지 `401 AUTHENTICATION_REQUIRED`로 fail-closed하므로 합성 테스트에서 resolver를 주입한 경우 외에는 현재 실제 인증된 route로 사용할 수 없습니다. 인증을 우회하는 local household 환경변수나 header는 제공하지 않습니다.

v0.1에는 별도 live search-index rebuild endpoint가 없습니다. 초기 normalization version은 DB constraint로 고정합니다. 향후 version bump가 필요하면 PostgreSQL transaction migration이 old committed state를 transaction commit 전까지 유지하고, commit 시 새 version으로 원자적으로 전환합니다. 앱/DB mismatch나 stale hit는 `SEARCH_INDEX_VERSION_MISMATCH`로 명시적으로 실패하며 silent fallback하지 않습니다.

이 Phase 4 변경은 실제 보험 자료, 외부 AI, Google Drive, 운영 배포를 사용하거나 검증하지 않았습니다. 현재 검증은 처음부터 만든 합성 한국어·영어 corpus와 합성 Evidence만 사용합니다.

### Phase 5 Rider-Clause and CoverageRule review boundary

Phase 5는 검색 결과를 실제 가입 담보와 실행 가능한 규칙으로 연결하기 전의 검토 경계를 제공합니다. UI 경로는 `/app/clauses/review`이며 두 개의 대기열을 분리합니다.

- **담보와 약관 연결**: 실제 가입이 Evidence로 확인된 Rider와 계약일에 적용되는 TermsEdition의 Clause를 검토합니다. 약관에만 존재하는 Rider, 잘못된 판본, 다른 문서의 Clause, 누락·충돌 Evidence는 `NEEDS_REVIEW`로 남습니다. 확인·제외는 현재 link version을 요구합니다.
- **보장 규칙**: 저장된 CoverageRule candidate의 reason code, rule kind, 필요한 입력 필드와 Evidence를 확인합니다. data-only allowlist에 맞지 않는 DSL은 설명만 표시되고 게시할 수 없습니다.

API 계약은 다음과 같습니다.

```text
GET   /api/v1/review-items?domain=rider_clause|coverage_rule&status=NEEDS_REVIEW
PATCH /api/v1/review-items/{id}/fields/{field_id}
GET   /api/v1/riders/{id}/clause-links
POST  /api/v1/rider-clause-links/{id}/confirm|reject
GET   /api/v1/coverage-rules/{id}/versions
POST  /api/v1/coverage-rules/{id}/publish
```

Rule version GET은 `expected_version`을 반환합니다. publish 요청은 새 DSL 본문이나 household ID를 받지 않고 `expected_version`과 이미 저장된 `version_id`만 받습니다. typed correction은 후보 원본을 수정하지 않고 child version을 만들며, optimistic conflict가 발생하면 현재 입력을 버리지 않고 화면에 유지합니다. `AI_VERIFIED`·`USER_CONFIRMED`와 exact Evidence를 만족한 stored version만 executable로 게시할 수 있습니다. 게시되었다고 해서 아직 `MATCH`, `NO_MATCH`, `UNKNOWN`을 계산하거나 보험금 지급을 확정하는 것은 아닙니다.

Evidence drawer는 bounded excerpt와 1-based physical page만 보여줍니다. 화면에는 raw DSL textarea, 문서 전체 text, provider payload, private path를 표시하지 않으며 query/cache는 no-store와 memory-only 경계를 유지합니다. 합성 Web 시나리오는 320px viewport에서도 연결·규칙 dialog focus, Evidence disclosure, stored-version publish body, browser storage 미사용을 확인합니다. 이 단계의 검증은 합성 데이터에 한정되며 실제 보험 자료·실제 기기·Tailscale 환경을 검증하지 않습니다.

### Planned v0.1 local runtime

v0.1은 개인 WSL의 Docker Compose에서 Web gateway 하나만 host에 노출하고 API, Worker, PostgreSQL은 internal network에 둡니다. 부부 기기는 Tailscale private access와 app login을 함께 사용합니다. 이 문서는 인증 연결과 운영 migration 적용을 완료로 표시하지 않으며, 실제 운영 사용 절차는 별도 승인과 acceptance 뒤에 추가합니다.

- existing WSL `OPENAI_API_KEY`는 Worker container에만 주입합니다.
- Gemini와 Google Drive API는 사용하지 않습니다.
- encrypted PDF password는 family-scoped batch process memory에서만 사용합니다.
- managed archive key는 저장소 밖 file secret으로 제공합니다.
- LUKS, BitLocker와 WSL swap은 변경하지 않습니다.

### Use the local synthetic document-analysis API

문서 route는 기본적으로 꺼져 있습니다. 저장소 밖의 처음부터 만든 합성 PDF와 별도 개발용 PostgreSQL을 사용할 때만 로컬 `.env`에서 다음 두 변수를 함께 opt-in하고 API를 재시작합니다.

```dotenv
FAMILYCARE_ENV=development
FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true
```

두 변수 중 하나라도 다르거나 없으면 router가 등록되지 않아 `POST /api/v1/documents/analysis`와 `GET /api/v1/analysis-jobs/{job_id}`가 모두 `404`입니다. 이 gate는 local synthetic-only 개발용이며 authentication·authorization이 없고 production-safe endpoint가 아닙니다. `/health/live`와 `/health/ready`는 gate와 무관하게 유지됩니다.

유효한 요청은 source key와 canonical extraction 설정만 보내고, 응답의 `status_url`을 polling합니다.

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/documents/analysis \
  -H 'content-type: application/json' \
  --data '{"schema_version":"1","source_key":"synthetic/policy-001.pdf","document_kind":"policy","extractor_config":{"profile":"quality-v1","quality_rule_version":"quality-v1","table_strategy":"auto"}}'

curl -i http://127.0.0.1:8000/api/v1/analysis-jobs/00000000-0000-4000-8000-000000000001
```

성공적인 POST는 파일을 열지 않고 항상 `202 Accepted`로 job UUID, queued state, 상대 `status_url`을 반환합니다. Worker가 `POST → Worker → GET` 순서로 intake·isolated extraction·persistence를 수행한 뒤 status GET은 `succeeded`와 sanitized extraction summary를 보여줍니다. 파일이 없거나 손상되었거나 암호화되어도 POST는 동기 오류로 바뀌지 않으며, Worker 결과에서 encrypted input은 `PASSWORD_REQUIRED`가 됩니다. 요청 body가 잘못되거나 absolute/parent-traversal source key, `password`, `absolute_path`, `raw_pdf`, `url` 같은 추가 필드를 보내면 HTTP `422`와 `error_code: "INVALID_REQUEST"`가 반환됩니다. 알 수 없는 job UUID는 `404`와 `ANALYSIS_JOB_NOT_FOUND`입니다. 오류 응답은 raw value, password, absolute path, document body를 echo하지 않습니다.

API는 `FAMILYCARE_DOCUMENT_ROOT`를 직접 열지 않습니다. 실제 Worker 실행은 아래의 합성 전용 Analyzer 절차와 migration `0002_document_ingestion`을 사용하며, 문서·work root에는 실제 자료를 넣지 않습니다.

종료:

```bash
make down
```

기본 종료는 데이터베이스 볼륨을 삭제하지 않습니다. 볼륨 삭제는 복구가 어려울 수 있으므로 이 가이드가 자동 명령으로 제공하지 않습니다.

## Repository workflow

브랜치 예:

```bash
git switch -c feat/policy-ledger
```

커밋 예:

```bash
git commit -m "feat(policies): add rider review state"
```

전체 규칙은 `AGENTS.md`의 Branch and commit conventions를 따릅니다. CI는 PR 브랜치명과 커밋 제목을 검사합니다.

### Protect main ruleset

GitHub의 활성 `Protect main` ruleset은 PR을 요구하고 다음 exact display-name checks를 strict required checks로 적용합니다: `Repository safety`, `Web`, `Python`, `PostgreSQL integration`, `Container (web)`, `Container (api)`, `Container (worker)`. Branch deletion과 force-push는 차단되고, merge는 merge commit만 허용되며, review threads는 모두 resolved 상태여야 합니다. bypass actor는 구성하지 않습니다. Required approving review count는 `0`이며, 자동 검증과 review-thread 해결 조건은 그대로 적용됩니다. 현재 ruleset 상태는 GitHub에서 확인합니다.

## Safe data handling

### Public development

기본 개발과 CI는 `fixtures/synthetic/`만 사용합니다. 실제 보험 폴더나 Drive를 마운트하지 않습니다.

합성 fixture는 다음 조건을 모두 만족해야 합니다.

- 처음부터 가상으로 작성
- 실제 상품명·문장·금액·표를 복사하지 않음
- `Family Member A`, `Sample Policy` 등 명백한 가상 식별자 사용
- fixture README에 합성 출처 명시

### External local paths

Phase 1 구현과 CI에서는 실제 자료 또는 private external root를 열지 않습니다. 테스트는 처음부터 만든 합성 PDF를 checkout 밖 임시 디렉터리에 복사해 `FAMILYCARE_DOCUMENT_ROOT`로 지정합니다. 실제 자료 사용 단계가 별도 승인된 뒤에만 저장소 밖 경로를 지정할 수 있습니다.

```dotenv
FAMILYCARE_DOCUMENT_ROOT=/absolute/path/outside/repository
FAMILYCARE_WORK_ROOT=/absolute/path/outside/repository/work
```

위 문자열은 경로 형식 예시이며 실제 경로가 아닙니다. 실제 값을 문서, 이슈, PR, 로그에 복사하지 않습니다.

현재 Compose에는 문서 경로 mount가 없습니다. 실제 자료 연결은 Phase 8 구현과 사용자가 지정한 source path의 별도 acceptance가 완료된 뒤에만 사용합니다.

Phase 1 safety contract는 25 MiB input, 500 pages, 120-second parent wall timeout, 90-second child CPU, 1536 MiB address space, 64 MiB output file, 64 open descriptors입니다. Work directory는 `0700`, file은 `0600`이며 SHA-256은 1 MiB chunk로 계산합니다. 요청과 job은 relative `source_key`만 사용하고, resolved regular file은 root 아래에 있어야 하며 symlink traversal과 `%PDF-` magic 불일치를 거부합니다.

문서 parser child에는 부모가 no-follow 방식으로 연 read-only source descriptor와 canonical JSON settings만 전달하며 network client나 external URL resolution을 제공하지 않습니다. OS egress enforcement와 실제 private-data acceptance는 approved runtime boundary가 마련될 때까지 수행하지 않습니다.

### Run the synthetic Analyzer Worker locally

Analyzer queue 실행은 migration `0002_document_ingestion`까지 적용된 합성 개발용 PostgreSQL과 저장소 밖의 absolute document/work root가 모두 있을 때만 활성화됩니다. 세 환경변수 중 하나라도 없으면 Worker는 Foundation idle mode를 유지합니다. 기본 Compose에는 document root mount가 없으므로 이 절차는 direct local/test 실행 전용입니다.

```bash
FAMILYCARE_DATABASE_URL=postgresql+psycopg://familycare:development-only@127.0.0.1:5432/familycare \
FAMILYCARE_DOCUMENT_ROOT=/absolute/synthetic/document/root \
FAMILYCARE_WORK_ROOT=/absolute/synthetic/work/root \
TMPDIR=/tmp uv run familycare-worker
```

위 값은 형식 예시일 뿐 실제 credential이나 경로가 아닙니다. 두 root는 미리 생성된 directory여야 하고 이 단계에서는 처음부터 만든 합성 PDF만 넣습니다. Worker는 한 번에 job 하나만 실행하며 기본 180초 lease를 30초마다 갱신합니다. SIGTERM/SIGINT를 받으면 현재 parser child를 회수한 뒤 다음 job을 시작하지 않습니다. readiness는 DB 연결뿐 아니라 `public.analysis_jobs` table 존재까지 확인합니다.

### Before every commit

```bash
python3 scripts/check_repository_safety.py
git diff --check
git status --short
```

실제 데이터가 의심되면 commit과 push를 중단하고 `SECURITY.md`를 따릅니다.

## Database migrations

로컬 PostgreSQL이 실행 중이고 `.env`의 연결정보가 설정된 경우:

```bash
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini current
```

마이그레이션은 코드와 함께 검토합니다. 운영 데이터베이스 마이그레이션 절차는 Production Deployment 단계에서 별도 결정합니다.

## Contracts

OpenAPI와 작업 JSON Schema가 구현과 일치하는지 확인합니다.

```bash
TMPDIR=/tmp uv run python scripts/check_contracts.py
```

계약을 깨는 변경은 새 버전, CHANGELOG, 소비자 마이그레이션 계획이 필요합니다.

## Container builds

로컬 자원을 먼저 확인합니다.

```bash
free -h
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
```

다른 세션의 컨테이너를 중지하지 않습니다. 이미지는 한 번에 하나씩 빌드합니다.

```bash
docker compose --env-file .env -f infra/compose/compose.yaml build web
docker compose --env-file .env -f infra/compose/compose.yaml build api
docker compose --env-file .env -f infra/compose/compose.yaml build worker
```

## Releases

Foundation 릴리스 workflow는 `vMAJOR.MINOR.PATCH` 태그에서 다음 이미지를 GHCR에 게시합니다.

- `family-care-web`
- `family-care-api`
- `family-care-worker`

태그는 되돌리기 어려운 공개 릴리스 메타데이터입니다. 사용자 요청 없이 만들거나 push하지 않습니다. GHCR 게시 성공은 Cloud Run 또는 다른 운영 환경의 배포 성공을 의미하지 않습니다.

릴리스 workflow는 정확한 `vMAJOR.MINOR.PATCH`만 허용하며, 게시 전 문서·민감정보 경계·계약·Web·Python·PostgreSQL·세 컨테이너 빌드를 다시 검증합니다. 성공 시 각 이미지에는 버전 태그와 12자리 commit SHA 태그가 생성되며 `latest`는 생성하지 않습니다.

현재 CD 범위는 GHCR 게시까지입니다. Cloud Run 설정, 운영 비밀값, 운영 데이터베이스 마이그레이션, 실제 트래픽 전환은 포함하지 않습니다.

## Troubleshooting

- 도구 버전이 다르면 `.node-version`, `.python-version`, root package manager 설정을 우선합니다.
- lockfile이 바뀌면 의존성 선언 변경과 함께 검토합니다.
- 포트가 사용 중이면 점유 프로세스를 먼저 확인하고, 다른 세션 소유 프로세스를 임의 종료하지 않습니다.
- Docker 빌드가 메모리 부족으로 실패하면 동시 작업을 줄이고 swap·가용 메모리를 기록합니다.
- 실제 플랫폼에서 확인하지 못한 PWA 설치나 브라우저 동작은 로컬 단위 테스트 성공으로 대체하지 않습니다.

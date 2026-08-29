# FamilyCare guide

이 문서는 완료된 Foundation·Phase 1부터 정책 원장, 약관 검색·규칙 검토, 결정론적 판정·조건부 계산, Event/Result PWA와 수동 Claim workflow, 그리고 구현된 encrypted document batch·selective local OCR 계약까지 현재 개발환경의 경계를 설명합니다. 업무 API는 로컬 인증 session이 없으면 fail-closed이며, 합성 자료로 검증된 기능을 실제 보험 자료 분석 기능으로 과장하지 않습니다. 구현·검증에 실제 문서를 연결하지 않습니다.

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
cd /home/jihoon/projects/family-care
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

Compose 실행에는 저장소 밖 import root와 master-key file이 필요합니다. 아래의 **Private local Docker runtime** 절차에서 `.env.private`, migration, 미사용 Web port를 먼저 준비한 뒤 `ENV_FILE=.env.private make up`을 실행합니다.

host에는 `http://127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}` Web gateway만 열립니다. API의 8000과 PostgreSQL의 5432는 Compose network 내부 port이며 host에 publish하지 않습니다. 업무 API는 같은 Web origin의 `/api/`를 통해서만 사용합니다.

Phase 1의 문서 endpoint와 analyzer는 인증이 없는 local synthetic-only 개발 기능입니다. production-safe endpoint가 아닙니다. 업무 API와 Web PWA는 외부 provider가 아니라 `docs/design/authentication.md`의 두 로컬 관리자와 server-side session으로 보호됩니다.

### Phase 2 policy ledger boundary

Migration `0003_policy_ledger`는 `HouseholdSpace`, `FamilyMember`, Evidence, `PolicyContract`, `PolicyParty`, 실제 가입 Rider, 시점별 상태 snapshot을 추가합니다. 가족과 계약 API는 soft delete·휴지통·복원, optimistic version 충돌, household object scope를 적용하며 계약과 당사자·Rider 응답은 검증된 증권 page Evidence만 노출합니다.

Policy route는 항상 등록되지만 클라이언트가 `household_space_id`를 보내 권한을 선택할 수 없습니다. 활성 PostgreSQL session에서 파생한 `HouseholdScope`가 없으면 기본 resolver는 모든 Policy route를 `401 AUTHENTICATION_REQUIRED`로 닫습니다. 인증을 우회하는 로컬 household 환경변수나 header는 제공하지 않습니다.

계약 원장 발행에 사용하는 Evidence는 같은 household의 성공 extraction과 실제 policy 문서 버전, 1-based physical page, page 범위 안의 선택 좌표, 일치하는 content SHA-256을 가져야 합니다. `AI_VERIFIED` 또는 `USER_CONFIRMED`만 현재 원장에 발행할 수 있고 `NEEDS_REVIEW`와 terms-only Evidence는 거부됩니다.

### Phase 4 Clause search boundary

Phase 2 candidate review는 main PR #16에 merge되었습니다. Phase 4 Clause search는 `TermsEdition`과 parent-child `Clause` hierarchy, PostgreSQL `simple` full-text search와 `pg_trgm` title relevance, household/date/edition/insurer/product scope, bounded Evidence-backed results를 제공합니다. 검색은 가입 Rider나 지급 가능액을 확정하지 않는 조사 도구이며, Evidence는 항상 1-based PDF physical page를 가리킵니다.

검색 API는 다음과 같습니다.

- `GET /api/v1/terms-editions`
- `GET /api/v1/terms-editions/{id}/clauses`
- `POST /api/v1/clauses/search`

검색어는 no-store JSON POST body로만 전송하고 URL, browser history, 일반 log 또는 Web Storage에 저장하지 않습니다. 모든 route는 server-derived `HouseholdScope`를 사용하며 클라이언트가 household를 선택할 수 없습니다. 활성 local session이 없으면 기본 scope resolver가 `401 AUTHENTICATION_REQUIRED`로 fail-closed합니다. 인증을 우회하는 local household 환경변수나 header는 제공하지 않습니다.

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

Evidence drawer는 bounded excerpt와 1-based physical page만 보여줍니다. 화면에는 raw DSL textarea, 문서 전체 text, provider payload, private path를 표시하지 않으며 query/cache는 no-store와 memory-only 경계를 유지합니다. 합성 Web 시나리오는 320px viewport에서도 연결·규칙 dialog focus, Evidence disclosure, stored-version publish body, browser storage 미사용을 확인합니다. 이 단계 자체의 검증은 합성 데이터에 한정되며 실제 보험 자료·실제 기기는 검증하지 않습니다. 별도 private-runtime acceptance에서 Tailscale HTTPS와 인증 브라우저 흐름을 확인합니다.

### Phase 6 coverage decision engine boundary

Phase 6는 구조화된 사건 입력을 실제 가입 Rider와 게시된 executable CoverageRule에 연결해 `MATCH`, `NO_MATCH`, `UNKNOWN` 후보를 만드는 결정론적 API 경계입니다. 약관에만 존재하는 담보는 후보가 되지 않으며, 정보 부족·충돌·계약 상태 미확인·stale Evidence·history 미연결은 `UNKNOWN`으로 남습니다. 이 단계는 보험금 지급이나 금액을 확정하지 않습니다.

현재 MedicalEvent API는 다음 lifecycle을 제공합니다.

```text
POST   /api/v1/medical-events
GET    /api/v1/medical-events/{id}
PATCH  /api/v1/medical-events/{id}
DELETE /api/v1/medical-events/{id}
GET    /api/v1/medical-events/trash
POST   /api/v1/medical-events/{id}/restore
POST   /api/v1/medical-events/{id}/structure
GET    /api/v1/medical-event-structuring-jobs/{job_id}
POST   /api/v1/medical-events/{id}/analyze
GET    /api/v1/medical-events/{id}/results/{version}
GET    /api/v1/evidence/{evidence_id}
```

생성·수정 body에는 `family_member_id`, `pre_visit` 또는 `post_treatment` mode, 사건/방문일, 그리고 제한된 구조화 fact만 넣습니다. fact는 `value`와 `confirmation`(`user`, `ai_structured`, `unconfirmed`, `conflicting`)으로 구성합니다. 클라이언트가 `household_space_id`, Evidence ID, tri-state, 후보, 금액, 지급 보장 문구를 입력할 수 없도록 strict schema가 적용됩니다. 수정·삭제·복원은 현재 `expected_version`을 요구하며 stale version은 `409` 충돌로 처리됩니다.

인증된 local session에서 동일 API에 보낼 최소 요청 모양은 다음과 같습니다. 아래 UUID와 값은 저장소 안에서만 쓰는 합성 예시이며, 실제 요청에는 로그인으로 발급된 host-only cookie와 state-changing 요청용 CSRF header가 필요합니다.

```bash
curl -i -X POST http://127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}/api/v1/medical-events \
  -H 'content-type: application/json' \
  --data '{"family_member_id":"00000000-0000-4000-8000-000000000101","mode":"pre_visit","situation":"진료 전에 확인할 합성 상황입니다.","event_date":"2026-08-25","facts":{"MedicalEvent.classification":{"value":"injury","confirmation":"user"}}}'

curl -i -X POST http://127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}/api/v1/medical-events/00000000-0000-4000-8000-000000000102/analyze

curl -i http://127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}/api/v1/medical-events/00000000-0000-4000-8000-000000000102/results/1
```

분석은 repeatable-read transaction 안에서 하나의 run, RuleEvaluation, Evidence join/snapshot, Rider candidate를 원자적으로 저장합니다. 결과에는 run·event·engine·rule-set version, 후보별 tri-state, 부족/충돌 field, reason code, bounded Evidence가 포함되며 모든 decision response는 `Cache-Control: no-store`입니다. Evidence snapshot에는 당시 문서/추출 ID, 페이지, 좌표, review state, content hash가 있어 나중에 Evidence 원본 행이 바뀌어도 이미 저장된 결과의 근거가 조용히 바뀌지 않습니다. 이후 새 분석에서는 현재 Evidence를 다시 검증하므로 stale이면 `UNKNOWN`이 됩니다.

기본 API scope resolver는 활성 local session이 없으면 fail-closed이므로 일반적인 미인증 요청은 `401 AUTHENTICATION_REQUIRED`가 됩니다. 이 Phase의 API·PostgreSQL 검증은 처음부터 만든 합성 데이터와 합성 household scope만 사용합니다. ClaimHistory projection은 paid/partially_paid의 counted occurrence와 denied의 audit-only 이력을 연결하며, history가 누락·충돌하면 임의로 0회로 계산하지 않고 `UNKNOWN`으로 반환합니다.

### Benefit calculation boundary

정액형 계산은 검증된 규칙의 금액·횟수 조건만 Decimal로 평가합니다. 실손형 계산은 사용자가 직접 입력한 영수증 항목의 `covered`, `possibly_excluded`, `unknown` 구분과 자기부담 조건을 사용해 확인액·추가 확인액·제외액을 분리합니다. 영수증 이미지 업로드와 브라우저 금액 계산은 제공하지 않으며, 통화 불일치·복수 실손 배분·필요 조건 누락은 금액을 억지로 확정하지 않고 `UNKNOWN` 또는 partial 결과로 남깁니다.

관련 API는 수기 영수증 항목 CRUD와 버전별 계산 조회를 제공합니다. 모든 금액은 decimal string이며 응답은 `Cache-Control: no-store`입니다.

```text
POST   /api/v1/medical-events/{id}/receipt-lines
PATCH  /api/v1/medical-events/{id}/receipt-lines/{line_id}
DELETE /api/v1/medical-events/{id}/receipt-lines/{line_id}
GET    /api/v1/medical-events/{id}/calculations
```

### Event and result PWA boundary

Web은 `/app/events/new`와 `/app/events/{event_id}`에서 병원 방문 전 짧은 상황 입력과 치료 후 수기 영수증 항목을 같은 흐름으로 제공합니다. 자연어 구조화는 선택 단계이며 실패해도 사용자가 사실을 직접 수정하고 결정론적 분석을 계속할 수 있습니다. 추가 질문을 답하지 않아도 현재 정보로 분석할 수 있고, 부족한 정보는 `UNKNOWN`으로 표시됩니다.

버전별 결과 경로 `/app/events/{event_id}/result/{version}`은 현재 사건, 지금 할 일, 청구 검토, 추가 확인 필요, 조건 불일치 순서를 고정합니다. 서버가 계산한 decimal string만 보여주며 브라우저에서 보험금 산술을 다시 수행하지 않습니다. Evidence dialog는 bounded 문서 label·physical page·Clause·excerpt만 표시하고, 키보드 focus trap·Escape 닫기·호출 버튼 focus 복원을 제공합니다.

Web query cache는 메모리에만 있고 API 요청은 `credentials: include`와 `cache: no-store`를 사용합니다. service worker는 hashed app shell만 precache하며 API, 사건, 결과, Evidence, 청구 데이터는 runtime cache나 Web Storage·IndexedDB에 저장하지 않습니다. 이 흐름 자체는 합성 데이터와 Chromium 320px viewport로 검증했으며 Windows 실제 브라우저, 모바일 PWA 설치, 실제 보험 자료는 아직 검증하지 않습니다. private-runtime acceptance에서는 Tailscale HTTPS와 인증 브라우저 login/navigation/logout을 별도로 확인했습니다.

### Claim workflow boundary

결과 카드의 **청구 검토 시작**은 선택한 Rider 하나를 서버에 전달해 ClaimCase를 만듭니다. 요청 body에는 `rider_id`만 있고 policy ID, insurer, household scope를 넣지 않습니다. 서버가 `rider_id`와 현재 HouseholdScope를 검증해 PolicyContract와 insurer를 파생하고, 그 시점의 Candidate·Rule·Policy·Evidence와 해당 후보에 연결된 모든 계산 metadata snapshot을 `preparing` ClaimCase와 함께 보존합니다.

```text
POST /api/v1/medical-events/{event_id}/claims
{"rider_id":"00000000-0000-4000-8000-000000000701"}
```

현재 ClaimCase API는 다음과 같습니다.

```text
POST   /api/v1/medical-events/{event_id}/claims
GET    /api/v1/claims?event_id={event_id}&status={status}&cursor={cursor}&limit={limit}
GET    /api/v1/claims/trash
GET    /api/v1/claims/{claim_id}
PATCH  /api/v1/claims/{claim_id}
POST   /api/v1/claims/{claim_id}/transitions
PATCH  /api/v1/claims/{claim_id}/checklist/{item_id}
DELETE /api/v1/claims/{claim_id}
POST   /api/v1/claims/{claim_id}/restore
```

상태는 `preparing → submitted → supplementation_requested`와 `paid`, `partially_paid`, `denied` 결과 전이, 이후 `closed`로 제한됩니다. 상태는 직접 PATCH하지 않고 expected version을 포함한 transition으로만 변경합니다. `paid`와 `partially_paid`는 amount·currency·payment date를 받아 ClaimHistory의 `counted_occurrence`로 기록하고, `denied`는 audit-only로 남겨 미래 `NO_MATCH`로 바꾸지 않습니다. 삭제는 soft delete이며 일반 목록에서는 숨기고 trash에서 expected-version restore를 수행합니다.

Checklist는 `document_kind`, requirement/prepared 상태, bounded `note_code`, source rule/Evidence ID만 기록하는 metadata-only 목록입니다. 파일 업로드, 문서 경로, OCR·원문, 외부 파일 ID는 제공하지 않습니다. `submitted`도 사용자가 보험사 channel에서 접수했다고 수동 기록하는 상태일 뿐 FamilyCare가 보험사 API·email·fax로 제출하는 기능은 없습니다. Claim API 응답은 `no-store`이며 청구 금액·사유·receipt metadata를 Web Storage나 IndexedDB에 보존하지 않습니다.

이 경계의 테스트와 예시는 처음부터 만든 합성 값만 사용합니다. 실제 보험 문서·개인정보, 보험사 제출, Windows·모바일 기기와 다른 실제 기기의 접근은 아직 검증하지 않았습니다. private-runtime에서는 Tailscale HTTPS를 통한 인증 브라우저 흐름을 확인했습니다.

### Private local Docker runtime

v0.1 Compose는 Web gateway 하나만 loopback에 publish하고 API, Worker, PostgreSQL은 internal network에 둡니다. API와 Worker는 저장소 밖 `FAMILYCARE_IMPORT_ROOT`를 동일한 read-only bind로 사용합니다. application-encrypted archive와 임시 work area는 Worker 전용 named volume이고, API는 고정 GID `10003`의 Unix socket volume만 공유합니다. master key와 `OPENAI_API_KEY`도 Worker만 받습니다.

#### Configure the private environment

```bash
cp .env.example .env.private
chmod 0600 .env.private
```

`.env.private`에는 실제 값을 공개 문서나 shell history에 복사하지 말고 로컬 editor로 설정합니다.

- `FAMILYCARE_IMPORT_ROOT`: 저장소 밖 absolute directory. API/Worker 모두 read-only이며 원본을 삭제하지 않습니다.
- `FAMILYCARE_ARCHIVE_MASTER_KEY_FILE`: 저장소 밖 absolute regular file. 정확히 32 bytes, mode `0600`, numeric owner UID `10002`여야 합니다.
- `FAMILYCARE_WEB_PORT`: 다른 프로젝트와 충돌하지 않는 미사용 loopback port.
- database password: 개발 placeholder 대신 이 runtime 전용 값.

기존 WSL shell의 `OPENAI_API_KEY`는 값을 출력하거나 파일로 복사하지 않고 그대로 Compose interpolation에 재사용합니다. shell 환경값은 `.env.private`의 합성 placeholder보다 우선합니다.

```bash
test -n "${OPENAI_API_KEY:-}"
docker compose --env-file .env.private -f infra/compose/compose.yaml config --quiet
```

master-key metadata를 읽기 전용으로 확인했을 때 numeric owner, mode, size는 `10002:10002 600 32`여야 합니다. 이 조건을 맞추기 위해 필요한 경우 사용자가 정확히 선택한 key file 하나에만 `chown 10002:10002`와 `chmod 0600`을 적용합니다. key의 recovery copy는 Git·DB·container image와 분리해 보관하며, 실행 중인 앱이 recovery 저장소를 자동으로 읽거나 동기화하지 않습니다.

처음 시작하거나 migration이 추가된 뒤에는 DB를 먼저 띄우고 migration을 적용합니다.

```bash
docker compose --env-file .env.private -f infra/compose/compose.yaml up -d --wait db
docker compose --env-file .env.private -f infra/compose/compose.yaml run --rm api \
  alembic -c apps/api/alembic.ini upgrade head
docker compose --env-file .env.private -f infra/compose/compose.yaml run --rm api \
  familycare-admin init \
  --space-key primary-household \
  --household-name "FamilyCare Home" \
  --username admin-a \
  --display-name "Admin A"
docker compose --env-file .env.private -f infra/compose/compose.yaml up -d --wait
```

`init`은 fresh database에서 한 번만 실행하며 TTY에서 첫 관리자 password와 확인을 묻습니다. sole HouseholdSpace와 첫 관리자 insert는 한 transaction이고, 기존 또는 soft-deleted HouseholdSpace가 하나라도 있으면 `HOUSEHOLD_ALREADY_INITIALIZED`로 종료합니다. migration을 다시 실행하거나 Compose를 재시작할 때 `init`을 반복하지 않습니다.

정상 상태에서는 네 서비스가 healthy이고 host publisher는 Web 하나뿐입니다. 외부 liveness는 Web 자체의 `/healthz`이고 API의 `/health/live`·`/health/ready`는 internal network에만 있습니다. Web gateway는 원래 Host와 port를 보존하고 Tailscale HTTPS proxy의 forwarded scheme만 제한적으로 전달하며, host에 publish되지 않은 API가 이 내부 proxy header를 해석합니다. Worker readiness는 DB와 import/archive/work, master-key mode, secret socket을 함께 검사하므로 key 또는 mount가 없으면 fail closed합니다. `FAMILYCARE_DOCUMENT_ROOT`는 별도 Phase 1 합성 개발 route 전용이며 private batch 입력으로 사용하지 않습니다.

#### Read-only Tailscale inspection

다음 inspector는 정확히 세 가지 read-only 명령만 허용하고 원본 stdout, node name, IP, tailnet 식별자를 보고서에 남기지 않습니다.

```bash
TMPDIR=/tmp uv run python scripts/private_acceptance.py \
  tailscale status --json --peers=false
TMPDIR=/tmp uv run python scripts/private_acceptance.py tailscale ip -1
TMPDIR=/tmp uv run python scripts/private_acceptance.py \
  --expected-gateway-port 18080 tailscale serve status --json
```

`status --json --peers=false`는 연결 판정에 불필요한 peer 목록을 요청하지 않아 실제 tailnet 크기와 관계없이 bounded output을 유지합니다. full-peer JSON form은 exact allowlist에서 거부합니다. 예시 `18080`은 실제 `.env.private`의 `FAMILYCARE_WEB_PORT`와 같은 숫자로 바꿉니다. `serve status --json`은 `tailscale-serve-empty`, `tailscale-serve-configured`, `tailscale-serve-gateway-match` 중 하나만 출력하고 node·IP·tailnet·raw JSON·target port는 출력하지 않습니다. 비교용 foreign-configuration fingerprint도 process memory에만 두고 `repr`와 CLI 출력에서 제외하므로, 추가 mapping 전후에 FamilyCare target을 뺀 기존 구성이 동일한지 확인할 수 있습니다. 연결 안 됨, timeout, malformed output, command failure는 non-zero로 종료합니다.

`serve`, `funnel`, `up`, `down`, `set`, `logout`과 추가 인자는 실행 전에 거부됩니다. inspector는 Serve 구성을 변경하지 않습니다. 실제 device access는 기존 mapping을 우선 재사용하고, 없을 때만 현재 상태와 공식 CLI 문서를 별도로 확인한 뒤 충돌 없는 FamilyCare HTTPS endpoint 하나를 추가합니다. Funnel은 사용하지 않습니다. Tailscale 연결은 앱 인증을 대체하지 않으며, remote browser는 HTTPS에서 FamilyCare login과 Secure/HttpOnly session cookie, CSRF 검사를 모두 통과해야 합니다.

#### Stop, backup, and restore ownership

```bash
docker compose --env-file .env.private -f infra/compose/compose.yaml down
```

기본 종료는 named volume을 삭제하지 않습니다. 일관된 backup 단위는 PostgreSQL volume, encrypted archive volume, 그리고 동일한 master key recovery copy입니다. Worker work와 secret-socket volume은 임시 상태이므로 backup 대상이 아닙니다. restore는 원본과 동일한 key를 먼저 복구하고 DB와 archive를 한 세트로 검증한 뒤 서비스를 시작해야 합니다. 이 가이드는 `down --volumes`, archive 삭제, key rotation을 자동 명령으로 제공하지 않습니다.

### Local authentication

업무 API와 Web PWA는 하나의 `HouseholdSpace`에 연결된 동일 권한의 로컬 관리자 계정으로 보호됩니다. v0.1은 활성 관리자를 최대 두 개까지 지원하며 초기에는 한 계정만 생성해 사용할 수 있습니다. 두 번째 계정을 추가해도 역할 차등 없이 같은 가족 원장과 계약을 관리하며, 세 번째 활성 계정 생성은 `ADMIN_LIMIT_REACHED`로 거부됩니다.

관리자 수명주기는 API container의 `familycare-admin` 명령으로만 관리합니다. 위의 `init`이 첫 HouseholdSpace와 첫 관리자를 만들며, 아래 `create`는 선택적 두 번째 관리자만 추가합니다. 계정명과 표시명은 합성 예시이며, 각 명령은 독립적으로 실행합니다.

```bash
docker compose --env-file .env.private -f infra/compose/compose.yaml run --rm api familycare-admin create \
  --username admin-b \
  --display-name "Admin B"

docker compose --env-file .env.private -f infra/compose/compose.yaml run --rm api \
  familycare-admin set-password --username admin-a

docker compose --env-file .env.private -f infra/compose/compose.yaml run --rm api \
  familycare-admin disable --username admin-b
```

`admin-b` 생성은 두 번째 로그인 수단이 필요할 때만 수행합니다.

`init`, `create`, `set-password`는 TTY에서 비밀번호와 확인을 두 번 묻고, 비대화형 실행에서는 stdin의 두 줄을 읽습니다. 비밀번호는 명령 옵션·argv·환경변수·shell history·로그에 넣지 않습니다. CLI에는 `--password` 옵션이 없으며, 위 예시에도 비밀번호 값을 적지 않았습니다. `set-password`는 해당 관리자의 기존 session을 폐기하고, `disable`은 계정과 session만 비활성화하며 가족·계약·청구 기록을 삭제하지 않습니다. 복구용 자격 증명은 사용자가 관리하는 외부 password vault에 보관할 수 있지만 앱이 자동 동기화하지 않습니다.

Web 로그인 성공 시 원본 session token은 `familycare_session` host-only cookie로만 전달됩니다. cookie는 `Secure`, `HttpOnly`, `SameSite=Strict`이며 `Domain`을 지정하지 않습니다. 서버는 PostgreSQL에 token hash와 최소 device label·lifecycle 시각만 저장하고 원본 token과 raw password를 저장하지 않습니다. session은 마지막 활동 후 7일 또는 생성 후 30일 중 먼저 도달하면 만료됩니다.

인증 API는 다음의 좁은 surface를 제공합니다.

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
GET  /api/v1/auth/csrf
POST /api/v1/auth/reauthenticate
POST /api/v1/auth/password
GET  /api/v1/auth/sessions
POST /api/v1/auth/sessions/{session_id}/revoke
```

모든 state-changing 요청은 same-origin과 session별 CSRF token을 함께 검사합니다. Web의 계정 화면에서는 현재 기기와 다른 기기의 session을 목록으로 보고 폐기할 수 있으며, 다른 기기 session 폐기와 비밀번호 변경은 최근 재인증을 요구합니다. 비밀번호 변경은 해당 사용자의 모든 session을 폐기하고, logout·만료·비활성화도 session을 폐기합니다. signup, email reset, invite endpoint는 제공하지 않습니다.

Tailscale은 private network 접근 경로일 뿐 app login을 대체하지 않습니다. Tailscale에 연결된 기기도 FamilyCare 로그인과 유효한 session cookie, state-changing 요청의 CSRF 검사를 통과해야 합니다.

이 인증 경계는 합성 계정·합성 PostgreSQL·합성 Web 테스트와 private-runtime의 Tailscale HTTPS 브라우저 흐름으로 확인했습니다. Windows 실제 브라우저, 실제 모바일 PWA, 다른 실제 기기, 실제 private document acceptance는 아직 검증하지 않았습니다. PR #27과 #28의 merge 및 CI/post-merge 검증은 완료되었지만, `v0.1.0` tag/GHCR publish와 운영 배포는 별도 단계로 남아 있습니다.

### Use the local synthetic document-analysis API

문서 route는 기본적으로 꺼져 있습니다. 저장소 밖의 처음부터 만든 합성 PDF와 별도 개발용 PostgreSQL을 사용할 때만 로컬 `.env`에서 다음 두 변수를 함께 opt-in하고 API를 재시작합니다.

```dotenv
FAMILYCARE_ENV=development
FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true
```

두 변수 중 하나라도 다르거나 없으면 router가 등록되지 않아 `POST /api/v1/documents/analysis`와 `GET /api/v1/analysis-jobs/{job_id}`가 모두 `404`입니다. 이 gate는 local synthetic-only 개발용이며 authentication·authorization이 없고 production-safe endpoint가 아닙니다. internal API의 `/health/live`와 `/health/ready`는 gate와 무관하게 유지되지만 Web gateway가 host에 proxy하지 않습니다.

유효한 요청은 source key와 canonical extraction 설정만 보내고, 응답의 `status_url`을 polling합니다.

```bash
curl -i -X POST http://127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}/api/v1/documents/analysis \
  -H 'content-type: application/json' \
  --data '{"schema_version":"1","source_key":"synthetic/policy-001.pdf","document_kind":"policy","extractor_config":{"profile":"quality-v1","quality_rule_version":"quality-v1","table_strategy":"auto"}}'

curl -i http://127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}/api/v1/analysis-jobs/00000000-0000-4000-8000-000000000001
```

성공적인 POST는 파일을 열지 않고 항상 `202 Accepted`로 job UUID, queued state, 상대 `status_url`을 반환합니다. Worker가 `POST → Worker → GET` 순서로 intake·isolated extraction·persistence를 수행한 뒤 status GET은 `succeeded`와 sanitized extraction summary를 보여줍니다. 파일이 없거나 손상되었거나 암호화되어도 POST는 동기 오류로 바뀌지 않으며, Worker 결과에서 encrypted input은 `PASSWORD_REQUIRED`가 됩니다. 요청 body가 잘못되거나 absolute/parent-traversal source key, `password`, `absolute_path`, `raw_pdf`, `url` 같은 추가 필드를 보내면 HTTP `422`와 `error_code: "INVALID_REQUEST"`가 반환됩니다. 알 수 없는 job UUID는 `404`와 `ANALYSIS_JOB_NOT_FOUND`입니다. 오류 응답은 raw value, password, absolute path, document body를 echo하지 않습니다.

API는 `FAMILYCARE_DOCUMENT_ROOT`를 직접 열지 않습니다. 실제 Worker 실행은 아래의 합성 전용 Analyzer 절차와 migration `0002_document_ingestion`을 사용하며, 문서·work root에는 실제 자료를 넣지 않습니다.

### Use the authenticated encrypted document batch

Encrypted import는 Phase 1 synthetic analysis route와 분리된 인증된 batch use case입니다. source catalog는 `FAMILYCARE_IMPORT_ROOT` 아래에서 opaque source ID만 목록화하며, API와 Worker가 같은 root를 read-only로 사용합니다. 브라우저 upload, absolute source path, raw PDF response는 없습니다.

```text
GET  /api/v1/document-import-sources
POST /api/v1/document-batches
GET  /api/v1/document-batches/{batch_id}
POST /api/v1/document-batches/{batch_id}/password
POST /api/v1/document-batches/{batch_id}/cancel
```

batch request는 정확히 한 `FamilyMember`와 bounded 64-character lowercase-hex source IDs를 가집니다. 서버가 로그인 session의 HouseholdScope를 사용하므로 클라이언트가 household를 넓히거나 source path를 지정할 수 없습니다. API는 password를 response·DB·job payload·log에 넣지 않고 Worker 소유 Unix-domain secret socket client로 한 번 전달합니다. Worker는 socket server와 batch password scope를 소유합니다.

password scope는 batch 안에서 재사용할 수 있지만 Worker 반복에서 expiry되며, 실패 파일을 재입력할 때 이전 scope를 교체·폐기합니다. 실행 중인 batch cancellation은 해당 batch만 폐기하고 Worker shutdown은 전체 registry를 폐기합니다. Worker가 아직 잡지 않은 대기 batch를 API에서 취소하면 별도 control frame 없이 최대 5분 expiry에서 정리됩니다. 성공·실패 직후의 즉시 scope 폐기나 프로세스 종료 뒤 terminal memory disposal은 확인하거나 주장하지 않습니다.

성공한 평문은 Worker 전용 archive root에 document별 AES-GCM data key와 AES-KW wrapped key로 저장한 뒤에만 ready가 됩니다. archive master-key file은 저장소 밖 absolute regular file, 정확히 32 bytes, mode `0600` 조건을 만족해야 합니다. 복호화 PDF와 중간 산출물은 Worker work root의 mode `0700`/`0600` 작업 공간에만 존재하며 import source와 Google Drive 원본은 성공·실패·취소 어느 경로에서도 수정·삭제하지 않습니다.

Private batch source, decrypted plaintext extent, and managed archive payload are each bounded to 128 MiB. The parser safety contract remains 500 pages, 64 MiB parser output/`RLIMIT_FSIZE`, 1536 MiB child address space, 90-second child CPU, 120-second parent wall timeout, and 64 open descriptors.

native extraction 뒤에는 `OCR_REQUIRED`로 분류된 1-based page만 선택적 OCR 대상이 됩니다. `TEXT_SUFFICIENT` page는 renderer와 engine을 호출하지 않습니다. Worker는 read-only source descriptor에서 bounded bytes를 PDFium으로 읽어 fixed 300 DPI PNG를 mode `0600` handle에 만들고, `/usr/bin/tesseract`를 fixed `kor+eng` argv와 `shell=False`로 실행해 bounded TSV를 stdout에서 파싱합니다. `pytesseract` dependency와 TSV artifact는 없으며 OCR 결과는 `ocr_layers`/`ocr_pages`/`ocr_blocks`의 별도 provenance layer에 저장됩니다. native blocks와 Evidence는 덮어쓰지 않습니다.

각 page의 PNG는 recognition 직후 삭제되고 outer Worker workspace도 성공·실패·취소·timeout·shutdown 경로에서 삭제됩니다. batch status는 `ocr_state`, 0..500 `ocr_pages_processed`, 최대 8개의 unique warning codes만 projection하며 OCR text, coordinates, image path, filename, stderr는 노출하지 않습니다. 합성 테스트는 선택 page, provenance separation, cleanup, atomic rollback을 검증하고 Worker image smoke check는 `eng`/`kor` language availability를 요구하지만, 실제 private PDF acceptance를 대신하지 않습니다.

encrypted batch와 selective OCR은 `main`에 병합되었습니다. private-runtime PR #27과 Tailscale inspection 보완 PR #28도 merge되었고, WSL Compose·Tailscale HTTPS·인증 브라우저 login/navigation/logout·synthetic OpenAI pipeline acceptance가 통과했습니다. 실제 private data와 OCR, mobile, Windows, 다른 기기, tag/GHCR publish는 아직 pending입니다. 합성 테스트나 localhost HTTP를 해당 실제 환경 검증으로 대체하지 않습니다. 자세한 상태는 [`docs/release/v0.1.0-verification.md`](release/v0.1.0-verification.md)에 기록합니다.

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

Phase 1 구현과 CI에서는 실제 자료 또는 private external root를 열지 않습니다. 테스트는 처음부터 만든 합성 PDF를 checkout 밖 임시 디렉터리에 복사해 `FAMILYCARE_DOCUMENT_ROOT`로 지정합니다. 이 변수는 Phase 1 synthetic-only root이며 private batch source root로 재사용하지 않습니다.

```dotenv
FAMILYCARE_DOCUMENT_ROOT=/absolute/path/outside/repository
FAMILYCARE_WORK_ROOT=/absolute/path/outside/repository/work
FAMILYCARE_IMPORT_ROOT=/absolute/path/outside/repository/import
FAMILYCARE_ARCHIVE_MASTER_KEY_FILE=/absolute/path/outside/repository/master-key
```

위 문자열은 경로 형식 예시이며 실제 경로가 아닙니다. 실제 값을 문서, 이슈, PR, 로그에 복사하지 않습니다.

`FAMILYCARE_IMPORT_ROOT`는 API와 Worker에만 read-only로 공유합니다. Compose의 archive/work는 Worker-only named volume이며 container 내부 `FAMILYCARE_ARCHIVE_ROOT`와 `FAMILYCARE_WORK_ROOT`를 가리킵니다. master-key file은 absolute regular file, 정확히 32 bytes, mode `0600`, UID `10002`여야 하며 key 값은 환경변수·Compose YAML·image·DB·log에 넣지 않습니다. `FAMILYCARE_SECRET_SOCKET`은 API/Worker가 공유하는 named volume 내부 socket path이며 host path가 아닙니다. Worker image의 `eng`/`kor` language package smoke와 합성 Compose permission smoke는 실제 자료·Windows·mobile·Tailscale·provider acceptance를 대신하지 않습니다.

현재 PDF safety contract는 128 MiB input, 500 pages, 120-second parent wall timeout, 90-second child CPU, 1536 MiB address space, 64 MiB parser output/`RLIMIT_FSIZE`, 64 open descriptors입니다. Private batch source, decrypted plaintext extent, and managed archive payload are each bounded to 128 MiB. Work directory는 `0700`, file은 `0600`이며 SHA-256은 1 MiB chunk로 계산합니다. 요청과 job은 relative `source_key`만 사용하고, resolved regular file은 root 아래에 있어야 하며 symlink traversal과 `%PDF-` magic 불일치를 거부합니다.

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

다음은 host에서 직접 실행하는 합성 local/test PostgreSQL 전용 절차입니다. private Compose runtime은 위의 `docker compose ... run --rm api alembic ... upgrade head`를 사용합니다.

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

# Private Local Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal**

FamilyCare v0.1을 한 가구의 비공개 로컬 런타임으로 실행한다. 외부에 게시되는 포트는 Web gateway 하나뿐이고, API·Worker·PostgreSQL은 Compose 내부 네트워크에서만 통신한다. 원본 import, 암호화 archive, Worker 작업 디렉터리, Unix-domain secret socket은 명시된 volume 경계로 분리한다. OpenAI API key는 Worker만 읽을 수 있으며, Web과 API에는 전달하지 않는다. 기존 Phase 1 API·DB·문서 분석 계약은 유지한다.

**Current status**

암호화 document-import의 API·Worker 계약, gateway-only Compose, private mount/readiness policy, bounded read-only Tailscale inspector는 PR #27과 PR #28로 `main`에 merge되었다. 두 PR의 CI/post-merge 검증과 WSL Compose, Tailscale HTTPS, 인증 브라우저 login/navigation/logout, synthetic OpenAI pipeline acceptance는 통과했다. pre-created snapshot용 offline backup-set packaging과 deletion-free archive audit도 합성 경계에서 구현했다. 실제 backup 취득·PostgreSQL restore drill·archive cleanup, 실제 보험 PDF·파생 데이터·OCR, Windows/mobile, 다른 실제 기기는 별도 acceptance이며 완료로 주장하지 않는다.

**Architecture**

- Compose 서비스 집합은 정확히 db, api, worker, web 네 개다.
- web만 host port를 publish하며 nginx가 /api/를 내부 api:8000으로 전달한다. API 및 PostgreSQL의 host port mapping은 존재하지 않는다.
- db, api, worker, web은 기본 Compose network에서 이름으로 통신한다. 외부 reverse proxy, public ingress, Cloud Run, 별도 검색 서비스는 이 PR에서 추가하지 않는다.
- db volume은 PostgreSQL 데이터만, archive volume은 Worker가 쓰는 애플리케이션 암호화 문서만, worker-work volume은 Worker가 처리 중인 합성·사용자 승인 입력의 임시 파일만 담는다.
- secret-socket volume은 API와 Worker가 Unix-domain one-time secret handoff에만 사용한다. socket server는 Worker가 소유하고 API는 client로 연결한다. TCP socket, in-memory broker, 데이터베이스 job payload로 secret을 전달하지 않는다.
- `FAMILYCARE_IMPORT_ROOT`는 저장소 밖 absolute directory로 API와 Worker에 같은 read-only bind mount로 제공한다. API는 source catalog와 opaque ID 해석, Worker는 descriptor-safe intake를 수행한다. archive와 work는 Worker-only named volume의 container path이며, 저장소 밖 archive master-key file만 Worker에 read-only bind mount한다.
- `FAMILYCARE_DOCUMENT_ROOT`는 Phase 1 synthetic-only root로 유지하며 private batch source로 사용하지 않는다.
- archive master key는 Worker 전용 read-only 외부 absolute regular file이며 정확히 32 bytes, mode `0600`이어야 한다. Worker는 archive 복호화, PDF password scope, OCR, AI adapter를 소유하고 API는 HTTP 인증·계약·작업 생성과 secret socket client만 수행한다.
- offline backup-set tool은 operator가 writer quiesce 뒤 저장소 밖에 미리 준비한 PostgreSQL custom dump와 encrypted archive snapshot만 packaging·verification·fresh materialization한다. master key는 set에 복사하지 않고 path는 argv나 output에 넣지 않으며 live DB/volume snapshot과 `pg_restore`를 실행하지 않는다.
- Worker archive audit는 `managed_archives` object key·ciphertext size와 archive filesystem metadata를 read-only로 대조하고 aggregate count만 출력한다. audit finding은 삭제·격리 권한이 아니며 actual cleanup은 별도 정책과 승인이 필요하다.
- import source와 Google Drive 원본은 어떤 성공·실패·취소 경로에서도 수정하거나 삭제하지 않는다.
- 자동화된 검사와 PR/CI는 Tailscale 상태를 읽기 전용으로만 확인한다. PR merge 뒤 root acceptance에서도 기존 Serve 구성을 snapshot으로 보존하며, 필요한 경우에만 충돌 없는 전용 HTTPS endpoint를 추가할 수 있다. 이번 acceptance는 기존 mapping을 재사용했으며 기존 endpoint 교체, Funnel, route, SSH, key, up/down, logout 변경은 금지한다.
- 애플리케이션 인증은 012의 두 관리자·Argon2id·hashed server session 계약을 사용한다. Tailscale 네트워크에 연결된 것만으로 인증된 것으로 취급하지 않는다.

**Tech Stack**

- Docker Compose v2와 현재 저장소의 pinned Docker base images
- nginx-unprivileged 1.31.2-alpine3.23, UID 101:101
- API Python 3.14.7, Uvicorn, PostgreSQL 18.6-alpine
- Worker Python 3.14.7, UID 10002:10002, existing PostgreSQL job queue
- pytest, pytest-cov가 아니라 기존 pytest invocation, Python TOML/YAML structural checks
- Tailscale CLI의 read-only JSON/status output을 policy 검사와 post-merge acceptance에 사용한다. 기존 HTTPS Serve mapping을 보존한 채 Tailscale HTTPS acceptance를 통과했으며, 추가 mutation이나 Funnel은 사용하지 않았다.
- GitHub Actions는 synthetic fixtures와 repository policy만 실행하며 실제 문서·비밀값·외부 AI는 사용하지 않는다.
- backup/audit CI는 synthetic custom-dump bytes와 application-encrypted objects, fake read-only DB connection만 사용한다. 실제 database, archive volume, recovery key, private path는 사용하지 않는다.

**Spec**

### Global Constraints

각 Task에서 다음 규칙을 반복해서 확인하고, 하나라도 위반되면 구현과 PR 준비를 중단한다.

- 실제 보험증권·약관·의료문서, 그 추출 텍스트·표·스크린샷·페이지 이미지·OCR·embedding을 저장소와 CI에 넣지 않는다.
- 실제 이름·이메일·주소·전화번호·생년월일·증권번호·금액·Drive 식별자를 코드·fixture·로그·문서에 기록하지 않는다. 테스트는 Admin A, Family Member A, Sample Policy, synthetic-policy-001처럼 처음부터 합성 값으로 작성한다.
- CI와 PR은 실제 문서, 실제 password, 실제 master key, Google Drive, 외부 AI 없이 실행한다. 자동 acceptance는 저장소 밖의 합성 문서만 사용하고, 실제 자료 검수는 사용자가 정확히 지정한 외부 파일과 범위에 한해 별도 단계로 수행한다.
- AI는 구조화와 설명을 보조할 뿐 자격 판정·계산·권한 부여의 권위자가 아니다. 입력이 없거나 계약 상태가 확인되지 않으면 판정은 UNKNOWN이고, Compose 연결 실패를 MATCH/NO_MATCH로 바꾸지 않는다.
- Phase 1의 /health/live, /health/ready, /api/v1/documents/{document_id}/analysis, document-ingestion.v1, extraction-result.v1, analysis-job.v1, source_key 상대경로, PASSWORD_REQUIRED 및 existing job lease 계약을 보존한다.
- 브라우저 service worker와 Web Storage에는 PDF, 보험 데이터, API response, session token을 저장하지 않는다. 로그에는 문서 본문·검색어·개인식별자·token·실제 경로를 기록하지 않는다.
- 기존 Compose stack 또는 다른 프로젝트의 프로세스·container·volume·port를 중지·재생성·삭제하지 않는다. 검증 전 ownership과 port를 read-only로 확인한다.
- master key와 import root의 실제 값은 명령행·Git·image layer·CI log에 노출하지 않는다. 문서에는 변수 이름과 형식만 기록한다.

### Runtime interfaces

Create or modify the following Python interface in scripts/private_runtime_policy.py:

~~~python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimePolicy:
    service_names: frozenset[str]
    host_published_services: frozenset[str]
    worker_secret_names: frozenset[str]
    forbidden_host_bindings: frozenset[str]


def validate_runtime_config(
    compose: Mapping[str, Any],
    environment: Mapping[str, str],
) -> list[str]:
    """Return stable, non-sensitive policy errors."""


def validate_tailscale_inspection_command(argv: Sequence[str]) -> None:
    """Accept status/ip/serve-status-json read-only forms; reject mutations."""


def validate_private_roots(
    repository_root: Path,
    import_root: Path,
    archive_root: Path,
    worker_work_root: Path,
) -> None:
    """Require absolute, distinct, non-repository roots."""
~~~

`validate_private_roots`는 direct synthetic run 또는 향후 explicit bind-root 구성을 위한 fail-closed helper다. 현재 Compose의 archive/work는 named volume이고 이 helper의 host path 입력으로 사용하지 않는다.

The checker may report service names, mount classes, and policy categories. It must never print environment values, mount contents, source filenames, key bytes, or command output containing node metadata.

The Compose contract is:

~~~yaml
services:
  db:
    # no ports key
  api:
    # no ports key; internal port 8000 only
  worker:
    # no ports key; OPENAI_API_KEY is allowed here only
  web:
    ports:
      - 127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}:8080

volumes:
  familycare-postgres-data:
  familycare-archive-data:
  familycare-worker-work:
  familycare-secret-socket:
~~~

The exact environment variable names are FAMILYCARE_DATABASE_NAME, FAMILYCARE_DATABASE_USER, FAMILYCARE_DATABASE_PASSWORD, FAMILYCARE_ENV, FAMILYCARE_DOCUMENT_ROOT, FAMILYCARE_IMPORT_ROOT, FAMILYCARE_ARCHIVE_ROOT, FAMILYCARE_WORK_ROOT, FAMILYCARE_ARCHIVE_MASTER_KEY_FILE, FAMILYCARE_SECRET_SOCKET, OPENAI_API_KEY, FAMILYCARE_AI_STRUCTURER_MODEL, and FAMILYCARE_AI_VERIFIER_MODEL. `FAMILYCARE_DOCUMENT_ROOT` is Phase 1 synthetic-only; `FAMILYCARE_IMPORT_ROOT` is the shared read-only API/Worker private source root. Compose declares archive, work, and socket as container paths over named volumes; the master-key host path and API key are supplied outside Git to Worker only. The default non-secret model values are gpt-5.6-luna and gpt-5.6-terra. No secret value belongs in this plan or in .env.example. GEMINI_API_KEY is not mounted or consumed in v0.1.

### Task 1: Define the private Compose policy checker

- [x] 2–5 min: Create scripts/private_runtime_policy.py with RuntimePolicy, stable category errors, and validators shown above. Keep filesystem checks read-only and reject import, archive, and work roots that are relative, overlapping, inside the repository, or equal to one another.
- [x] 2–5 min: Create scripts/tests/test_private_runtime_policy.py with synthetic mappings for four services. Assert that an API or db ports key, an API OPENAI_API_KEY, a Web OPENAI_API_KEY, a writable import mount, a missing read-only key-file declaration, or a fifth service is rejected.
- [x] 2–5 min: Run the RED command:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_runtime_policy.py -q
  Expected failure: the test import fails with ModuleNotFoundError for scripts/private_runtime_policy.py or the first policy assertion fails because no private checker exists.
- [x] 2–5 min: Implement the smallest checker that parses an already-loaded Compose mapping and never invokes Docker, Tailscale, or network calls. Keep error strings limited to categories such as host-port, worker-secret-scope, read-only-mount, service-set, and root-boundary.
- [x] 2–5 min: Run the GREEN command:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_runtime_policy.py -q
  Expected result: all policy unit tests pass and no test output contains a synthetic key value or source path.
- [x] 2–5 min: Run focused static checks:
      TMPDIR=/tmp uv run ruff check scripts/private_runtime_policy.py scripts/tests/test_private_runtime_policy.py
      TMPDIR=/tmp uv run mypy scripts/private_runtime_policy.py
  Then run the repository safety checker in read-only mode:
      TMPDIR=/tmp uv run python scripts/check_repository_safety.py
- [x] 2–5 min: Commit only this task as:
      build(runtime): define private compose policy

### Task 2: Make Web the only host gateway

- [x] 2–5 min: Modify infra/containers/nginx.conf so /api/ is proxied to http://api:8000, with HTTP/1.1, forwarded request identity headers, and no-store response headers. Keep /healthz local and retain static app-shell fallback. Do not proxy PDF, archive, or raw file paths.
- [x] 2–5 min: Create scripts/tests/test_private_compose.py as the static gateway/Compose contract test. It asserts exactly one host-published service, Web port 8080, no API or DB port, internal API target api:8000, and no cacheable /api/ response without adding a Web runtime test for infrastructure text.
- [x] 2–5 min: Run the RED command:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_compose.py -q
  Expected failure: the current Compose mapping reports API and db host ports, and nginx configuration contains a 404 location instead of an upstream proxy.
- [x] 2–5 min: Modify infra/compose/compose.yaml to remove ports from db and api, retain the four-service set, add internal health dependencies, and keep only Web mapping 127.0.0.1:${FAMILYCARE_WEB_PORT:-8080}:8080. Add the archive, worker-work, and secret-socket named volumes without publishing them.
- [x] 2–5 min: Apply the minimal nginx route:

~~~nginx
location /api/ {
    proxy_pass http://api:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_no_cache 1;
    add_header Cache-Control "no-store" always;
}
~~~

- [x] 2–5 min: Run GREEN:
      docker compose --env-file .env.example -f infra/compose/compose.yaml config
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_compose.py -q
      corepack pnpm@11.22.0 web:check
  Expected result: Compose config succeeds, exactly four services are listed, only Web has a host port, and Web checks pass. A local config test may use synthetic environment values only and must not print them.
- [x] 2–5 min: Run:
      TMPDIR=/tmp uv run python scripts/check_containers.py
      TMPDIR=/tmp uv run python scripts/check_workflows.py
  Record any pre-existing unrelated failure separately; do not weaken a safety rule to make this task pass.
- [x] 2–5 min: Commit only this task as:
      build(runtime): expose one web gateway

### Task 3: Isolate archive, import, socket, and Worker-only AI access

- [x] 2–5 min: Modify infra/compose/compose.yaml to attach familycare-postgres-data to db, the same read-only `FAMILYCARE_IMPORT_ROOT` bind mount to API and Worker, familycare-archive-data to the Worker archive path, familycare-worker-work to the Worker work path, and familycare-secret-socket at /run/familycare with a dedicated group. The API socket client may share only the import read-only mount and socket volume; it must not receive the archive, work, or key mount.
- [x] 2–5 min: Modify infra/containers/worker.Dockerfile to install the approved OCR/runtime system packages in the Worker image only, retain USER 10002:10002, and create mount points without changing ownership to root at runtime. Modify infra/containers/api.Dockerfile only if the API needs the non-secret socket directory and keep its non-root UID.
- [x] 2–5 min: Create scripts/tests/test_private_mounts.py. Assert read-only import and key mounts, archive writes owned by Worker, socket permissions limited to API/Worker group, API/Web environment absence of OPENAI_API_KEY, and absence of key material in image COPY instructions.
- [x] 2–5 min: Run the RED command:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_mounts.py -q
  Expected failure: current Compose has no archive, worker-work, or secret-socket volume and the Worker-only key/mount assertions fail.
- [x] 2–5 min: Add the minimum Compose environment and mount declarations. Use long syntax to make read-only intent machine-checkable:

~~~yaml
api:
  volumes:
    - type: bind
      source: ${FAMILYCARE_IMPORT_ROOT:?set outside Git}
      target: /var/lib/familycare/import
      read_only: true
    - familycare-secret-socket:/run/familycare
  # no archive, work, or master-key mount
worker:
  environment:
    OPENAI_API_KEY: ${OPENAI_API_KEY:?set outside Git}
    FAMILYCARE_ARCHIVE_MASTER_KEY_FILE: /run/secrets/familycare_archive_master_key
  volumes:
    - familycare-archive-data:/var/lib/familycare/archive
    - familycare-worker-work:/var/lib/familycare/work
    - familycare-secret-socket:/run/familycare
    - type: bind
      source: ${FAMILYCARE_IMPORT_ROOT:?set outside Git}
      target: /var/lib/familycare/import
      read_only: true
    - type: bind
      source: ${FAMILYCARE_ARCHIVE_MASTER_KEY_FILE:?set outside Git}
      target: /run/secrets/familycare_archive_master_key
      read_only: true
~~~

  The implementation must use a real Compose secret or an equivalent read-only bind with a pre-created 0600 key file containing exactly 32 bytes; it must not put the key into environment, DB, job payload, log, HTTP response, or image layer. Import source files remain read-only and are never deleted by the import flow.
- [x] 2–5 min: Add a Worker health probe that checks database reachability, import/archive/work availability, master-key validity, and the Unix-socket type without returning path or key content. Fixed GID `10003` and mode `2770` are enforced by image/Compose policy and the synthetic runtime permission smoke. A missing key fails closed and prevents archive import.
- [x] 2–5 min: Run GREEN:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_mounts.py -q
      TMPDIR=/tmp uv run python scripts/check_containers.py
      docker compose --env-file .env.example -f infra/compose/compose.yaml config
  Expected result: mount and service policy tests pass; Docker Compose reports the four services and no host ports except Web. If the local Docker daemon is unavailable, retain the exact failure as unverified rather than changing policy.
- [x] 2–5 min: Run the focused image checks one image at a time:
      docker build --file infra/containers/worker.Dockerfile --tag familycare-worker:policy-check .
      docker build --file infra/containers/api.Dockerfile --tag familycare-api:policy-check .
      docker build --file infra/containers/web.Dockerfile --tag familycare-web:policy-check .
  Inspect only image configuration metadata for non-root user, exposed ports, and environment names. Do not inspect or export filesystem contents containing private data.
- [x] 2–5 min: Commit only this task as:
      build(runtime): isolate private worker mounts

### Task 4: Add read-only Tailscale and private acceptance checks

- [x] 2–5 min: Create scripts/private_acceptance.py with an explicit command allowlist and stable report types. Accept only tailscale status --json --peers=false, tailscale ip -1, and tailscale serve status --json forms; reject the full-peer status JSON form, tailscale serve, funnel, route, ssh, set, up, down, logout, the non-JSON Serve status form, and every unknown argument. Omitting peers keeps the connection-state response within the existing bounded-output contract even on a larger tailnet.
- [x] 2–5 min: Create scripts/tests/test_private_acceptance.py. Feed synthetic status JSON with a node category and assert that the report excludes node names, IP addresses, tailnet identifiers, and command stdout. Assert mutation forms are rejected before subprocess creation.
- [x] 2–5 min: Run the RED command:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_acceptance.py -q
  Expected failure: the module is missing or the current implementation accepts mutation commands.
- [x] 2–5 min: Implement private_acceptance.py so subprocess execution uses an argv list, a fixed timeout, no shell, no output persistence, and a small output-size limit. Return categories such as tailscale-unavailable, tailscale-not-connected, tailscale-connected, tailscale-serve-empty, tailscale-serve-configured, tailscale-serve-gateway-match, gateway-unreachable, and app-auth-required. Parse Serve JSON only in memory; exclude the exact expected FamilyCare loopback target before computing a non-rendered foreign-configuration fingerprint so before/after preservation can be compared without emitting node, IP, tailnet, raw configuration, or port values. Inspection failures return non-zero. Never copy, delete, upload, print, or alter Tailscale state.
- [x] 2–5 min: Add tests for private roots. The caller supplies absolute roots outside the repository for import, archive, and worker work; tests use a temporary directory outside the repository test fixture tree and synthetic files only. Assert that a source root and output root cannot be the same directory.
- [x] 2–5 min: Run GREEN:
      TMPDIR=/tmp uv run pytest scripts/tests/test_private_acceptance.py scripts/tests/test_private_runtime_policy.py -q
      TMPDIR=/tmp uv run python scripts/private_acceptance.py --help
  Expected result: policy and acceptance tests pass and help output contains no environment value.
- [x] 2–5 min: Modify docs/guide.md to describe gateway-only access, Tailscale read-only inspection, app login, backup/restore ownership, and the boundary that real Windows/mobile/Tailscale operation remains separately validated. Do not include a real host address, account, filename, or secret.
- [x] 2–5 min: After the PR is merged, root captures `tailscale status --json --peers=false` and `tailscale serve status --json` through the redacting inspector, supplying the configured loopback Web port only as `--expected-gateway-port`. The gateway is bound only to loopback and the existing HTTPS Serve configuration is inspected without emitting node, account, tailnet, IP, port, raw JSON, or fingerprint values into the task report.
- [x] 2–5 min: The existing Serve mapping that reaches the FamilyCare loopback gateway over HTTPS was reused without replacing existing configuration. No Funnel mapping was created.
- [x] 2–5 min: From an authenticated HTTPS browser, login cookie delivery, `/api/` reverse proxy, no-store headers, ledger/event/result/claim navigation, and logout passed. Windows, mobile, and other-device access remain explicitly unverified; CI or localhost HTTP is not used as a substitute for Secure-cookie HTTPS acceptance.
- [x] 2–5 min: An explicitly marked local-provider smoke inside the Worker with wholly synthetic Evidence and event text passed the approved model IDs, strict structured output, independent verifier, deterministic validator, retry classification, and log-boundary checks. The existing WSL `OPENAI_API_KEY` was not printed, copied, exported, or passed to Web/API. Real-document/OCR acceptance remains unverified.
- [ ] 2–5 min: Only after synthetic private-runtime acceptance passes, process user-selected real PDFs from exact external paths supplied for acceptance. Do not enumerate adjacent directories. Record only format/error categories and aggregate counts; never copy source files or derived text/images into the repository or report. Sanitized failures become new from-scratch synthetic regression fixtures before implementation changes.
- [ ] 2–5 min: Run focused final checks:
      python3 scripts/check_documentation.py
      TMPDIR=/tmp uv run python scripts/check_repository_safety.py
      TMPDIR=/tmp uv run python scripts/check_containers.py
      git diff --check
  Run the root PR gate from docs/plan/003-v0.1-implementation-index.md once, serially, immediately before opening the PR. This is a gate, not a reason to weaken private-runtime policy.
- [ ] 2–5 min: Commit only this task as:
      test(runtime): add private acceptance checks

### Operational readiness follow-up

- [x] Add an offline `capture`/`verify`/`materialize` contract for an already-created PostgreSQL custom dump and quiesced flat encrypted archive snapshot. Require external absolute paths, mode `0700`/`0600`, new destinations, exact artifact names, bounded members, SHA-256 and key-derived manifest HMAC. Never copy the master key or invoke PostgreSQL commands.
- [x] Keep all backup CLI paths in local environment variables rather than argv and emit fixed status/error codes without paths, object keys, key bytes, or document data.
- [x] Add `familycare-archive-audit` with startup and transaction read-only PostgreSQL enforcement, bounded `nofollow` filesystem metadata scanning, count-only JSON and exit `0` clean / `1` findings / `2` error. Do not expose a delete or quarantine operation.
- [x] Verify capture→verify→materialize→decrypt, tamper/wrong-key/path/symlink/race rejection, audit categories, output minimization, non-mutation and read-only SQL using only synthetic fixtures.
- [ ] After every actual-data writer session is quiesced and the operator explicitly approves exact external targets, define and execute named-volume snapshot acquisition plus isolated PostgreSQL restore validation. Do not infer approval from the existence of this tooling.
- [ ] Define retention, object identification, quarantine and physical deletion policy before acting on any archive audit finding. A non-zero finding count alone never authorizes mutation.

### Root PR gate

Before the root agent opens the PR for this plan, root must review the complete diff once and run the focused checks for this PR, followed by the Root PR gate in docs/plan/003-v0.1-implementation-index.md. The gate must confirm:

- Compose has exactly db, api, worker, web and only Web has a host port.
- /api/ is reachable only through Web gateway and is never cached.
- Worker alone can read OPENAI_API_KEY and archive master key.
- The shared `FAMILYCARE_IMPORT_ROOT` mount is read-only for API and Worker; archive/work/master-key are Worker-only with no repository path, and the secret socket is served by Worker and consumed by API.
- Automated Tailscale inspection is read-only; any post-merge mutation is one additive, collision-free FamilyCare HTTPS Serve endpoint with before/after evidence and no change to existing mappings.
- Phase 1 routes, schemas, job lease, error semantics, and UNKNOWN behavior remain intact.
- CI uses only synthetic fixtures and no actual private material.
- A fresh migrated database can be initialized exactly once through the stdin-password `familycare-admin init` transaction and can then authenticate through the Web gateway.
- Any unverified actual private data, Windows, mobile, other-device, real-document/OCR, or future release-publication acceptance is reported as unverified.

The PR title should follow Conventional Commits, for example:

    build(runtime): make local Compose private by default

# Workthrough: Review findings hardening

**Date:** 2026-08-29
**Branch:** `fix/review-findings`
**Pull request:** [#33](https://github.com/jihoon22-lee/family-care/pull/33)

## Overview

저장소 전체 검토에서 확인된 네 경계를 하나의 PR에서 보강했다. destructive PostgreSQL
integration suite는 명시적인 disposable test database에서만 시작할 수 있게 했고, 외부 AI로
전달되는 policy Evidence의 household identity 최소화를 확대했다. Candidate 원장 projection은
field별 source/status Evidence와 인증된 audit actor를 보존한다. Web에서는 ID 없는 문서 제안을
사용자가 role·page 범위로 확인할 수 있고 일시적 polling 장애 뒤 자동 복구한다.

코드·테스트 4개 커밋을 먼저 push한 뒤 GitHub Actions 7개가 모두 통과한 것을 확인했다. 이
문서는 사용자 지시대로 그 첫 CI 뒤에 별도 documentation commit으로 추가하며, 같은 PR의 두
번째 CI가 통과하기 전에는 merge하지 않는다.

## Review context

검토한 실제 경로는 다음과 같다.

```text
integration test selection -> pytest collection hook -> database identity guard
Evidence rows -> household identity loader -> provider-bound minimizer
candidate fields -> projection repository -> PolicyContract/Rider Evidence
authenticated request -> candidate mutation -> audit actor
inventory suggestion -> explicit component review -> inventory reload
batch status poll -> transient/client error classification -> retry or stop
```

정적 경로만으로 충분하지 않은 항목은 합성 PostgreSQL 18과 Chromium E2E로 동적 검증했다.
실제 보험 자료, 저장소 밖 private source, 실제 provider, 운영 database에는 접근하지 않았다.

## Changes

### 1. Destructive integration database guard

- root `conftest.py`는 선택된 test 중 `integration` marker가 하나라도 있을 때만 guard를 실행한다.
- `scripts/integration_test_database.py`는 `FAMILYCARE_DATABASE_URL` fallback을 금지하고
  `FAMILYCARE_TEST_DATABASE_URL`과 정확한
  `FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true`를 요구한다.
- guard는 실제 연결 후 `SELECT current_database()`를 읽어 database 이름에 standalone `test`
  또는 `ci` marker가 있는지 확인한다. 세 조건이 통과한 뒤에만 기존 integration fixture가 읽는
  runtime URL을 test URL로 설정한다.
- CI와 release workflow는 별도의 합성 database 이름·URL·opt-in을 제공하며 workflow 정책
  검사가 이 계약을 고정한다.

```bash
FAMILYCARE_TEST_DATABASE_URL=postgresql+psycopg://familycare:synthetic-only@127.0.0.1:55439/familycare_review_test \
FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true \
TMPDIR=/tmp uv run pytest -m integration apps/api/tests workers/analyzer/tests -q
```

### 2. Provider-bound identity minimization

- Evidence loader는 선택된 FamilyMember가 active HouseholdSpace에 속하는지 확인한 뒤 같은
  household의 모든 active 표시명·내부 별칭을 수집한다.
- identity term은 최대 16개로 제한하며 초과, 잘못된 값, scope 불일치는 provider 호출 전에
  fail closed한다.
- minimizer는 runtime identity term, email, phone, policy/contract identifier에 더해 한글·영문
  계약자, 피보험자, 수익자, 성명, 주소, 생년월일과 식별번호 label의 값을 제거한다.
- 다음 contract field가 같은 line에 이어져도 insurer, product, date, status, Rider와 amount 같은
  구조화 입력은 보존한다. 각 Evidence slice의 240-character bound와 text-free `repr`도 유지한다.

### 3. Candidate Evidence lineage and audit actor

- Policy source Evidence 우선순위는 `product_name -> insurer`, Rider는
  `rider_name -> rider_key`이며 같은 field 안에서는 최소 UUID로 결정론적으로 고른다.
- Policy/Rider status는 각각 exact `policy_status`/`rider_status` Evidence가 있어야
  non-unknown 값으로 저장된다. 값만 있고 근거가 없으면 `unknown`과 null status Evidence가 된다.
- correction, confirmation, rejection은 request에 이미 해석된 `AuthContext`의 UUID actor를
  사용한다. actor household가 현재 scope와 다르거나 인증 actor가 없으면 mutation을 거부한다.
- repository 단위 회귀 테스트를 추가해 unrelated Evidence, policy-only Evidence, status fallback,
  actor lineage를 직접 검증한다.

### 4. Document review and polling recovery

- handwritten Web client가 기존
  `POST /api/v1/family-members/{member_id}/insurance-document-components` endpoint를 호출한다.
- component ID가 없고 `READY + SUGGESTED`인 source에는 explicit review form을 표시한다. intake
  role은 기본 authority가 아니며 사용자가 role을 고르고 처리된 component의 page 범위 안에서
  시작·끝 page를 입력해야 한다.
- 성공하면 `USER_CONFIRMED` component를 만들고 inventory를 다시 읽는다. 기존 contract/set
  attach는 별도 명시적 단계로 남는다.
- batch polling의 network/5xx 오류는 다음 poll에서 재시도하고 회복한 응답은 이전 오류를
  지운다. 4xx와 인증 오류는 반복하지 않는다.

## Contract impact

- public OpenAPI와 JSON Schema에는 변경이 없다. 이미 존재하던 component-create endpoint를 Web이
  사용하기 시작했다.
- integration test command 계약에는 두 environment variable과 connected database-name check가
  추가됐다.
- 보험 판정 값과 계산 계약은 바뀌지 않았다. 근거 없는 status가 더 보수적인 `unknown`으로
  저장될 뿐 `MATCH`나 `NO_MATCH`를 새로 만들지 않는다.

## Test-first evidence

- DB guard: module/hook 부재로 collection import가 실패하고 workflow contract test 2개가
  실패하는 RED를 확인했다. 구현 뒤 focused 25 tests가 통과하고 1개가 deselect됐다.
- AI minimization: labelled synthetic name/address가 남는 RED를 확인했다. 구현 뒤 focused 13
  tests가 통과했다.
- Candidate projection: arbitrary source Evidence, 근거 없는 active status, nullable actor 때문에
  8 failures/11 passes인 RED를 확인했다. 구현 뒤 focused 43 tests가 통과하고 12개가 deselect됐다.
- Web: create client/UI와 transient retry가 없어 focused tests가 실패했고, page maximum이 처리
  boundary 7이 아니라 500인 추가 RED도 확인했다. 구현 뒤 관련 Vitest가 통과했다.
- 첫 Chromium 전체 실행은 현재 API가 `sources`를 반환하는데 E2E가 과거 `source_ids`를 기대해
  1 failure/13 passes였다. request expectation을 현재 계약에 맞춘 뒤 14 tests가 통과했다.
- 첫 full Web gate는 변경된 3개 파일의 Prettier 검사에서 실패했다. formatter를 적용하고 같은
  full gate를 다시 실행해 통과했다. 실패 실행을 성공으로 간주하지 않았다.
- 완료된 v0.1.0 metadata를 기록하자 기존 documentation checker가 pre-tag `PENDING` template만
  허용해 실패했다. completed workflow/head SHA와 image별 version/SHA digest equality를 검증하는
  test를 먼저 바꿔 2 failures를 확인한 뒤 parser를 갱신했고 focused 2 tests가 통과했다.
- 최종 Python format 첫 실행은 새 documentation checker 한 줄을 지적했다. Ruff formatter를
  적용한 뒤 전체 format check를 다시 실행해 통과했다.

## Verification

최종 code state에서 다음 검사를 직렬로 실행했다.

| Area | Result |
| --- | --- |
| Documentation | `48 passed` |
| Repository safety | `562 paths` checked, passed |
| Web | Prettier, ESLint, TypeScript, 20 files/117 tests, Vite build and PWA passed |
| Python format/lint | Ruff format `390 files`, Ruff check passed |
| Python types | mypy `173 source files`, passed |
| Default Python suite | `1,274 passed, 112 deselected, 3 subtests` |
| Contracts | generated OpenAPI/JSON Schema checks passed |
| Container definitions | 3 images and 4 services passed |
| Workflow policy | passed |
| Diff whitespace | `git diff --check` passed |
| Browser E2E | Playwright Chromium `14 passed` |

Docker 작업 전 19 GiB memory, 10 GiB available, 8 GiB unused swap과 기존 container를 read-only로
확인했다. 별도 임시 `familycare_review_test` PostgreSQL 18.6 database에 migration
`0001`~`0017`을 적용한 뒤 guard를 통과한 integration suite에서 `112 passed, 1,274 deselected`를
확인했다. 작업 소유 임시 container만 중지·삭제했고 다른 session의 container와 port는 변경하지
않았다.

코드 전용 commit은 다음과 같다.

- `e21b4c5 test(db): guard destructive integration databases`
- `1379964 fix(ai): redact provider-bound identities`
- `f1c0dc6 fix(api): preserve candidate evidence and audit lineage`
- `4656ead fix(web): enable document review and polling recovery`

첫 PR CI는 [run 33252772383](https://github.com/jihoon22-lee/family-care/actions/runs/33252772383)에서
`Repository safety`, `Python`, `PostgreSQL integration`, `Web`, `Container (api)`,
`Container (web)`, `Container (worker)` 7개가 모두 통과했다.

## Files modified

### Database and workflow safety

- `conftest.py`
- `pyproject.toml`
- `scripts/integration_test_database.py`
- `scripts/check_workflows.py`
- `scripts/tests/test_integration_test_database.py`
- `scripts/tests/test_workflows.py`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

### Candidate and AI boundaries

- `workers/analyzer/src/familycare_worker/ai/evidence_loader.py`
- `workers/analyzer/src/familycare_worker/ai/minimizer.py`
- `workers/analyzer/tests/test_policy_evidence_loader.py`
- `workers/analyzer/tests/test_policy_ai_minimization.py`
- `apps/api/src/familycare_api/policies/candidate_repository.py`
- `apps/api/src/familycare_api/policies/candidate_router.py`
- `apps/api/src/familycare_api/policies/candidate_service.py`
- `apps/api/tests/test_policy_candidate_repository.py`
- `apps/api/tests/test_policy_candidate_api.py`
- `apps/api/tests/test_policy_candidate_integration.py`
- `apps/api/tests/test_rider_clause_rules_api.py`

### Web document flow

- `apps/web/src/api/insurance-document-inventory.ts`
- `apps/web/src/api/insurance-document-inventory.test.ts`
- `apps/web/src/features/ledger/InsuranceDocumentInventory.tsx`
- `apps/web/src/features/ledger/insurance-document-inventory.test.tsx`
- `apps/web/src/features/documents/ImportPage.tsx`
- `apps/web/src/features/documents/document-import.test.tsx`
- `apps/web/e2e/document-import.spec.ts`

### Documentation

- `README.md`
- `CHANGELOG.md`
- `docs/guide.md`
- `docs/plan/000-project-roadmap.md`
- `docs/plan/005-policy-candidate-review.md`
- `docs/plan/018-insurance-document-inventory.md`
- `docs/design/ai-document-analysis.md`
- `docs/design/policy-ledger.md`
- `docs/design/insurance-document-inventory.md`
- `docs/design/test-strategy.md`
- `docs/release/v0.1.0-verification.md`
- `scripts/check_documentation.py`
- `scripts/tests/test_release_evidence.py`
- `workthrough/2026-08-29-review-findings-hardening.md`

## Privacy and security boundary

- 모든 새 fixture와 입력은 처음부터 만든 합성 문자열과 UUID만 사용한다.
- 실제 이름, 연락처, 주소, 증권번호, 보험금액, 문서 본문, OCR, image, private path, Drive ID,
  provider payload, credential을 저장소나 로그에 넣지 않았다.
- 실제 문서 root를 탐색하지 않았고 외부 provider나 운영 database를 호출하지 않았다.
- GitHub에서 확인한 공개 PR, Actions, Release와 GHCR metadata에는 credential이나 registry response
  body를 기록하지 않았다.

## Unverified and unchanged boundaries

- 실제 보험 PDF와 파생 text/table/image/OCR, 실제 provider 요청, 실제 가족별 inventory 비교
- Windows browser, 실제 mobile PWA, 다른 실제 기기의 Tailscale 접근
- 운영 database, public ingress, Cloud Run 또는 다른 운영 배포
- 이번 작업의 tag 또는 release 생성; 기존 `v0.1.0`·`v0.2.0` metadata만 문서화했으며 새 tag는
  만들지 않았다

문서 follow-up commit을 같은 PR에 push한 뒤 required CI 전체가 다시 통과해야만 merge한다.

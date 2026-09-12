# Source unit in a currency field

- Status: implementation, required CI, protected application, release, activation and bounded HTTPS acceptance complete; partial source support and device limits retained.
- Scope: B02 Task 3/4 / #63; final activation and acceptance #69/#70.
- Base: PR #106 source `39a63bcf4e3ed61eb0be44c0f27ab6eebf1205ed`.
- Branch: `fix/source-unit-currency`.

## Problem and outcome

Explicit v13 recovered 37 enrollment rows on the approved clone, preserving all prior
records. Twelve new rows retained empty money because the saved draft used the exact
native Korean amount unit as its currency value. All twelve independently proved the
complete name cell, unscaled integer, amount column and explicit unit; the ISO-only
currency guard prevented the otherwise valid conversion. This is a known processing
limitation, not missing user documentation.

Explicit v14 / normalization v8 accepts that exact source unit only after the unchanged
same-row proof. It creates a separate scaled amount/KRW draft for fresh verification.
Foreign or differing units, invented context and old raw responses remain unchanged.
The API separately proves the original numeric cell and unit, old v13 program removal,
source/generation/association, fresh verifier and unchanged ledger/user-review versions.
Only the absent money pair and version/time may change. Historical v1-v13 behavior stays
unchanged; schema 0083 permits the new explicit processing revision.

## Evidence and remaining acceptance

PR #106 protected execution: schema 0082, 8 ranges retained as REVIEW, 37 new Riders,
selected contract 16 to 53. Additional journal 22 requests / USD 0.52531240. Its canonical
refresh created 34 links, reaching 50 selected / 59 global, then the helper's overly
broad all-53 expectation failed. Ten amount/currency conflicts are empty native pairs;
five display-name conflicts remain. Three native rows have no proven canonical link.
These partial links are not reported as complete identity reconciliation.

The first authenticated app helper failed on its own wrong Evidence response field
name before creating an event. Correcting that field and using the actual 50-link
boundary passed the final AI-off read/event/history checks in 67.883 seconds: native53,
combined100 to unique50, source excerpt available, previous insurance/review/claim rows
preserved, external HTTP/provider jobs0. The generic planned-event sample had total191,
evaluated0, unsupported191 and candidates0; this does not establish useful claim guidance.
The separate rule-publication reader is being inspected. The catalog's executable fact
flags are intentionally false and are not a defect or an authorization to promote data.

Finish this bundle's implementation/tests/docs before one focused verification pass.
Use affected-only reruns after failures and final required CI for full suites. No actual
source text, values, identities or paths are recorded here. At that protected phase,
data remained in the authorized owned clone and no live switch or release had occurred.
The current tag/release/activation state is recorded below.


## Focused verification

On `9ff1a32` plus the final API/replay/schema/runtime/test/doc wiring, the following
unit selection passed 192 cases (2 deselected, 6.50 seconds), and Mypy passed 369 files:

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_source_unit_currency_draft.py workers/analyzer/tests/test_policy_draft_recovery.py apps/api/tests/test_missing_amount_enrichment.py apps/api/tests/test_guidance_amount_source.py apps/api/tests/test_policy_currency_enrichment.py apps/api/tests/test_runtime_schema_readiness.py workers/analyzer/tests/test_runtime_schema_readiness.py -q --tb=short
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
```

The first focused PostgreSQL run had two failures and one pass (18.95 seconds): the
two publication cases started against the still-0082 synthetic test database and v14
was correctly refused. The migration roundtrip/history case then passed and left the
dedicated synthetic database at 0083. No product-code change was needed. Only the two
failed publication/user-correction cases were rerun: both passed in 21.43 seconds.
Thus three distinct new PostgreSQL cases passed across the two runs.

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_missing_amount_enrichment_integration.py workers/analyzer/tests/test_source_unit_currency_revision.py -m integration -k 'v14 or source_unit_currency_migration' -q --tb=short
TMPDIR=/tmp uv run pytest apps/api/tests/test_missing_amount_enrichment_integration.py -m integration -k v14 -q --tb=short
```

Both used the owned PostgreSQL 18 synthetic test container and explicit destructive-test
opt-in, never the private database. Packaging checks passed: documentation50, repository
safety1203 paths, Ruff lint/format986 files, and diff whitespace. Required final CI will
supply full-suite evidence without duplicate full local tests.

The separate reader diagnosis found the 191-input denominator is private94 + operational147
minus canonical50, not duplicate insertion of the private context. This member has no
published rules/calculations; other current members have existing supported publications.
Final representative use must use a previously supported event and still retain this
member's unsupported count in the complete source/support report.


# v0.5.0 final acceptance

- Status: complete (명시된 최종 수용·릴리스·운영 전환·HTTPS 경로) / PARTIAL (자료 지원); 문서 PR 후 이슈 종료 동기화.
- Scope: #60 / WP03 #63 / WP09 #69 / WP10 #70; R01–R20, S01–S14.
- Candidate source: `54496fa915b80e2dcdb043268473eb3ffe72399a`; v13 기록은 `39a63bc`에 연결.
- Schema: `0083_source_unit_currency`; 기존 owned82 결과와 운영 source `2370761`/0069를 구분.
- Documentation branch: `docs/v05-final-acceptance-20260913`.

## Purpose and completion boundary

원문 근거가 확인된 부분을 실제로 사용하면서 미지원 부분과 전체 분모를 보존한다.
#60/#63/#69/#70에 없는 전 자료 100% 자동 확정·모바일 실기기 PASS를 종료 gate로
추가하지 않는다. 약관 신원 UNKNOWN과 사용자 component의 역할/범위를 혼동하지 않고,
기존 고정 평가의 기대값·실패·holdout 분모를 바꾸지 않는다. 알려진 구현 누락의 수정과
최종 필수 CI는 필요하며, 배포·기기·검수 품질은 실제 수행한 범위에서만 보고한다.

이 문서 묶음은 [로드맵](../docs/plan/000-project-roadmap.md),
[B02](../docs/plan/v0.5.0/002-document-structure-and-linking.md),
[B07](../docs/plan/v0.5.0/007-private-runtime-transition.md),
[B08](../docs/plan/v0.5.0/008-final-acceptance.md),
[수용 원장](../docs/release/v0.5.0-verification.md)을 동기화한다.
이전 완료·실패 기록을 지우거나 새 검사로 재계산하지 않는다.

## Implemented and protected results

| 구분 | 확인 결과 | 제한 |
|---|---|---|
| PR #103 | [MERGED](https://github.com/jihoon22-lee/family-care/pull/103), `957d7edf4d94d716bdcd43a62f45d16d25efe85f` | 출처 한정 부모·통화·이름 인용·지목 필드 검수 복구. 전체 약관 신원 완료가 아님 |
| PR #105 | [MERGED](https://github.com/jihoon22-lee/family-care/pull/105), `f4858f62d4cac0f0f11de44a44b7205ab06d6609` | 원문 명시 단위의 빈 가입금액/통화 보강. 가입금액은 지급 예상액이 아님 |
| PR #106 | [MERGED](https://github.com/jihoon22-lee/family-care/pull/106), `34df397`; [CI 34715572418](https://github.com/jihoon22-lee/family-care/actions/runs/34715572418) 7/7·PG 944 | v13/정규화 v7/grounding v5는 증명된 문맥·범위만 복구. 세부 합성 실행은 [원래 workthrough](2026-09-13-proven-policy-draft-context.md)에 보존 |
| PR #107 | [MERGED](https://github.com/jihoon22-lee/family-care/pull/107), `7d1a53d`; [CI 34717282331](https://github.com/jihoon22-lee/family-care/actions/runs/34717282331) 7/7·PG 947 | 명시 원화 단위 보강과 최종 수용 기록 통합 |
| 승인 자료 | 58 sources; policy 43개/703쪽, terms 13개 | 전체 분모 유지, 미지원 자료를 제외하지 않음 |
| v13 실제 선택 처리 | verifier 3회, 새 담보 37개, 해당 계약 native 16→53 | 별도 계약 기존 9개 유지. 기존 원문·후보·교정·검수·원장·청구 이력 보존 |
| 추가 source binding | 기존 2개 + 새 1개 | 13개 unique 이름/금액/통화와 상품 근거 2개를 기존 repository로 선언. 해당 source의 9개 UNRESOLVED·금액 페이지 오차 10개 유지; 이 선언의 신규 가입/담보 0 |
| v13 canonical | 새 연결 34개, 선택 계약 50/53개·전체 59개 | 당시 53개 전부 연결 assertion 실패와 쓰기 완료를 구분. 당시 금액/통화 충돌 각 10·표시명 충돌 5는 아래 v14와 구분 |
| v14 적용 | schema 준비 1.762초, verifier 1회 43.978초 | 금액/통화 12개 보강+담보 1개 추가, 선택 원문의 native 53→54. 기존 다른 필드·전 이력·8개 REVIEW 범위 보존 |
| v14 canonical | 24.044초, 변경 11개=기존 10개 갱신+신규 1개 | 선택 51/54개·전체 60개(다른 계약 9개), 금액 충돌 0·표시명 충돌 6, 원래 private 인용/identity 한계의 미연결 3개 |
| owned82 인증 앱 보존 | PASSED, 67.883초 | 인증·원문 발췌·AI-off 조회·저장·이력 보존. 결합 100개→공통 identity 50개. 첫 helper의 Evidence 필드명 오류/사건 생성 0개 실패는 보존 |
| 규칙 없는 구성원의 대표 입력 | PARTIAL | total 191 = private 94 + operational 147 − canonical 50; evaluated 0/unsupported 191/candidate 0. 해당 구성원의 실행 규칙·계산 0, 중복 합산 버그 아님 |
| 지원 사건 앱 경로 | 확인, 59.831초 | `54496fa`/0083, 규칙 있는 구성원의 기존 입력 복제 1개: 후보 6·POINT 3·FORMULA 1·RANGE 0; support 238=evaluated 5+unsupported 233, 실패 코드 0 |
| 지원 사건 보존·마지막 helper | 경로 확인 / helper FAILED | SUMMARY 근거 2개 조회·저장 재조회 일치·기존 사건 불변·청구 조회·새 사건 soft delete·logout·외부 HTTP 0. 정상 LOCAL_SEARCH_ONLY/SUCCEEDED/attempts 0 행을 provider 증가로 잘못 센 마지막 카운터 오류는 보존 |
| 앱 helper 후속 대사 | 조건 확인, 원시험 FAILED 보존 | 재분석 0; provider 관련 5개 job table 신규 0/work state 0/attempts 0. assistance job 1개는 정상 LOCAL_SEARCH_ONLY/SUCCEEDED/attempts 0/NO_SEARCH_CANDIDATES 행으로 확인 |
| 보조 root-delta 보존 | 40개 table/기존 116,681행 | missing 0/changed 0/additional 10. 단일 앱 실행 전 baseline이 아니므로 보조 증거로 한정; source event API 동등성은 직접 PASS |
| 추가 승인 비용 원장 | HTTP 23회, USD 0.57333290 | 목표 USD 1 / 상한 USD 2, 기존 문서/일일 예약 유지. 과거 20건 고정 모델 평가와 별도 |

range 유래 native 54+9개는 두 계약의 집계다. 기존 legacy 원장까지 포함한 전체 담보
63개로 표현하지 않는다. v13 시점의 예산 22회/USD 0.52531240도 이후 누계와 구분한다.

13개 약관의 제한된 native·기존 OCR 조사와 한 자료 첫 페이지의 로컬 OCR 1회로 완전한
보험사·상품·판본 identity를 확보하지 못했다. OCR로 텍스트가 늘어도 발급주체 근거가
없으면 신원으로 게시하지 않는다. 조사하지 않은 나머지 본문까지 정보 부재로 단정하지
않으며, USER component의 기존 역할·범위와 PARTIAL을 보존했다. 추가 전량 원문 탐색이나
유료 검수 반복은 이 수용 문서 작업에 포함하지 않는다.

## v14 focused verification

명시 원화 단위 보강은 `54496fa`에서 관련 unit 192개와 mypy 369개 소스를 통과했다.
PG는 서로 다른 3개가 통과했다. 첫 두 실패는 전용 합성 DB에 선행 schema 0082 migration을
적용하지 않은 준비 오류였으며, migration 왕복 후 영향받는 두 개만 재실행해 21.43초에
통과했다. 이를 한 번의 전체 PG 성공으로 표시하지 않는다. 실제 자료나 provider를 사용한
검사가 아니며 이번 문서 작업에서 재실행하지 않았다. 최종 CI는 다음 PR에서 한 번 모은다.

## Verification and pending actions

이번 문서 커밋에서 Python의 선택 Markdown 파일/로컬 링크 확인으로 6개 파일·41개 링크,
마지막 줄바꿈·닫힌 code fence를 확인했고 `git diff --check`와 소유 파일 범위 확인도
통과했다. 테스트·DB·Docker·provider·CI는 문서 작업에서 실행하지 않았다.
PR #106과 다음 v14 PR의 소스에 대응하는 최종 CI는 주 작업이 한 번 수집하며 로컬 전체 검사를
중복하지 않는다. 실패 수정 후에는 영향받는 검사만 다시 실행한다.

- COMPLETE: PR #106/#107 최종 CI 각각 7/7과 merge. 최종 docs-only PR의 필요한 검사는
  실제 운영 결과를 모은 뒤 실행하며 로컬 코드 suite를 반복하지 않는다.
- COMPLETE (명시 범위): 지원 사건 앱의 AI-off·기존 이력 보존. 마지막 helper 실패는 보존하고
  재분석 없는 후속 대사로 조건을 해소했다. 규칙 없는 구성원의 191개 미지원은 전체 분모와
  함께 PARTIAL로 남긴다.
- PENDING: #59/#60/#63/#69/#70에 최종 판단 동기화.
- COMPLETE (사전 병합): live 이력 9,208행의 원래 key·본문 보존, local job 6개와 incoming
  run 12개의 명시 alias. 최종 barrier는 추가 INSERT·누락 0·기존 행 보존으로 통과했다. Rider 7개 차이는 승인된 금액 보강/API 원문 증명으로 확인했고
  사용자 보험정보 교정 충돌은 발견하지 않았다. session 상태·약관 확인 시각은 별도다.
- PUSHED / PUBLISHED / ACTIVATED: 최종 v0.5.0 태그·이미지·GitHub Release는 게시됐고
  순차 up activation의 새 컨테이너 수용은 통과했다. 외부 네트워크 수용도 명시된 기존 결과 조회 범위에서 통과했다. 격리 DB 준비를
  새 live 이력의 병합·운영 전환 완료로 표시하지 않는다.
- UNVERIFIED: 실제 모바일/PWA 설치 등 직접 확인하지 않은 환경. 이전 Windows/Linux
  브라우저 수용을 새 기기/새 배포의 PASS로 확대하지 않는다.

실제 값·원문·식별자·경로는 저장소 밖 보호 artifact에만 유지했다. 공개 문서에는 승인된
집계·고정 reason·코드 source/schema와 공개 PR만 기록한다. source binding 실행의
후보·원장·검수·claim·canonical 보존 비교는 통과했으며 연결 1건 이외의 쓰기는 없었다.

## Final release, activation and HTTPS acceptance

| 단계 | 상태 | 근거 / 완료 시 기록할 항목 |
|---|---|---|
| PR #106 통합 | PASSED | merge `34df3970ce51262939df2fe5062ca47ec8d873ce`, 필수 CI 7/7·PG 944 |
| PR #107 통합 | PASSED | merge `7d1a53d85438249c6a9c8e5effc71e092461a858`, 필수 CI 7/7·PG 947 |
| 최종 태그 | PUSHED | `v0.5.0` → `7d1a53d85438249c6a9c8e5effc71e092461a858` |
| release workflow | SUCCESS | [34719394060](https://github.com/jihoon22-lee/family-care/actions/runs/34719394060), [GitHub Release](https://github.com/jihoon22-lee/family-care/releases/tag/v0.5.0) 정식 게시 2026-09-12T22:00:32Z; draft/prerelease 아님 |
| 로컬 VERIFY_RELEASE_IMAGES | 첫 FAILED / 인증 복구 후 PASSED | GHCR_TOKEN/actor 인증 누락으로 manifest-status 단계 실패·전환 없음. 기존 gh 인증을 메모리에서 재사용해 manifest HTTP 200·digest 대조 통과; 실패 단계만 재개, release workflow 재실행 없음 |
| PULL_RELEASE_IMAGES | PASSED | 검증된 이미지를 순차 pull했고 이어 순차 up으로 새 컨테이너를 생성 |
| 전환 준비 첫 시도 | FAILED 보존 / 수정 후 PASSED | 기존 Compose env-file의 symlink를 FILE_BOUNDARY_INVALID로 거부해 전환 전 중단. 승인된 원래 argv를 유지하고 resolved target·내용 hash를 고정하도록 준비 helper만 수정; 이미 확인한 이미지·pull은 재실행하지 않음 |
| writer barrier·이력 | PASSED | history 9개 table 16.268초 재호출, 추가 INSERT 0·누락 0·모든 기존 source/owned 행 보존. 나머지105개 table/live35,160행 보존, source app_sessions19·refresh 시각20·승인 Rider 보강7개 대사. 총114개 table |
| activation | PASSED | `7d1a53d`/schema 0083, 순차 up 새 컨테이너 3개 digest/이미지/리비전/health 일치·API readiness PASS. 별도 restart 없음 |
| 네트워크 첫 시도 | FAILED 보존 | Windows curl 요청 프로세스 시작 시 OSError errno8, HTTPS 요청0. 서비스 HTTPS 응답 실패가 아니라 interop 실행 실패 |
| 네트워크 재조회 | PASSED, 30.765초 | WSL_CURL·HTTPS10/readiness1, 인증·schema83·이미지3·health·no-store·logout 확인. 실패한 네트워크 읽기만 재실행, 재배포/restart 없음 |
| 기존 저장 결과 수용 | PASSED (과거 결과) | 후보6·POINT3·FORMULA1·RANGE0, support238=evaluated6+unsupported232. saved_guidance_stale=true로 최신 fresh 결과가 아님. SUMMARY 근거1·청구/원래 사건 불변 |
| 새 동작·원본 보존 | PASSED | provider activity 불변·local projection delta0·new events0/analyze0/restarts0. manager 새 stage active, 이전 DB 보존 |
| 최종 문서 PR | 로컬 문서 검증 완료 / root 게시 대기 | `docs/v05-release-completion`, 기존 workthrough 재사용. 문서/안전/diff만 검사하고 기존 PR107/release의 코드 검증을 재사용 |

사전 병합의 첫 UNIQUE 충돌은 전체 rollback됐고 실패 기록은 보존했다. 동일 logical job의
6개 원행·원래 UUID/시각과 canonical job 매핑을 보호 manifest에 남겼으며 원래 run/결과/청구
ID는 유지했다. 누락된 run 12개의 INSERT에서만 job FK를 바꾸고 기존 owned 행의
UPDATE/DELETE·constraint 우회·live 쓰기는 하지 않았다. 명시 alias 정규화 후 누락 0과
원래 source/owned 행 보존을 16.128초에 확인했다. 최종 barrier의 별도 재호출도 16.268초에
추가 INSERT·누락 0과 이력 보존으로 통과했다.

위 결과는 주 작업이 제공한 실제 실행 증거에 연결했다. 기능 PR·릴리스의 동일 runtime
검증을 재사용하고 해당 표와 로드맵/B02/B07/B08/수용 원장을 한 docs-only PR로 동기화한다. 과거
실패와 자료 지원 PARTIAL·58개 전체 분모·기기 UNVERIFIED를 보존하고 전량 MATCH를
새로운 종료 gate로 만들지 않는다.

최종 네트워크 수용은 기존 저장 결과를 읽은 검증이다. 앞선 `54496fa`/owned83의 fresh
대표 입력은 support 238=evaluated 5+unsupported 233이었고, 최종 HTTPS의 과거 저장
결과는 6+232 및 `saved_guidance_stale=true`였다. 두 실행의 분모·시점·성격을 합치거나
새 분석이 실행된 것처럼 보고하지 않는다. 실제 모바일/PWA 설치는 계속 UNVERIFIED다.

최종 문서 검증은 `7d1a53d` 기준의 이 여섯 Markdown 변경에 한정한다. 로컬에서
`python3 scripts/check_documentation.py`, `python3 scripts/check_repository_safety.py`,
`git diff --check`를 한 번 실행했다. 문서 50개·저장소 안전 1,203개 경로 및 diff 검사가
모두 통과했다. 코드 suite·DB·Docker·provider·
네트워크 helper를 이 문서 작업에서 다시 실행하지 않는다. push와 최종 이슈 종료 동기화는
주 작업이 문서 커밋을 검토한 뒤 진행한다.

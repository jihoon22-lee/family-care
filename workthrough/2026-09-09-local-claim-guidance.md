# B04 local claim guidance

[WP05 #65](https://github.com/jihoon22-lee/family-care/issues/65)와
[WP06 #66](https://github.com/jihoon22-lee/family-care/issues/66)의 후보·금액 경로를 구현 중이다.
기반은 B03 PR #78 merge `3f4a47cc2d1b4e649d202850020b94882dc4adfe`, 필수 CI 7/7이다.

## Common input and first operational path

기존 분석은 private import가 없으면 실제 원장 자료가 있어도 local_guidance를 만들지 않았다.
공통 context/rule/citation 입력을 추가하고 실제 원장·private publication을 같은 안내 엔진으로
평가한다. 식별자는 원래 저장 종류를 유지하며 private import/section 식별자를 만들지 않는다.
기존 source evidence와 사건일의 상태 기록을 재조회하고 개별 규칙은 기존 DSL로 평가한다.
산식이 없는 가입금액은 예상액으로 제공하지 않는다. historical result는 저장 JSON을 유지한다.

기존 private 계산기는 식별자와 무관한 fact protocol을 통해 재사용한다. private adapter는
publication·인용·상태/가입 의미를 유지한다. 아직 실제 B03 root의 Rider 연결과 최종 중복/합산
선택은 다음 구현 범위이며, 이 단계의 기존 원장 테스트를 새 약관 산식 E2E로 표현하지 않는다.

## Verification so far

2026-09-09 KST, 기반 `3f4a47c` + guidance domain/private adapter/repository/runtime/engine,
DecisionRepository·fact protocol과 전용 테스트의 미커밋 변경에서 실행했다. Python은 격리된
B04 worktree의 API/Worker source를 PYTHONPATH로 지정하고 기존 고정된 검증 환경을 사용했다.
실제 자료·외부 AI·운영 DB는 사용하지 않았다.

- `python -m pytest apps/api/tests/test_operational_local_guidance.py -m integration -q`:
  private import 부재 시 `local_guidance is None` RED 1개(1.62초)를 확인했다. 구현 후
  **1 passed**(4.53초), 이후 기존 private/canonical 안내 통합과 함께 다시 확인했다.
- `python -m pytest apps/api/tests/test_local_guidance.py
  apps/api/tests/test_local_guidance_benchmark.py apps/api/tests/test_guidance_canonical_identity.py
  -q`: **22 passed**(1.02초). 기존 private 안내/고정 benchmark 회귀이며 B04 새 해석·전체 품질
  수용 결과를 의미하지 않는다.
- `python -m pytest apps/api/tests/test_operational_local_guidance.py
  apps/api/tests/test_local_guidance_integration.py apps/api/tests/test_canonical_links_integration.py
  -m integration -k guidance -q`: **5 passed / 25 deselected**(9.75초).
  전용 합성 DB 0055와 destructive-test guard, `TMPDIR=/tmp`를 사용했다.
- 관련 Ruff lint/format와 mypy **9 source files**가 통과했다. 전체 필수 검사는 B04 최종 변경 후
  수행한다.

B03 원문/산식 연결, 분류 체계/판본, 부정/예정/시점 해석, 계산 trace·부분 금액·합산,
UI/snapshot·stale와 전체 기준 검증은 진행 중이다. 릴리스·배포는 아직 수행하지 않았다.

## Original sources and bounded event facts

후속 구현은 실제 Rider의 승인된 Clause 연결, 사건일 약관 선택, 완전한 Clause 원문 구역,
B03의 재생 검증된 semantic root를 연결한다. semantic 인용은 실제 publication/citation,
문서·판본·generation·원문 node/page/span/bbox와 digest를 보존하며 Evidence ID나 private
section ID로 바꾸지 않는다. 일당의 관련성 근거와 분류 조건을 분리하고, 같은 원문에서 완전한
root를 partial root보다 우선한다. 남은 partial 지식은 조건부 후보로 보존한다.
Clause 원문 주소로 root/page 조회를 먼저 제한하므로 다른 조항 1,024개가 필요한 원문을
밀어내지 않는다. 한 Clause의 조회 예산을 넘으면 고정된 실패 상태를 남긴다.

가입금액·통화는 실제 게시된 원문 필드와 현재 원장을 다시 대조한다. 프로그램 검증과
사용자 교정 권한을 구분하고 evidence의 검토 상태를 올리지 않는다. 미게시 draft가 기존
게시 근거를 지우지 않으며, 원장 숫자만 달라지면 원문의 금액 권한을 이어받지 못한다.

제한된 한국어 사건 해석과 구조화 사실 adapter를 추가했다. 부정·예정/실시·시점·가족 범위,
명시 사실/로컬 파생/미상을 분리하고 단어 언급을 임상 확정으로 만들지 않는다. 가입 가정은
확인된 active로 저장하지 않는다. 가정 안의 구성원 이름·별칭을 parser에 전달하되 결과에는
그 값 대신 revision digest만 남긴다. 임상 코드와 원문 분류는 입력의 명시적 code_system과
code_version을 대조한다. API 교정은 이 metadata를 보존하고, 코드만 바꾸면 이전 metadata를
이어받지 않는다. 기존 내부 classification enum의 역사적 호환 의미는 보존한다.

새 안내는 내부 schema `2`와 engine `local-guidance-v2`를 저장한다. migration 0056은 기존
JSON을 수정하지 않으며 v2 이력이 있을 때 파괴적인 downgrade를 거부한다. 과거 조회는
저장 JSON을 유지하고 현재 사건·원장·금액 원문·약관 연결·지식 digest만 비교해 optional
`local_guidance_stale`을 반환한다. 기존 top-level stale의 의미와 혼동하지 않도록 v2 화면은
새 표시를 소비한다. 새 semantic 출처는 약관 근거로 표시하고 같은 쪽의 서로 다른 인용을
지우지 않는다. JSON Schema/OpenAPI/Web 생성물과 엄격한 응답 검사도 함께 갱신했다.

## Focused verification of the first complete source path

2026-09-09 KST 01시대, `427d844`와 source binding/공통 context/사건 code metadata/
새 stale 계약·migration·Web 소비자 및 관련 테스트의 미커밋 변경에서 실행했다. 아래 Python
명령은 `TMPDIR=/tmp`와 B04 worktree의 API/Worker/root PYTHONPATH, 기존 잠금 검증 환경을
사용했다. PostgreSQL은 전용 합성 test DB, destructive-test guard와 schema 0056을 사용했다.
다음은 개발 중 선택 검증이며 B04 PR 전체 필수 검사 완료를 뜻하지 않는다.

- 사건 해석 `edbac0d`, adapter `7835348`: parser 99 + adapter 48 + 기존 fact 회귀 6,
  **153 passed**. adapter의 당시 Ruff·mypy가 통과했다.
- 금액 reader `99791dc`(원 커밋 `8fd7936`): 순수 20 + PostgreSQL 2, **22 passed**.
  원문/표 연속 행, 미게시 draft, 실제 합성 사용자 교정, 불충분한 legacy 근거를 검증했다.
- Clause 범위 reader `427d844`(원 커밋 `cf2a170`): 새 순수 **10 passed**,
  새/기존 PostgreSQL **24 passed**(32.12초), Ruff·mypy 통과. 두 reader의 PG 실행은
  각각 소유한 별도 합성 DB에서 주 에이전트의 무거운 검사와 직렬로 실행했다.
- `python -m pytest apps/api/tests/test_guidance_semantic_selection.py -q`:
  주소·캐시·초과 예산의 부재로 RED 2개를 확인한 후 **4 passed**(0.41초).
- `python -m pytest apps/api/tests/test_guidance_local_event_engine.py
  apps/api/tests/test_guidance_code_scope.py apps/api/tests/test_guidance_semantic_binding.py
  apps/api/tests/test_guidance_context_combination.py apps/api/tests/test_local_guidance.py -q`:
  **37 passed**(0.53초). partial 조건 flag 도입 뒤 private adapter의 AttributeError 회귀를
  확인하고 명시적 adapter 제외 필드로 수정했다.
- `python -m pytest apps/api/tests/test_guidance_code_scope_integration.py -m integration -q`:
  코드 metadata 전송 거부 RED 후 **1 passed**(0.76초). 입력→교정→저장→과거 버전 보존을 검증했다.
- `python -m pytest apps/api/tests/test_semantic_local_guidance_integration.py
  apps/api/tests/test_operational_local_guidance.py -m integration -q`:
  **5 passed**(14.46초). 원문 300/400, 현재 결과의 guidance stale false, 사건 변경과 링크
  폐기 후 stale true·과거 JSON 유지, 원장 숫자 999의 원문 권한 거부를 포함한다.
  중간 실행의 mappingproxy 직렬화 오류 4건은 명시적 규칙/dict 변환으로 수정했다.
- 가족 이름·별칭 연결을 추가한 뒤 위 semantic integration 단독 재실행:
  다른 구성원 입원이 후보를 만드는 RED 후 **1 passed**(10.37초). 합성 구성원은 원문
  projection 전에 생성한다. 이후 생성하면 약관 변경의 subject revision을 의도대로 무효화함을
  확인했고 fixture의 생성 순서를 수정했다. 키 미설정과 HTTP transport 차단은 원문 준비부터
  실제 분석 API·과거 조회까지 적용했으며 외부 요청 **0건**, private import **0건**이다.
- `corepack pnpm --filter @familycare/web test src/features/results/LocalGuidancePanel.test.tsx`:
  semantic 인용/새 stale 표시 RED 3개 후 **14 passed**(최종 1.42초).
  `corepack pnpm --filter @familycare/web typecheck`도 통과했다. optional discriminator에
  따른 타입 오류는 실제 citation 필드로 출처를 구분해 수정했다. 의존성은 frozen lockfile로
  이 worktree에 설치했다. 공유 modules symlink와 offline 설치 시도는 실패했고 사용하지 않는다.
- `ruff check` 관련 guidance/decisions/새 테스트와 `python -m mypy` 해당 **42 source files**
  통과. schema/OpenAPI/business/Web 생성 명령을 실행했다.
- `python -m pytest apps/api/tests -q --tb=short`: **2047 passed / 3 failed / 490 deselected**
  (21.32초). 실패는 새 optional 응답 필드의 명시 허용 목록 3곳이며 해당 계약 검사에 반영했다.
  이 실행을 전체 API 통과로 보고하지 않는다. 수정 후 결과는 아래 후속 증거에 기록한다.

현재 source path는 연결됐으나 수술 activity·시나리오, 후보 정렬/추가 질문, 상세 trace·부분
비용·합산, 청구 bridge와 전체 품질/성능/필수 CI는 계속 구현한다. 실제 문서·외부 AI·runtime
DB·Windows/모바일·릴리스·배포는 이 단계에서 사용하거나 검증하지 않았다.

후속 계약 허용 목록 수정 후 `python -m pytest apps/api/tests/test_decision_api.py
apps/api/tests/test_decision_privacy.py apps/api/tests/test_policy_ledger_contracts.py -q --tb=short`는
**44 passed**(4.03초), `python scripts/check_contracts.py`는 통과했다. 같은 소스와 기록에서
문서 검사 **50 files**, 저장소 안전 검사 **946 paths**, `git diff --check`도 통과했다.

오류 격리 경로도 보강했다. operational reader가 실패해 기존 private 지식으로 안내할 때도
동일한 가정의 구성원 이름·별칭 context를 사용한다. 실제 합성 private publication을 준비한
PG 테스트에서 다른 구성원의 입원을 후보로 만드는 RED를 확인했고, 공통 subject reader로
수정했다. 중간 실행의 누락 import 실패도 수정했다. 이후 `python -m pytest
apps/api/tests/test_local_guidance_integration.py apps/api/tests/test_operational_local_guidance.py
apps/api/tests/test_guidance_code_scope_integration.py
apps/api/tests/test_semantic_local_guidance_integration.py -q -m integration --tb=short`는
**10 passed**(17.79초)였다. source fallback의 성공/가족 거부, pre-guidance migration 호환,
DB snapshot 제약, 코드 metadata 이력, 300/400 및 source stale 보존을 함께 확인했다.
동일 변경의 관련 Ruff와 mypy **42 source files**도 통과했다.

독립 정적 검토는 부분 지식 소실·원문 범위 paging·구성원 명칭 미연결을 발견했고 해당 경로를
수정했다. 코드 metadata의 사용자 권한/버전 잠금/이력과 새 stale·0056 경계에서는 추가
P1/P2를 발견하지 않았다. 정적 검토를 동적 검증으로 표현하지 않는다.

## 사건 관련성·계산 출처·등록 비용 연결

`guidance/activity_binding.py`, `event_facts.py`, `relevance.py`, `runtime_facts.py`는 실제
원문의 활동 조건과 사건 해석을 연결한다. 특정 수술명/분류 코드를 키워드로 확정하지 않으며
실제 사건·조건부 주제·예정의 관련성에 원문 citation과 입력 span을 남긴다. 명시 사실은
해석으로 덮지 않는다. 새 evaluator는 AI 제안의 수치·이력·Rider 필드를 확정 사실로 사용하지
않는다. 기존 판정 snapshot의 정책은 변경하지 않았다.

`calculation_source.py`는 실제 publication과 원문 산식으로 단위·통화·지급 기준을 검증한다.
`calculation_runtime.py`/`trace_projection.py`는 모든 피연산자·음수 중간값·명시 반올림·누락값을
보존한다. 가입금액의 실제 권한과 증권/원장 출처는 별도 `contract_amount` 필드이며 산식 없는
예상액으로 승격하지 않는다. 단위가 미지원인 식은 값으로 위장하지 않고 원문 식을 유지한다.

`expenses.py`/`expense_projection.py`는 같은 transaction의 실제 영수증 행·버전으로 보장 확인,
제외, 보장 검토, 미확정 비용을 통화별로 구분한다. 기존 private context의 전체 확인 비용
합계에 제외 비용까지 들어가던 의미를 v2 계산에서는 사용하지 않는다. 확인된 보장 부분만
계산하며 미상은 0원이 아니다. 다른 비용이 남으면 `FORMULA`와 `partial_amount`를 제공하고,
완전한 등록 비용 집합은 `REGISTERED_COSTS`로 표시한다. 이는 전체 수령액의 확정이나 하한이
아니다. 부분 비용으로 전체 비용 조건을 `NO_MATCH` 처리하지 않는다. 비용 digest를 guidance
stale 입력에 포함하고 저장된 과거 결과는 그대로 유지한다.

검증은 2026-09-09 03:04~03:08 KST, source `b49beee`와 위 사건/trace/비용 관련 미커밋 변경에서
실행했다. 이후 지급 경우/시나리오 변경의 결과로 재사용하지 않는다.

- 비용 입력 계약 부재 RED 12개 후 새 비용 계산 테스트를 추가했다. 테스트의 Decimal 및
  range DSL 형식을 바로잡은 뒤 부분 비용이 후보를 지우는 RED도 확인하고 수정했다.
- `python -m pytest apps/api/tests/test_guidance_expense_estimates.py
  apps/api/tests/test_guidance_expenses.py apps/api/tests/test_guidance_local_event_engine.py
  apps/api/tests/test_guidance_calculation_provenance.py apps/api/tests/test_local_guidance.py
  -q --tb=short`: **81 passed**(0.55초), guidance mypy **22 source files** 통과.
- `python -m pytest apps/api/tests/test_guidance_expenses_integration.py
  apps/api/tests/test_semantic_local_guidance_integration.py
  apps/api/tests/test_operational_local_guidance.py apps/api/tests/test_local_guidance_integration.py
  apps/api/tests/test_guidance_code_scope_integration.py -m integration -q --tb=short`:
  전용 합성 test DB에서 **16 passed**(23.55초). 첫 실행은 test 전용 URL 변수 누락으로 수집
  전에 안전하게 거부되었으며 결과 없음이다. 올바른 `FAMILYCARE_TEST_DATABASE_URL`로 실행했다.
  실제 receipt CRUD·RR·가정/사건 거부·실패 복구, 비용 수정의 stale 및 과거 snapshot 보존,
  기존 원문 300/400 분석과 코드 metadata를 함께 검증했다.

독립 runtime 구현 `34ef6eb`(원 커밋 `ca10b9c`)은 명시적 `scenario_inputs`에서 같은 값·단위·
실제 EVENT_SCENARIO UUID/version/digest가 일치하는 예정 입원 일수만 계산한다. 기본 신뢰 목록을
확장하지 않고 `SCENARIO_ASSUMPTION`을 trace에 유지한다. 별도 worktree의 `b49beee`+runtime/test
두 파일에서 관련 순수 **97 passed**(0.58초), Ruff/mypy/diff 검사를 03:17 KST에 통과했다.
주 안내의 시나리오/원문별 지급 경우 통합과 새 계약 생성·Web·전체 필수 검사는 이어 진행한다.

## 원문별 지급 경우와 예정 일수의 통합

각 원문 root의 규칙/산식을 `GuidancePayoutCaseInput`으로 유지하고, 동일 canonical 후보의
`cases` 안에서 각각 활동·관련성·조건·금액을 평가한다. A의 분류가 맞고 B가 틀려도 A를
지우지 않는다. 분류 원문이 계산 root에서 이미 참조된 경우는 원문 주소와 조건 계약을 함께
비교해 중복을 제거한다. 별개 원문은 같은 문구여도 유지한다. 코드 체계/판본이 같은 필수
분류 집합의 배타성이 검증되고 각 식이 같은 통화/가정으로 계산될 때만 `SOURCE_ALTERNATIVES`
범위를 제공하며 `SOURCE_CASE_APPLIES` 가정을 붙인다. 동시 지급·합산 권한으로 사용하지 않는다.

예정 입원 일수는 실제 사건값을 바꾸지 않고 `scenarios`의 가설·span·실제 사건 UUID/version/
digest로 남긴다. 기본 결과는 필요한 일수와 식을 유지하고, 별도 가정 계산에서만 5일→300을
제공한다. 이미 확인된 실제 일수·다른 가족·취소된 계획은 이 가설로 덮지 않는다. 계산식의
AST 주소는 public `expression_path`로 이름을 명확히 하고 제한된 `/calculation/args/...`
형식만 허용했다. 파일 경로 금지 검사나 allowlist를 완화하지 않았다.

독립 정적 검토가 발견한 operational 산식 소실도 RED 후 수정했다. 새 semantic 조건 root가
있어도 기존 operational 산식을 별도 case로 보존하며, 교체·중복 근거 없이 버리지 않는다.
등록된 금액과 원문 benefit 종류가 충돌하면 한쪽으로 재분류하지 않는다. 가입금액 권한이
통화 권한을 대신하던 RED도 수정했다.

2026-09-09 03:23~03:33 KST, `dec52ae`와 위 guidance/계약/테스트 미커밋 변경의 증거:

- `dec52ae`의 독립 case relation helper(원 커밋 `60025bb`)는 별도 `b49beee` worktree에서
  신규 순수 **36 passed**(0.36초), Ruff·mypy·diff 통과 후 통합했다.
- 초기 사건/시나리오/지급 경우/비용/원문 suite **68 passed**(0.63초), 같은 단계의
  PostgreSQL 5개 파일 **16 passed**(24.13초), guidance mypy **25 source files** 통과.
- `python -m pytest apps/api/tests/test_guidance*.py apps/api/tests/test_local_guidance.py
  apps/api/tests/test_rule_dsl.py apps/api/tests/test_decision_api.py
  apps/api/tests/test_decision_privacy.py apps/api/tests/test_policy_ledger_contracts.py
  -q --tb=short`: **632 passed / 9 integration deselected**(5.57초).
  이는 이후 통화 권한/operational 보존 수정 전 실행이다.
- 최종 수정 후 `python -m pytest apps/api/tests/test_guidance_semantic_binding.py
  apps/api/tests/test_guidance_payout_cases.py apps/api/tests/test_guidance_calculation_provenance.py
  apps/api/tests/test_guidance_scenarios.py -q --tb=short`: **24 passed**(0.49초).
  `test_semantic_local_guidance_integration.py`와 `test_operational_local_guidance.py`를
  `-m integration -q --tb=short`로 다시 실행해 **5 passed**(12.92초) 확인했다.
- OpenAPI·decision schema·business/Web 타입을 생성하고 `python scripts/check_contracts.py`
  통과. 첫 검사에서 일반 `path` 필드가 거부되어 위의 제한된 `expression_path`로 고쳤다.
  해당 소스의 Ruff와 mypy **25 source files**도 통과했다.
- `corepack pnpm --filter @familycare/web test src/features/results/LocalGuidancePanel.test.tsx`
  **14 passed**(1.31초), Web typecheck 통과. 기존 표시와 새 optional 계약의 호환 확인이며,
  새 trace/부분 비용/시나리오/case 표시의 구현·검증을 대신하지 않는다.
- 문서 **50 files**, 안전 **969 paths**, diff 검사 통과. 실제 문서·외부 provider·운영 환경을
  사용하지 않았다. B04의 상세 UI·청구 bridge·합계·품질/성능·전체 필수 검사와 CI는 남아 있다.

## 안내 보존과 private 청구 출처

`claims/guidance_snapshot.py`와 `guidance_repository.py`는 run·사건 버전·canonical 담보
선택만 받아 저장된 후보의 조건/금액/원문별 경우/예정 시나리오/trace/비용/버전을 복사한다.
새 청구는 `preparing`이며 FORMULA·조건부 후보도 준비할 수 있다. legacy 전역 판정 성공을
새 전제조건으로 사용하지 않는다. 실제 지급은 기존 수동 상태 전환과 별도 금액에 남긴다.
SERIALIZABLE 생성은 사건을 잠그고 현재 소스 digest를 재조회한다. 같은 사건/담보의 반복·동시
요청은 기존 청구를 반환하며 스냅샷 저장 실패는 청구와 상태 기록까지 롤백한다.

migration 0057은 기존 operational FK를 nullable로 바꾸고 실제 private 계약/담보 FK를
추가한다. 두 출처 중 하나의 완전한 쌍만 허용하고 private insurer 표시를 운영 key로 만들지
않는다. 가정/대상자/계약/import의 일치는 DB trigger에서도 확인한다. 대상자 교정 후 기존
청구의 상태 변경·휴지통 이동은 원래 출처/가족/스냅샷을 보존한다. 새 출처로 바꾸거나 역사적
JSON/hash를 다시 쓰지 않는다. 새 청구가 남은 downgrade는 거부한다.

연결 전 private 청구는 현재 원문 identity proof를 재생한 canonical 연결을 통해 새 경로와
legacy Rider 요청 모두에서 재사용한다. 수동 지급 후 미래의 operational/private 계산은
같은 이력을 읽으며, 지급 전·동일 사건·다른 가족은 제외한다. 기록 부재는 0으로 확정하지
않는다. 지급 이력 digest가 바뀌면 이전 안내는 stale로 표시되고 원래 청구 금액은 유지된다.
HTTP/neutral 계약은 실제 출처와 불완전/혼합 참조 거부를 유지하며, 새 local snapshot 부분은
Pydantic 원본에서 `--write-claim-schema`로 생성한다. 기존 neutral 예시는 변경하지 않았다.

2026-09-09 03:40~04:10 KST, `6dd7108`→`3af20ed`와 claims/migration 0057/history reader/
API·neutral schema 및 관련 테스트의 미커밋 변경에서 선택 검증했다. B04 PYTHONPATH,
`TMPDIR=/tmp`, 전용 합성 PostgreSQL과 destructive-test guard를 사용했고 Web 검사는 직렬이었다.

- 처음 새 claim 메서드/스냅샷 계약 부재를 RED로 확인했다. legacy stale 전제조건과 적용 전
  schema 때문에 실패한 중간 PG 실행은 수정 뒤 재검증했다. private 미래 지급 이력 누락과
  대상자 교정 후 휴지통 이동 503도 각각 RED→GREEN으로 확인했다. 전용 DB의 0056↔0057
  migration은 새 private 기록이 없는 합성 상태에서 왕복했다.
- `python -m pytest apps/api/tests/test_guidance_claim_concurrency_integration.py
  -m integration -q --tb=short`: 처음 5개 **5 passed**(11.96초). 실제 잠금 대기 중인 두 생성
  transaction, 두 출처의 snapshot INSERT trigger 실패/재시도와 canonical 재사용을 검증했다.
  테스트 커밋 `af6dda7`의 원 커밋은 `f939a10`이다.
- 후속 `3af20ed`(원 `7f7e120`)의 지급 이력 테스트는 operational None/private 1 RED 후
  reader 수정으로 GREEN이었다. 별도 worktree의 같은 root reader/claims 미커밋 소스에서
  전체 **6 passed**(16.41초), 같은 사건 날짜를 지급일 뒤로 교정한 추가 조건도 해당 테스트
  **1 passed / 5 deselected**(7.08초)였다. 두 context의 count/digest와 과거 JSON/hash를 검증했다.
- `python -m pytest apps/api/tests/unit/claims apps/api/tests/test_claim_workflow_api.py
  apps/api/tests/test_claim_history.py apps/api/tests/test_claim_state_machine.py -q --tb=short`:
  응답의 불완전/혼합 출처 4개 RED 후 **79 passed**(3.32초). private 도메인·neutral 계약의
  성공/거부, 선택된 안내의 깊은 불변성과 legacy API/상태/지급 회귀를 포함한다.
- `python -m mypy apps/api/src/familycare_api/claims
  apps/api/src/familycare_api/guidance/repository.py
  apps/api/src/familycare_api/guidance/private_adapter.py
  apps/api/src/familycare_api/decisions/knowledge_repository.py`: **14 source files passed**.
  생성된 claim schema/OpenAPI/Web에 대한 `python scripts/check_contracts.py`도 통과했다.
  이후 추가한 실제 HTTP private 청구 경로와 canonical source 병합의 검증은 별도 후속 증거다.

상세 금액 UI `6b1e2d8`(원 `df21ff2`)는 계약금액 권한·네 비용 그룹·부분 계산·시나리오·지급
경우·접힌 trace를 표시한다. 별도 worktree의 해당 변경에서 신규 6개 RED 후 panel **23 passed**,
Web typecheck·ESLint·format/diff 통과했다. jsdom 검증이며 실제 브라우저의 native Enter와
320px 화면 검증은 남아 있다. `6dd7108`의 정액 합산 helper는 순수 **35 passed**와 Ruff/mypy를
통과했으나 아직 주 엔진/계약/UI에 연결되지 않았다. 청구 UI bridge, canonical 출처 병합,
합계·dev/holdout 품질/성능, B04 전체 필수 검사/CI는 진행 중이다. 실제 자료·외부 AI·운영
환경·Windows/모바일·태그·릴리스·배포는 이 단계에서 수행하지 않았다.

04:11~04:14 KST 후속 검증은 같은 `3af20ed` + 위 미커밋 변경 및 실제 private HTTP 테스트에서
`python -m pytest apps/api/tests/test_local_guidance_integration.py
apps/api/tests/test_semantic_local_guidance_integration.py
apps/api/tests/test_guidance_claim_concurrency_integration.py
apps/api/tests/test_claim_workflow_integration.py -m integration -q --tb=short`를 실행해
**24 passed**(51.35초)였다. private 청구 생성 201/no-store·클라이언트 금액 422를 확인했다.
private 청구가 남은 downgrade 거부와 기존 스냅샷 보존 assertion 추가 후 해당 파일의
`-k 'not storage and not upgrade and not fallback'` 재실행도 **1 passed / 3 deselected**
(3.15초)였다. 최초 `-k default`는 선택된 테스트가 없어 결과 없음이다. 관련 Ruff lint와
format **19 files**가 통과했고 문서 **50 files**, 저장소 안전 **977 paths**, diff도 통과했다.

04:15~04:25 KST 후속 변경 `93589eb`와 `f4bc51c`는 canonical 출처별 평가와 청구 화면을
연결했다. 같은 담보의 private 규칙이 무관하더라도 operational 원문의 지급 경우를 가리지
않는다. 각 원문의 계약금액·현재성·조건·산식은 해당 경우에 남고, 충돌하는 출처의 금액을
서로 상속하거나 합산하지 않는다. source 선택 revision도 입력 digest에 포함한다. 별도
worktree에서 실제 관련/무관 두 context의 RED 후 관련 순수 테스트 **108 passed**(0.88초),
Ruff 6개 파일·mypy 5개 파일을 통과했다. 이 검증은 PG 연결 전체 검증을 대신하지 않는다.

결과 화면은 선택된 안내 run·사건 버전·담보 참조로 청구를 시작한다. 청구 화면은 저장된
가입금액·경우·시나리오·조건·trace·비용을 보여주고 수동 실수령 이력과 구분한다. private
청구의 null operational ID와 보험사 표시, stale 안내·로그아웃·늦은 응답을 처리했다.
별도 worktree의 실제 변경에서 청구 시작/페이지/저장 상세/source metadata RED 후
`LocalGuidancePanel`, `result-page`, `claims` 관련 **65 passed**(5.54초), TypeScript·ESLint·
Prettier·Vite/PWA build가 2026-09-08 19:23 UTC에 통과했다. root 통합 이후 전체 Web와
브라우저 검증, 합계의 API/저장/UI 통합은 후속 검증한다.

04:28~04:36 KST, `f39fe80`(원 `e4cd523`)과 root의 subtotal DTO/엔진/생성 계약 변경에서
정액 소계를 연결했다. 실제 사건과 정확히 같은 예정 치료 가정은 각각의 문맥으로 묶는다.
동일 가정의 전체 값·출처·원문 범위와 사건 버전/trace를 대조하며 예정 일수를 실제 사실로
승격하지 않는다. 출처 중복, 같은 계약의 여러 담보, 서로 다른 지급 경우, 실손, 통화,
미완성 계산은 명시적인 제외 사유를 남긴다. 합산은 독립 지급/공통 한도 없음의 미확인
전제를 포함 항목별로 보존하며 가능한 항목만 있으면 부분 소계로 표시한다. 시나리오 확장은
32개 문맥·1,000개 후보 선택·2,048개 원시 기록으로 제한하고 초과 시 실제 사건 소계와
개별 후보를 유지한다. 합산 오류도 개별 결과를 지우지 않는다.

- 예정 입원 두 계약의 typed 응답은 0개 소계 vs 기대 600 RED 후, helper 통합으로 통과했다.
  `python -m pytest apps/api/tests/test_guidance_subtotal_response.py
  apps/api/tests/test_guidance_subtotals.py apps/api/tests/test_guidance_scenarios.py
  apps/api/tests/test_guidance_local_event_engine.py
  apps/api/tests/test_guidance_context_combination.py -q --tb=short`: **94 passed**(1.12초).
  실제 300/부분 300/예정 600 직렬화와 합산 실패 격리를 포함한다. helper 원 worktree의
  해당 helper/시나리오/경우 검증은 별도로 **67 passed**(0.91초)였다.
- `python -m mypy apps/api/src/familycare_api/guidance`: **26 source files passed**.
  관련 Ruff lint/format 및 `python scripts/check_contracts.py`도 통과했다.
- `341d153`(원 `e07b786`)의 소계 UI는 금액·전제·포함 담보·누락 사유를 표시한다.
  별도 worktree에서 소계/Panel **39 passed**(신규 11), TypeScript·ESLint·Prettier·Vite/PWA
  build가 19:34:51 UTC에 통과했다. 기본 HTML disclosure의 Enter 동작과 좁은 브라우저의
  청구 연결은 이후 Playwright 검증으로 확인한다.

같은 소스와 미커밋 범위, WSL·Python 3.14·외부 AI 없는 순수 context/엔진에서
`python scripts/run_claim_guidance_benchmark.py --engine local --repetitions 20`을 실행했다.
고정 dev 10개/holdout 10개에서 재현율은 각각 **9/9, 10/10**, 주요 정밀도 **6/6, 7/7**,
조건부 정밀도 **3/3, 3/3**, 불필요 보류 **0/10, 0/10**, 무관 사건 오추천 **0/3, 0/3**이었다.
400회 context 생성/엔진 시간은 p50 **0.612ms**, p95 **1.205ms**였다. 원문·자연어·DB·HTTP·
전체 카탈로그·실제 자료의 정확도나 응답시간을 뜻하지 않는다.

비교 실행 `--engine legacy --repetitions 20`은 새 목표에 대해 exit 1이었다. dev/holdout
재현율 **8/9, 9/10**, 주요 정밀도 **0/0(null), 1/1**, 조건부 정밀도 **3/8, 2/8**,
불필요 보류 **1/10, 1/10**, 무관 사건 오추천 **0/3, 0/3**이다. 400회 p50 **0.226ms**,
p95 **0.314ms**였다. 고정 정답/분모는 수정하지 않았으며 역사적 결과의 호환성을 바꾸지 않았다.

04:36~04:37 KST, `f39fe80` + 같은 root API/계약 변경에서
`python -m pytest apps/api/tests/test_local_guidance_integration.py
apps/api/tests/test_semantic_local_guidance_integration.py
apps/api/tests/test_guidance_claim_concurrency_integration.py
apps/api/tests/test_claim_workflow_integration.py apps/api/tests/test_guidance_amount_source.py
-m integration -q --tb=short`는 **26 passed / 20 deselected**(49.47초)였다. 전용 합성 DB와
destructive guard를 사용했다. source별 경우와 새 소계 필드를 포함한 원문→300/400·이전 300,
private/operational 청구·canonical 이력·동시 생성·저장 롤백·금액 출처를 재검증했다.

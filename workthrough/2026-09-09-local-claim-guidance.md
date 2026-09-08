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

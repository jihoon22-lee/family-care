# v0.5 B04: Local candidates and explainable estimates

- 상태: in_progress
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP05 #65](https://github.com/jihoon22-lee/family-care/issues/65), [WP06 #66](https://github.com/jihoon22-lee/family-care/issues/66)
- 구현 기반: B03 [PR #78](https://github.com/jihoon22-lee/family-care/pull/78), merge `3f4a47cc2d1b4e649d202850020b94882dc4adfe`, 필수 CI 7/7 통과.
- 관련 요구사항: R01/R06/R07/R08/R09/R10/R11/R12/R13/R17/R19
- 선택 AI 검수는 B05 #67에서 연결하며 이번 기본 분석은 외부 요청 0건이다.

## Tasks

1. completed — 원장/private 공통 안내 context와 semantic 출처/버전 계약을 연결했다. 실제 Rider→Clause→사건일 약관→원문 root와 가입금액 출처를 대조하고, private import 없이 합성 원문의 300/400 계산·과거 300 보존을 확인했다.
2. in_progress — 제한된 로컬 사건 해석에 동의어·부정·예정/실시·가족/시점 범위를 반영한다. 명시 사실·파생 사실·가정·미상을 구분하고 판본이 없는 분류 코드를 임의 확정하지 않는다. 입원·부정·가족 이름/별칭과 명시적 코드 체계/판본은 연결했고 수술 activity·시나리오 연결을 이어 진행한다.
3. pending — 가입/대상자/사건일과 관련성·개별 조건을 분리한다. 무관 후보·결정적 불일치는 제외하고 미확인 최신성만으로 전체 후보·금액을 보류하지 않는다. 필요한 사건 질문과 로컬 설명을 보존한다.
4. pending — 근거 있는 정액/실손 산식, Decimal trace·부분 비용·시나리오·합계를 연결한다. 동일 canonical 담보만 중복 제거하고 복수 실손·다른 통화·상호배타 가정을 합산하지 않는다.
5. pending — API/UI/snapshot과 stale 판정을 새 계약으로 연결한다. 과거 청구/실수령/계산 snapshot을 보존하고 사용한 사건·가입/연결·원문·지식·가정 revision으로 재현한다.
6. pending — 합성 원문→가입 담보→실제 분석 응답의 300/400·과거 300 보존, 거부/부분 계산·AI-off·동시성과 dev/holdout 품질/성능을 검증한다. 전체 필수 검사·CI 후 B04를 통합한다.

## First vertical acceptance

private import 없이 합성 증권의 실제 Rider와 승인된 Clause 연결을 사용한다. 사건일에 적용되는
판본과 완전한 원문 구역을 다시 검증하고, B03의 별표/각주 산식에 가입금액 100·입원 5일을
넣어 300을 반환한다. 원문 각주 변경 후 새 분석은 400이며 과거 run/청구는 300을 유지한다.
다른 가족·미가입·인접 조항·다른 판본은 같은 이름이더라도 연결하지 않는다.
키 미설정·provider 호출 금지 상태에서 실제 API 응답과 저장 snapshot을 확인한다.

## Boundaries

semantic citation은 publication과 원문 주소를 가진 별도 출처다. 기존 Evidence/약관 section의
UUID로 가장하지 않는다. B03 executable은 일부 규칙/산식의 실행 가능성이며 가입 사실이나
모든 조건 일치를 뜻하지 않는다. latest partial과 같은 source의 last usable을 구별한다.
실제 자료·추가 원문 형식·운영 전환 수용은 #62/#63/#69에 남아 있으며 합성 검증으로 대체하지 않는다.

# v0.5 B01: Local guidance foundation

- 상태: in_progress
- 메인: [#59](https://github.com/jihoon22-lee/family-care/issues/59)
- 요구사항: [#60](https://github.com/jihoon22-lee/family-care/issues/60), R01/R07/R09/R10/R18/R19/R20
- 구현: [WP01 #61](https://github.com/jihoon22-lee/family-care/issues/61)
- 기준 소스: `77bc1cd9946328f28f2d36cbae15ffccdbc5d41e`
- 설계: [v0.5 local guidance](../../design/v0.5-local-first-claim-guidance.md)

## Tasks

1. complete — 개발/holdout 각 10개 합성 평가셋·부정 대조군·목표와 새 응답/전환 계약을 고정했다.
2. complete — 상태 가정·가입/관련성·산식·통화·기록 없는 지급 횟수 회귀의 RED/GREEN을 확인했다.
3. complete — API/영속 snapshot/최소 Web 소비자를 연결하고 기본 조회의 AI 예약/구조화 요청을 분리했다.
4. complete — 합성 API/PostgreSQL round trip, 키 없는 조회, JSON 무결성, 0024→0025 기존 결과 보존을 확인했다.
5. in_progress — 전체 필수 검사·브라우저 E2E·CI·리뷰 후 PR을 merge하고 실제 증거를 연결한다.

## Boundaries

기존 v1/v2 snapshot은 재계산하지 않는다. 문서상 가입/반대 증거는 유지하고 최신성 가정과
조건·계산 준비를 분리한다. 금액은 검증된 식으로만 계산하며 가입금액 fallback은 새 안내에서
사용하지 않는다. 실제 자료 처리·자동 연결·전체 약관 구조화·복수 보험 합산·선택 AI 검수·완성 UI는
각 후속 WP에서 이어간다. 초기 데이터 전환은 nullable 새 snapshot과 dry-run 호환성으로 시작한다.

## Early evaluation contract

합성 정답집의 개발/holdout 계약 그룹을 분리하고 엔진 튜닝 전에 목표를 고정한다.
기본 후보 재현율 >= 0.90, 주요/조건부 정밀도 각각 >= 0.90, 무관 사건 오추천율 <= 0.05,
답할 수 있는 사례의 불필요 보류율 <= 0.05를 사용한다. 모든 후보 반환·전부 보류·무관 후보의
조건부 배치 대조군은 실패해야 한다. 이는 합성 지원 범위 목표이며 실제 보험 정확도 주장이 아니다.
기본 조회 외부 요청/AI 작업 예약은 0, 정확한 합성 식의 Decimal 결과는 정답과 일치해야 한다.
규모·표본·baseline·실행 환경과 성능은 평가셋을 먼저 작성한 뒤 기록하고 해당 입력을 고정한다.

## Requirement and scenario handoff

아래는 B01의 실제 연결과 후속 수용 소유자다. 부분 구현을 전체 요구사항 PASS로 계산하지 않는다.
소스/검증 환경/PR은 이 묶음의 workthrough에 연결한다.

| 요구사항 | B01 증거 또는 후속 소유자 |
|---|---|
| R01 | `test_local_guidance.py`, API/PostgreSQL 대표 답변, `LocalGuidancePanel`; WP05/08/10에서 확장 |
| R02 | WP02/09: 기보관 자료 대사·재처리·전환 |
| R03 | WP02/04: 문서 구조와 부분 실패 |
| R04 | WP03: 가입 근거와 자동 연결 |
| R05 | WP04: 상세 조건·별표·근거 지식 |
| R06 | B01의 검증된 단일 DSL 계산과 부분 지원; WP04/05/06 확장 |
| R07 | 기본 AI 예약·Web 자동 구조화 제거, 키 없는 합성 API; 사건 해석은 WP05 |
| R08 | WP05/07: 부정·예정·대상자·시간 해석 |
| R09 | 개발/holdout 후보 지표와 부정 대조군; WP05/10 확장 |
| R10 | 문서 가입·최신성·조건·금액·지원 범위 독립 응답 |
| R11 | 사건/영수증/횟수 질문 경로만 노출; WP02/05/08 확장 |
| R12 | 산식 없는 가입금액 fallback 제거, 누락·통화 불일치는 FORMULA; WP06 확장 |
| R13 | 출처 포함 공통 담보 참조; 실제 중복 제거·합산은 WP03/06/08 |
| R14 | WP07: 선택 검수와 관련 원문 누락 탐색 |
| R15 | 기본 검수 NOT_REQUESTED; 선택 job·예산·늦은 결과 격리는 WP07 |
| R16 | 최소 후보·예상액 UI; 청구 초안 연결은 WP08 |
| R17 | event/catalog/rule/status/assumption 버전과 immutable snapshot; 문서 증분 무효화는 WP03~09 |
| R18 | 0024→0025 기존 결과 보존과 nullable 병행 저장; 전체 generation/복원은 WP09 |
| R19 | 기존 가정 scope/응답 개인정보 검사 유지, 합성 데이터·격리 DB·외부 요청 차단 |
| R20 | 고정 평가셋·baseline·음성 대조군·실제 실행 기록; 최종 집계는 WP10 |

| 시나리오 | B01 수용 층과 후속 범위 |
|---|---|
| S01/S02 | 문서 유지·미가입 제외: 엔진 및 S01 API/PostgreSQL |
| S03/S04 | WP02/03/04: 원문 재처리·판본 자동 대조 |
| S05 | 구조화된 예정/부정 fixture만 평가; 자연어 해석은 WP05 |
| S06 | 키 없는 API·HTTP transport 차단·AI queue 0·Web mock E2E |
| S07/S08 | 고정 산식·누락/통화 방어 회귀; 상세 계산 정답 corpus는 WP06 |
| S09 | 별개 계약 fixture 유지; 두 source 중복 제거·합산은 WP03/06 |
| S10/S11 | 무관/다른 가족/현재 해지와 과거 active/시점 미확인 회귀 |
| S12/S13 | WP07: 실제 검수 차이·실패·늦은 응답 |
| S14 | 합성 migration·구 snapshot 보존; 실제 자료·중단 재개·복구·기기는 WP09/10 |

기존 기대값 변경은 최신 상태 전역 gate와 가입금액 fallback을 새 안내에 적용하지 않는 것,
분석 시 AI를 자동 예약하지 않는 것, optional 안내 필드를 허용하는 것에 한정한다.
과거 v1/v2 fixture의 의미와 개인정보 금지 필드 검사는 유지한다. 가정 식별자는 응답에 포함하지
않으며 owning decision row와 scoped repository가 소유 범위를 보장한다.

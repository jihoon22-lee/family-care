# v0.5 B03: Detailed terms knowledge and compilation

- 상태: in_progress
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP04 #64](https://github.com/jihoon22-lee/family-care/issues/64)
- 코드 기반: [B02 PR #76](https://github.com/jihoon22-lee/family-care/pull/76), merge `bc727928a0030332452fb7b3bf639beba6b9e60e`
- 관련 요구사항: R03/R05/R06/R12/R17/R19, S03/S04/S07/S08/S11

## Tasks

1. completed — 원문 인용·판본/추출 식별자, 타입 있는 정의/조건/산식/별표/각주와 의존/override 관계의 중립 계약을 만든다. 원문 구조·의미 지식·실행 규칙을 구분하며 bounded closure와 단위/통화/반올림 검증으로 기존 DSL을 compile한다. 누락·순환·다른 판본과 미지원 조건은 영향 root에 남기고 나머지 설명/계산을 보존한다.
2. in_progress — 원문 구역의 전체 처리 계획, 조항 계층·국소 예시/각주 범위와 이어지는 구역을 연결한다. Worker 후보의 인용뿐 아니라 필드 의미·숫자/단위·구역/참조 관계를 API가 독립 검증한다. 짧은 발췌나 모델의 완료 선언을 전량 지식화 증거로 사용하지 않는다.
3. in_progress — 후보/검증/compiled root의 불변 저장과 현재 조회, 실제 원문 재대조, root별 부분 성공·실패/재시도·동시성과 마지막 정상 결과 보존을 연결한다. legacy 지식을 새 사용자 확인으로 가장하지 않는 adapter를 제공한다.
4. pending — 공유 정의/별표의 변경 영향을 의존 root에 전파하고 의미 재사용과 새 source proof를 구분한다. 사용자 교정·기존 계약/담보·청구 snapshot을 유지하며 로컬 질의/계산 reader에 인계한다.
5. pending — 같은 합성 문서로 주규칙→별표/각주→compiler→저장/조회→로컬 계산을 검증한다. 전체 필수 검사·통합/브라우저 경계·CI와 보호된 수용 결과를 구분해 기록하고 B03을 통합한다.

## B02 acceptance handoff

B02는 전량 보존/가입·출처·판본/사건일 연결 기반을 CI 7/7과 Chromium mock E2E 17개 후 통합했다. #62/#63은 추가 원문 형식·조항/예시 범위·문서 신원과 보호된 수용을 추적하는 열린 이슈다. 이 코드 인계가 두 WP 전체 수용 완료를 의미하지 않는다.

보관 추출 대사에서는 원문 이용 가이드 표시 설명을 구별했으나 실제 약관/보험사·상품·판본 분류 수용이 남았다. 국소 예시와 다음 조항의 계층/범위는 이 WP의 원문→지식 경로와 함께 보완한다. 실제 기존 자료·공통 출처 연결과 전체 운영 cutover는 #69가 조립한다. 운영 변경과 추가 AI 호출은 승인된 최소 범위에서 별도 실행 증거를 남긴다.

## First vertical acceptance

합성 일당 산식·원래 판본의 분류표·최초 2일 제외 각주·10일 한도로 가입금액 100과 입원 5일에서 300을 계산한다. 각주를 1일로 바꾸면 의존 root만 400으로 바뀌고 무관 root와 과거 snapshot은 유지한다. 가짜 인용, 숫자/단위 역할 교환, 누락/순환/다른 판본 참조, 임의 코드와 불완전한 처리의 거부를 같은 경로에서 검증한다. 실제 가입금액을 산식 없이 지급액으로 사용하지 않는다.

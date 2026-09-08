# B03 detailed terms knowledge

[WP04 #64](https://github.com/jihoon22-lee/family-care/issues/64)의 약관 원문→의미 지식→기존
DSL 경로를 구현 중이다. B02 기반은 PR #76 merge `bc727928a0030332452fb7b3bf639beba6b9e60e`다.
현재 기록은 compiler와 원문 검증/저장 기반이며 Worker·현재 질의 adapter·전체 수용 완료는 남았다.

## Changes

- `terms-semantic-knowledge.v1.schema.json`에서 API/Worker의 strict 소비자를 생성한다.
  조건·분류 판본·정액/비율/일당·공제·한도·각주와 DEPENDS_ON/OVERRIDES를 보존한다.
  compiler는 bounded closure와 단위/통화/반올림을 검사하고 기존 data-only DSL을 만든다.
  누락·순환·판본 불일치·미지원 계산은 영향 범위에 남긴다. 횟수 미만은 기존 `count_before`의
  실제 의미(이상)를 `not`으로 반전하며 누락 횟수를 0으로 바꾸지 않는다.
- source layout은 원래 manifest/active layer/native lineage/쪽수·주소와 연속 구역을 검사한다.
  명시적 별표·각주·종료된 예시를 보존하며 예시/표제의 처리 완료를 규칙 권위로 바꾸지 않는다.
  원문 문장 전체의 의미·수치 역할·참조 증거를 독립 검사한다. 지급일수 조정→총액→공제와
  0 하한→금액 한도→총액 반올림 순서를 원문에서 입증하지 못하면 계산을 제한한다.
- `0052_terms_semantic_knowledge`는 scoped source input, 후보, 검증 결과와 root 이력을
  append-only로 저장한다. 원문·판본·추출과 취소 상태를 잠금 안에서 재조회한다. 현재 reader는
  원문/규칙을 다시 검증하며 저장된 VERIFIED/executable 선언을 권위로 쓰지 않는다. 최근
  시도 조회와 마지막 정상 root 조회를 나누어 다수 실패 후에도 정상 결과를 보존한다.
- 정액 상수 산식은 입력 필드가 없어도 저장·반환할 수 있다. 실제로 참조한 입력과 선언의
  일치는 계속 검사한다. 검토 화면은 이 경우 ‘추가 입력 없음’을 표시한다. neutral snapshot
  계약과 OpenAPI를 재생성했으며 새 외부 HTTP endpoint는 아직 등록하지 않았다.

## Verification to date

2026-09-08 21:03~21:08 KST, 소스 `a303fa5`에 위 DSL·원문 의미/검증·0052/repository·
계약/UI와 관련 테스트/문서 변경을 더한 상태에서 실행했다. root별 마지막 정상 조회·취소,
malformed audit row와 위조 상태를 포함한 PostgreSQL 검사는 전용 합성 DB와 파괴적 검사 guard,
`TMPDIR=/tmp`를 사용했다. 운영 DB는 쓰지 않았다.

- 기본 `TMPDIR=/tmp uv run pytest -q`: **2,712 passed / 528 deselected / 3 subtests** (23.16초).
  이후 추가한 audit-row 테스트 2개는 integration 범위다. 최초 전체 실행은 새 인용 ID 회귀
  1개 실패/2,711개 통과였으며 수정 뒤 위 결과를 얻었다.
- `pytest apps/api/tests/test_terms_knowledge_repository.py -m integration -q`: **11 passed**
  (13.66초). 같은 합성 약관의 원문→compiler→불변 저장→재조회에서 100×(5−2)=300,
  잘못된 각주 후 무관 root·기존 정상 결과 보존, 65회 실패 이후 조회, 가정 거부·입력 변경,
  취소 generation, 중복 동시 처리와 이력 변경 거부를 확인했다. 초기 fixture 오류 7개 실패는
  수정했고 취소/마지막 정상 조회 결함은 RED 2개 후 수정했다.
- source/semantic/DSL 관련 pure **123 passed** (1.01초) 후 설명의 가짜 citation ID 제거를
  RED로 추가했고 최종 전체 suite에 포함했다. compiler의 독립 변경은 순서대로
  `ae41f58`/`bc398c0`/`a303fa5`, 원문 layout은 `aaece9f`로 통합했다.
- 0052 upgrade 및 빈 합성 DB의 0051 downgrade→head upgrade, generated contract 검사,
  관련 Ruff lint/format와 terms 모듈 mypy **6 files**가 통과했다.
- Web 검토 component는 새 표시가 없어서 RED 1개/기존 10개 통과를 확인한 뒤
  **11 passed** (1.58초)를 얻었다. 이는 이번 변경의 전체 Web/build/E2E 증거가 아니다.

기본 Python 성공은 전체 PostgreSQL·Web·브라우저·Worker 통합 성공을 의미하지 않는다.
전체 필수 검사, 비동기 source range/후보 경로, 원래/새 원문 각주 변경의 통합 변경 영향,
현재 질의/legacy adapter와 과거 snapshot 보존의 수용을 이어서 수행한다.

실제 자료·식별자·provider 결과를 코드/fixture/로그에 넣지 않았다. 이 B03 단계에서 실제
자료 접근·OpenAI 호출·운영 쓰기·태그·배포·실제 Windows/모바일 검증은 수행하지 않았다.
B02의 보호된 수용과 전체 기존 자료 cutover는 #62/#63/#69의 열린 범위로 유지한다.

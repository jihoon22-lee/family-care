# 약관 원문 줄 근거의 일관성

원문 단어에서 줄을 만들 때는 첫 단어의 높이/수직 위치를 기준으로 삼았지만 약관 근거
검증기는 직전 단어를 기준으로 삼았다. 그 결과 생성기가 만든 정상 줄과 실제 지급 조항을
버리고, 반대로 생성기가 허용하지 않는 누적 위치 이동을 받아들일 수 있었다.

Worker 검증을 생성기의 기준과 일치시키되 직전 단어와의 순서·수평 간격, 전체 문자 span,
source layer와 합친 bbox 검사는 유지한다. 새 metadata v9/0067은 동일 원문 세대에 새
증거를 추가하고 v1–v8 기록을 보존한다. API의 독립 검증과 후속 약관 source reader에도
revision별 기준을 전달한다. 과거 검증 결과를 새 기준으로 다시 쓰거나
사용자 편집/약관 연결의 supersession 보호를 우회하지 않는다.

- Worker `b6f9cfb`(통합 `5da1ffd`): 실패 6건/통과 5건을 먼저 확인한 뒤 11건 통과.
  관련 약관/줄 검사 81건 통과(1.02초), scoped mypy·Ruff·안전·diff 검사 통과.
- v9가 없는 동일 세대의 v8 재처리에서 새 작업을 만들지 못하는 PostgreSQL 실패 1건
  (2.36초)을 먼저 확인했다. 새 proposal/publication append, 동일 원문 세대와 과거 이력
  보존, 반복 실행과 v9 이력 downgrade 거부를 통합 완료 후 검증한다.
- 공유 계약의 revision enum과 range evidence 요구를 갱신하고 공식 generator로
  API/Worker 소비자를 재생성했다. API/Worker runtime fence는 0067을 요구한다.
- 저장된 JSON 좌표 배열이 줄의 tuple bbox와 다르다고 판정되는 추가 문제도 확인했다.
  실제 metadata `_pages` 복원 경로를 쓰는 합성 JSON 왕복으로 지원 조항이 AMBIGUOUS가
  되는 실패를 재현했고, 좌표 값으로 비교하도록 정규화했다. 초기 fixture import 오류는
  이 기능 실패 확인 전에 수정했다. 원문/IR 값과 identity는 변경하지 않는다.

PR84 개인정보 변경 `7372592`·`caf2f2f`를 PR85 기준 `f880ab5` 위에 통합했다.
최소화 v3·retained 처리 v2와 `0066_provider_privacy_revision`을 보존하고 약관 migration을
`0067_metadata_lineage`로 옮겨 `0065 → 0066 privacy → 0067 metadata` 순서로 연결했다.
metadata/검증기 v9는 유지한다. readiness fixture는 과거 0066 privacy와 미래 합성 0068을
거부하며, metadata 이력 downgrade 테스트는 바로 앞선 0066 privacy를 대상으로 한다.

이번 통합에서는 수정 코드·migration 연결·revision 참조를 정적으로 검토하고
`git diff --check` 및 `git diff --cached --check`를 실행했다. 기준은 개인정보 커밋을
통합한 `8b576ef`와 migration·runtime fence·readiness/metadata 테스트·설계·007·이 문서의
후속 미커밋 변경이다. 이는 앞서 기록한 단위 검사나 PostgreSQL 결과를 다시 실행한 것이
아니다. 공유 합성 DB가 0066 privacy인 상태이므로 이번에는 Python/PG/전체 검사와 실제
자료·외부 provider 접근을 실행하지 않았다. 병합 전 0067 upgrade, MR9 이력 보존과
downgrade 거부, API/Worker readiness, retained 개인정보 회귀 및 전체 필수 검사가 남는다.

전체 필수 검사·통합 API/약관 source 검증·보호된 재처리·릴리스/운영 전환은 진행 중이다.
보호 진단의 실제 본문·개인정보·수치는 저장소 밖에만 보존한다.

# Canonical source citations and cited Rider names

- 상태: in_progress
- 범위: #63/#69, B02 Task 3/4
- 기준: PR #103 source `45aa2363fd27837a8abc17725e7d63ba2c68620a`

## Concrete source findings

PR #103의 승인 격리 DB 0078에서 같은 6개 담보의 통화를 복구했다. HTTP 2회, 기존
추가 예산 누계 8회/USD 0.1778952이며 원래 처리·후보·가입·게시·청구 이력을 보존했다.
인증된 API의 금액·통화 조회와 native 독립 금액 증명 소비 6/6을 확인했고 조회 HTTP는 0이다.

같은 원문의 실제 표제, 이름/금액/통화 6행, 각 physical page와 대상자를 직접 대조하여
한 private 계약의 동일 alias를 현재 PDF에 선언했다. 경쟁 private/native 계약은 없었으며,
옛 PDF bytes hash를 복원했다고 주장하지 않는다. 기존 manifest 적용으로 source binding
1개를 추가했지만 canonical은 예상 6개 중 2개만 생성됐다. 3개 담보는 이름 인용에 포함된
primary 표 헤더를 별도 이름 위치로 세는 코드 때문에 제외됐고, 나머지 1개는 원문 전체
이름 위치 유일성 검사를 통과하지 못해 원인을 분리한다. 최초 적용은 기대 개수 미달로
실패로 기록하고 이미 추가된 유효한 binding/2개 연결을 삭제하거나 완료로 바꾸지 않는다.

해당 private 계약 등록 담보 9개 중 native 미등록 3개도 따로 대조했다. 2개는 provider가
이름의 공백을 다르게 썼고 1개는 원래 행과 다른 이름을 반환했다. 3개 모두 이미 인용한
native 행에 원래 이름·가입금액·통화가 있고 private 인용 페이지와 일치했다. 원문 정보
부재가 아니라 처리 누락이다. private 값을 정답으로 주입하지 않고 같은 source 행에서
새 초안을 만들며 원래 응답과 제외 이력은 유지한다.

## Implementation

전체 name-field 인용으로 기존 독립 locator가 하나의 물리 위치를 입증한 뒤, 그 위치를
재현하는 primary 이름 근거를 선택한다. 머리글과 equivalent view 인용은 삭제하지 않으며
서로 다른 이름 위치는 거부한다. 명시적 v10/normalization v5/schema 0079는 이미 인용된
native 표의 명확한 이름 열과 같은 금액 proof로만 잘못된 이름을 복구한다. 기존 독립 검수
의미와 기본 처리 버전은 유지하고 새로 복구한 후보만 fresh verifier에 전달한다.

## Verification

구현·관련 테스트·문서가 완성된 뒤 필요한 검사만 한 번 모아 실행한다. 현재는 작성 중이며
이 PR의 상세 검사와 실제 v10 복구는 아직 실행하지 않았다. PR #103의 통과 결과를 이번
추가 코드의 증거로 확대하지 않는다. 실제 값·본문·Drive 식별자는 저장소에 포함하지 않는다.

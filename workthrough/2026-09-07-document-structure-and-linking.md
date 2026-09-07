# v0.5 B02 document structure and linking

- 상태: in_progress; 로컬 구조/저장 기반 검증, 자동 가입·판본 연결과 runtime 소비는 미완료
- 범위: [WP02 #62](https://github.com/jihoon22-lee/family-care/issues/62),
  [WP03 #63](https://github.com/jihoon22-lee/family-care/issues/63)
- 기준: B01 merge `b084fd9c62e3e617c4cbc15e57cf959d450349b5`
- 초기 통합: IR `195bb36`(독립 소스 `e9c0cd1`), layout `36ddf8a`(독립 소스 `8bed931`)
- 실행: 2026-09-07, WSL Linux, 잠금 의존성, 전용 합성 PostgreSQL 18.6
- 계획: [002 B02](../docs/plan/v0.5.0/002-document-structure-and-linking.md)

## Implemented local boundary

`document_structure.py`는 보관 extraction/OCR 전문과 page/block/row/cell/좌표/reading order를
보존한다. 원본 계층을 지우지 않고 사용할 계층을 별도로 선택한다. 새 chunk는 node의
겹치지 않는 문자 범위이며 반복 헤더·단위·각주 문맥을 primary row와 분리한다.
240자 뒤·64번째 범위 뒤의 텍스트, 40개 특약 행을 보존한다. 행/문맥이 예산을 넘으면
unprocessed 범위와 이유를 남긴다. `ChunkPlan.complete`는 계획 완료만 뜻한다.

`document_layout.py`는 실제 표/좌표/문구에서 명확한 header/unit/footnote/continuation
관계를 제안한다. 상이 단위, 여러 대상 표, 다른 계약, 열 차이, 미완료 OCR은 추정하지
않는다. 원문/셀/행/금액은 불변이며 기존 metadata와의 모순은 덮지 않는다.
`LAYOUT_RELATION_PROPOSAL`은 가입·지급·USER_CONFIRMED 권위가 아니다.

`document_structure_source.py`는 가정·구성원·batch item에 연결된 성공한 저장 추출을
읽는다. 모든 native block/table/cell과 해당 OCR 계층을 읽고 layout/IR로 변환하며
PDF 파일이나 provider를 호출하지 않는다. OCR의 실제 문서/해시/페이지를 Evidence에
연결하고 불일치는 저장 전에 거부한다. source/설정/OCR/layout revision은 snapshot에 남긴다.

0026 migration과 `DocumentStructureRepository`는 구조/범위 계획을 immutable generation으로
저장한다. 동일 입력은 같은 generation을 재사용하고, 부분 계획은 기존 current 구조를
대체하지 않는다. 범위 작업은 PostgreSQL 행 잠금·시도 한도·고유 lease token을 사용한다.
완료 범위는 취소/재개 때 다시 처리하지 않으며 늦은 이전 lease는 성공을 기록할 수 없다.
보존된 source에서 IR, IR에서 계획을 다시 계산해 노드/범위 본문 변조도 거부한다.

`is_current`는 완전한 로컬 구조 계획을 가리킨다. 청크 처리·AI 검수·가입 원장·약관 지식의
활성 상태를 의미하지 않는다. 결과 저장은 그 자체로 가입/금액 authority가 아니며
새 tables는 기존 원본·교정·보험/청구 snapshot을 변경하지 않는다. history가 있으면
0026 downgrade를 거부하므로 실제 전환은 WP09의 백업·복원·중단 재개 수용을 거쳐야 한다.

## Executed verification

독립 구현은 새 module 부재 RED 후 IR 22개+기존 PDF 추출 14개를 통과했다. 각주 누락,
헤더 identity, 누락 페이지와 OCR lineage 추가 RED도 확인했다. layout은 새 module 부재와
실패/OCR 페이지·상이 계약의 연속표 회귀 RED 후 26개가 통과했다.
최종 독립 관련 suite는 layout 26+IR 22+기존 추출 14 = 62 passed였다.

저장 통합은 새 repository module 부재 RED 후 구현했다. 계획만 바꾼 경우뿐 아니라
노드 본문을 바꾸고 계획을 다시 만든 경우도 RED로 확인해 각각 canonical 재계산으로
막았다. 기존 DB 추출 재사용은 `prepare_stored` 부재 RED 후 연결했다.

OCR 통합 최초 실행은 합성 fixture의 필수 `source_layer/review_state` 누락 때문에
기능 증거가 없었다. fixture 수정 후 다른 해시를 허용하는 RED를 확인했다. 실제 OCR
해시와 nested Evidence를 전달하도록 수정하고 성공/거부 쌍이 최종 통합에서 통과했다.

| 명령/환경 | 실제 결과 |
|---|---|
| `TMPDIR=/tmp uv run ruff format --check .` | 534 files 통과 |
| `TMPDIR=/tmp uv run ruff check .` | 통과 |
| `TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts` | 224 sources 통과 |
| `TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q` | 1,718 passed, 196 integration deselected, 3 subtests passed |
| `TMPDIR=/tmp uv run python scripts/check_contracts.py` | 기존 API/생성 계약 drift 없음 |
| `TMPDIR=/tmp uv run python scripts/check_containers.py` | 3 images/4 Compose services 정적 정책 통과 |
| `TMPDIR=/tmp uv run python scripts/check_workflows.py` | workflow 정책 통과 |
| 전용 합성 test URL/삭제 방어 승인 변수의 `pytest -m integration apps/api/tests workers/analyzer/tests -q` | 195 passed, 1,445 deselected |
| 최종 source adapter 수정 후 대상 Ruff format/lint·mypy 2 files·`git diff --check` | 통과 |

새 저장 통합 7개는 전량/멱등/본문 변조, 이전 정상 구조 보존, 다른 가정·구성원 거부,
두 thread의 동시 claim, 취소/재개, 오래된 lease 거부, 실제 DB native/OCR 재사용을 검사한다.
OpenAI 키를 제거한 사례도 포함하며 provider 호출·실제 PDF 파일 열람은 없다.

Web 소스/생성 계약/잠금파일은 B01 `c2d4f58` 이후 동일하다. 그 소스의 같은 작업 중
Web 전체 162개·Chromium mock E2E 15개 통과 증거를 유지한다. 새 Web 동작이나 실제 기기
검증으로 확대하지 않는다. 이번 PR의 실제 이미지 빌드와 최종 CI는 remote 결과로 추가한다.

## Remaining work and review findings

- 자동 source preparation/runtime 소비, provider 범위 처리·총량 한도, 부분 결과 publication은
  아직 연결하지 않았다. 새 helper와 DB 검증을 앱의 전체 문서 처리 완료로 보고하지 않는다.
- 기존 raw `private_knowledge_coverages.rider_id/MATCH`는 제품에서 생성하지 않는 snapshot
  비실행 경계다. 계약 수준 operational link도 개별 담보 동일성을 보장하지 않는다.
  다음 구현은 검증된 가입 행과 별도 담보 연결 이력을 사용하고 이름/금액만으로 합치지 않는다.
- 새 snapshot에서 이전 current 계약 링크를 정리하지 않아 다시 연결할 때 unique 충돌할 수
  있는 경로를 정적으로 확인했다. 세대 전환 때 이력을 supersede하고 과거 결과는 보존해야 한다.
- 같은 담보의 source refs, 다른 계약/가족 보존, 링크·원장 version 변경의 stale 감지는 미구현이다.
- 기존 키 재사용과 보호된 runtime/자료 처리 범위는 현재 세션의 필수 입력 대기다.
  실제 자료/보관소/DB를 탐색·변경하거나 외부 AI에 전송하지 않았다.

API 응답·생성 schema·서비스 시작 명령은 이 부분 변경에서 바꾸지 않았다. 문서 내용·실제
식별자·경로·암호·Drive ID를 공개 자료로 가져오지 않았으며 모든 검증은 처음부터 만든
합성 값과 전용 DB를 사용했다. 마일스톤/실자료 전환/태그/릴리스/배포는 미완료다.

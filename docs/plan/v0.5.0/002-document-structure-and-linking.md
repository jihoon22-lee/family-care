# v0.5 B02: Document structure and enrollment linking

- 상태: in_progress
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59),
  [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 구현: [WP02 #62](https://github.com/jihoon22-lee/family-care/issues/62),
  [WP03 #63](https://github.com/jihoon22-lee/family-care/issues/63)
- 선행: B01 [PR #75](https://github.com/jihoon22-lee/family-care/pull/75),
  merge `b084fd9c62e3e617c4cbc15e57cf959d450349b5`
- 요구사항: R02/R03/R04/R10/R11/R13/R17/R18/R19/R20, S01/S02/S03/S04/S09/S11/S14

## Tasks

1. complete — 전체 block/table/cell/OCR 보존 IR, 명확한 레이아웃 관계,
   페이지/행/문자 범위와 처리 누락 adapter를 통합했다. 관련 합성 62개 통과.
2. complete — 버전별 IR/범위 상태 저장과 기존 DB 추출 재사용, 부분 계획·취소·재개·
   stale lease·동시 claim의 PostgreSQL 7개 회귀를 통과했다. runtime 소비와 source 처리
   상태 노출을 연결했다. 원문 없음과 처리 실패를 구분하며 새 버전이 과거 준비 상태를
   최신 완료로 표시하지 않는다. 신규 준비 통합 6개와 전체 PostgreSQL 201개가 통과했다.
3. in_progress — 범위별 검토 후보와 원문 위치, 필드 원문 대조, 로컬 피보험자/계약
   연결 근거를 저장한다. 검증된 계약/담보의 API 자동 반영·원장 조회·재시도와 사용자 교정
   우선순위를 연결했다. 검증된 연속표·열/단위 문맥을 반영하고, 가입 사실과 유형 분류를
   분리해 미분류 담보를 보존하며 미가입/예시는 반영을 막는다. native 단어의 원본 역추적
   줄을 연결하고 같은 DocumentVersion의 재추출/표·줄 표현을 물리 이름 좌표로 연결했다.
   동일 bytes·원문 계약번호·피보험자 근거로 일반 재가져오기의 새 DocumentVersion도
   기존 계약/담보에 연결하며 alias 근거가 있는 원장 조회/약관 링크를 허용한다.
   현재 원본 snapshot을 수정하지 않는 exact content manifest 기반 문서 연결 이력과
   공통 담보 identity를 구현 중이며, component/판본 연결은 후속이다.
4. in_progress — 문서 등록의 범위별 구조화·최소화·총량 제한을 연결한다. OpenAI 연결 작업은
   기존 키 재사용·보호된 자료 처리·최소 전송은 세션에서 승인받았다. 낮은 API 잔액에
   맞춰 로컬 재사용을 우선하고 범위별 호출 예산을 구현한 뒤 제한적으로 실행한다.
   기존 정책 Worker에 묶음 검증·영속 요청 예약/캐시·문서 누적/UTC 일일 예산을 연결했다.
   전체 IR 범위 저장/재개·후보 publication·API projector를 합성 통합에 연결했고 기본
   Worker/Compose 설정을 전환했다. 실제 provider/보호된 자료 수용은 아직 남아 있다.
5. pending — 전체 필수 검사·합성 PostgreSQL/문서 흐름·리뷰·CI 후 B02를 merge한다.

## Local data contract

`document_structure.py`는 로컬 전량 보존 adapter이고 외부 전달 DTO가 아니다.
문서 bytes·extraction/OCR revision·node/row/cell 위치를 보존한다. 행은 쪼개지 않고
반복 헤더/각주 문맥을 primary 가입 행과 별도로 참조한다. `ChunkPlan.complete`는 범위의
계획 완료이며 구조화/원장/지식화 완료를 의미하지 않는다.

원문에 없는 가입 사실·금액·상태를 만들지 않는다. 명확한 근거가 없는 구조 관계,
읽을 수 없는 페이지, chunk 예산을 넘긴 범위는 unresolved로 남긴다. 새 처리 실패가
기존 가입·교정·청구 snapshot을 삭제하거나 현재 상태를 변경하지 않는다.

## Acceptance evidence

IR 구현은 별도 worktree commit `e9c0cd1`(통합 `195bb36`)에서 시작했다. 새 모듈 부재
RED와 누락 각주/헤더 identity/페이지·OCR lineage 회귀 RED 후 IR 22개와 기존 PDF 추출
14개 합성 테스트가 통과했다. 이 증거는 순수 adapter 범위이며 DB/실제 import/외부 전달
완료를 의미하지 않는다. 최신 결과와 남은 경계는
[B02 workthrough](../../../workthrough/2026-09-07-document-structure-and-linking.md)에 연결한다.

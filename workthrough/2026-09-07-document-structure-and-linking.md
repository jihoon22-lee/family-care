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

Web 소스/생성 계약은 B01 `c2d4f58` 이후 동일하다. 후속 PR #77의 보안 패치를 병합했고
Web tree/manifest/lock/Node pin이 검증된 보안 소스 `5e3c4f3`과 동일함을 `git diff`로 확인했다.
그 소스의 Web 전체 162개·Chromium mock E2E 15개 통과 증거를 유지하며 root에서도 frozen
install을 확인했다. Python/DB 소스는 이 병합으로 바뀌지 않았다.

Draft [PR #76](https://github.com/jihoon22-lee/family-care/pull/76)의 `fff6aa7` 대상
CI [34090106992](https://github.com/jihoon22-lee/family-care/actions/runs/34090106992)는
필수 7개 검사와 세 이미지 빌드가 모두 통과했다. 현재 draft는 자동 가입/판본/담보 연결과
runtime 소비를 남긴 상태이므로 #62/#63을 종료하거나 merge하지 않았다.

## Remaining work and review findings

- 아래 후속 변경에서 로컬 source preparation과 상태 소비를 연결했다. provider 범위 처리·
  총량 한도와 부분 결과 publication은 아직 연결하지 않았다. 새 helper와 DB 검증을 앱의 전체 문서 처리 완료로 보고하지 않는다.
- 기존 raw `private_knowledge_coverages.rider_id/MATCH`는 제품에서 생성하지 않는 snapshot
  비실행 경계다. 계약 수준 operational link도 개별 담보 동일성을 보장하지 않는다.
  다음 구현은 검증된 가입 행과 별도 담보 연결 이력을 사용하고 이름/금액만으로 합치지 않는다.
- 새 snapshot에서 이전 current 계약 링크를 정리하지 않아 다시 연결할 때 unique 충돌할 수
  있는 경로를 정적으로 확인했다. 세대 전환 때 이력을 supersede하고 과거 결과는 보존해야 한다.
- 같은 담보의 source refs, 다른 계약/가족 보존, 링크·원장 version 변경의 stale 감지는 미구현이다.
- 기존 키 재사용·보호된 자료 처리·최소 외부 전송은 세션에서 승인받았다. API 잔액 제약에
  따라 이 후속 구현도 합성 데이터와 외부 호출 0회로 진행했다.

위 `0026` 기준 증거에서는 API 응답·생성 schema·서비스 시작 명령을 바꾸지 않았다. 문서 내용·실제
식별자·경로·암호·Drive ID를 공개 자료로 가져오지 않았으며 모든 검증은 처음부터 만든
합성 값과 전용 DB를 사용했다. 마일스톤/실자료 전환/태그/릴리스/배포는 미완료다.

## Local preparation runtime and status (2026-09-07)

기준 `27d1d60` 이후 변경이다. `document_preparation.py`와 migration `0027`이 저장 추출의
자동 준비를 Worker fair queue에 연결한다. provider/key와 private 원본 root 없이 동작한다.
DB item lock과 준비 이력으로 동시 실행·재시작·재시도에 대응하고, terminal 준비 기록의
삭제/재작성을 금지한다. 신규 배치 상태의 네 optional 필드를 canonical schema에서 생성해
API와 Web이 소비한다. 가져오기 성공을 전체 분석 완료로 표현하지 않으며 로컬 준비 실패는
별도 표시한다. polling 상한은 응답마다 초기화하지 않고 batch 전체에 적용한다.

새 모듈 부재, Worker lane 미연결, API 상태 필드 부재, 최신 버전에서 과거 준비 상태 재사용,
Web 준비 실패 표시/가져오기 후 polling 부재를 각각 RED로 확인했다. 현재 관련 결과는
Web component 15 passed, Worker startup 31 passed, API batch unit 51 passed다.
최종 전체 검증은 Web format/lint/typecheck·164 tests·production build 통과, Ruff format
539 files/lint·mypy 225 sources 통과, Python default `1719 passed, 202 deselected,
3 subtests passed`, 전용 합성 PostgreSQL `201 passed, 1446 deselected`다. 문서 50개·
안전 732 paths·계약/OpenAPI/생성 타입·container/workflow 정적 검사·diff 검사도 통과했다.
명령은 위 필수 검증 표와 동일하며 source `27d1d60` + 이 절의 관련 미커밋 변경에 연결된다.
이전 source의 CI를 새 source의 image-build 증거로 사용하지 않는다.
합성 전용 DB에만 `0027`을 적용했다. 승인 범위에서 운영 DB의 자료 수만 read-only 집계했고
본문·식별자·경로를 출력하거나 저장하지 않았다. 첫 집계의 마지막 테이블 이름이 맞지 않아
부분 결과로 끝났고 후속 수정 조회는 성공했다. 운영 schema/자료 변경·원본 열람·provider
호출·배포는 수행하지 않았다. 조회 집계는 합성 수용/판독 품질 검증이 아니다.

Chromium mock E2E는 초기 `14 passed, 1 failed`였다. 완료 문구를 구분한 변경에 맞춰
합성 import 시나리오의 기대값과 내용 준비 상태를 갱신한 뒤 `15 passed`를 확인했다.
후속 E2E 파일의 ESLint·Web typecheck도 통과했다. 실제 backend/Windows/mobile 검증은 아니다.

## Retained provider stages and bounded spend (2026-09-07)

기준 `e6b9891` 이후 정책 Worker를 구조화 1회·묶음 검증 1회로 바꿨다. migration `0028`과
`policy_request_budget.py`는 외부 요청 전 예약 commit, 동일 단계 성공 응답 재사용, 문서 누적
4회/전체 정책 구조화 UTC 일일 8회 한도를 제공한다. timeout·실패도 소비량에 포함하며 SDK
내부 재시도를 끈다. 예산 대기는 처리 시도 횟수를 소모하지 않는다. 모델·schema·최소화된
입력·지침이 바뀌면 캐시를 재사용하지 않는다. 예약은 가정/구성원/문서/추출/파이프라인과
현재 lease owner·시도 번호를 확인하며, 이력을 삭제하거나 결과를 원장 권위로 승격하지 않는다.
기존 API와 원장 schema는 유지한다. Worker 출력 상한은 구조화 8192·검증 4096 token이다.

모듈/묶음 schema/출력 상한 부재 및 다른 lease 시도 허용을 각각 RED로 확인했다. 합성
PostgreSQL에서는 동시 마지막 예산, 중복 진행 요청, 다른 문서의 일일 예산 공유, 잘못된
구성원, timeout 소비, 네트워크 직전 commit, 예산 보류, Worker verifier 재시도의 structurer
재사용을 검증한다. 신규 source window helper는 구간을 자르기 전 식별자 span을 계산한다.
관련 합성 unit 83개와 Web 164개·format/lint/typecheck/build가 통과했다. 최종 필수 명령은
2026-09-07 07:36–07:40 UTC, `e6b9891` + 이 절의 변경에서 실행했다. Ruff format 544개/lint,
mypy 226개, Python default `1734 passed, 210 deselected, 3 subtests passed`, 계약/생성 타입,
container/workflow 정적 검사, 문서 50개, 안전 737 paths, `git diff --check`가 통과했다.
전체 PostgreSQL은 `208 passed, 1 failed, 1461 deselected`였다. 실패는 기존 import 합성
provider가 이전 단일 verifier 응답 형식만 지원한 데서 발생했다. 해당 fixture를 새 묶음
계약으로 갱신한 뒤 import 흐름과 신규 예산 통합을 함께 실행해 `9 passed`를 확인했다.
fixture 후속 변경의 Ruff format/lint도 통과했다. 전체 DB suite를 다시 실행했다고 표현하지
않으며, 이전 PR source의 image-build 증거를 이번 변경에 적용하지 않는다.

전체 IR 범위 scheduler·범위별 publication·가입/판본 연결은 남아 있으며 B02 전체 완료를
주장하지 않는다. 합성 테스트와 전용 합성 DB에만 `0028`을 적용했다. 실제 provider 호출은
0회이며 보호된 원문·키·경로를 읽거나 출력하지 않았다. 실제 런타임 migration/전환·태그·배포는
수행하지 않았다.

## Durable analysis ranges (2026-09-07)

기준 `b5926ac` 후속이다. `ai/policy_ranges.py`는 보관 전문을 4096자 primary 범위와
문맥으로 나누고 32 primary/64 Evidence/16384자 요청에 묶는다. 페이지 단위 최소화를
먼저 수행해 식별자의 범위 경계 누출을 막고, 긴 표 행·문맥·예산 초과를 누락으로 남긴다.
`policy_range_structurer_v3`는 모든 primary 범위의 처리 여부와 후보 인용을 검사하며
뒤쪽 특약만 있는 범위에 계약 header를 만들지 않는다. 알 수 없는 문서 역할에 AI가 동의해도
가입 권위를 주지 않는다. Worker 출력 상한은 이 schema에도 8192 token이다.

`0029_policy_ranges`와 `PolicyRangeRepository`는 원문 generation/문자 위치를 보존한
최소화 입력과 범위별 결과를 저장한다. 완료 결과는 불변이며 실패한 범위를 넘겨 다음 범위를
처리하고, 예산 보류·재시작 때 완료 범위를 반복하지 않는다. 변경된 구성원 최소화 목록,
다른 구성원/시도 번호, 만료 lease는 기존 범위를 전송·저장할 수 없다. 모든 범위를 끝내도
누락/미해결이 있으면 전체 성공으로 표시하지 않는다. 기존 legacy 후보 provenance의
verifier revision도 실제 묶음 verifier에 맞췄다.

모듈/runner 연결/출력 상한 부재와 잘못된 범위 뒤 전체 job 중단, 최소화 목록 변경 허용을
RED로 확인했다. 관련 unit 85 passed, 신규 PostgreSQL 최종 10 passed다. 71개 범위의
3개 요청 묶음 보존·재개, 예산 중단, 개별 실패 뒤 계속 처리, unknown 역할 거부,
structurer 성공→verifier timeout→verifier만 재시도하는 실제 Worker 경로를 검사했다.
합성 DB에서 빈 `0029` downgrade/upgrade도 실행했다. 2026-09-07 08:07–08:11 UTC,
`b5926ac` + 이 절의 변경에서 전체 필수 검사를 직렬 실행했다. 문서 50개/안전 744 paths,
Web format/lint/typecheck·164 tests·build, Ruff format 551개/lint, mypy 229개,
Python default `1747 passed, 219 deselected, 3 subtests passed`, 계약/container/workflow 정적
검사와 diff 검사가 통과했다. 전체 합성 PostgreSQL은 `218 passed, 1474 deselected`였다.
후속 검토에서 unknown primary 역할의 계약 후보 저장 거부를 추가 RED로 확인하고
저장 단계의 역할 검사로 보강했다. 해당 변경 후 대상 Ruff/mypy와 신규 DB suite 10개가
통과했다. 후속 소스의 전체 DB suite를 재실행했다고 표현하지 않는다.

기본 Worker 실행 전환은 범위 publication 구현과 함께 진행한다. 현재 선택 가능한 범위
runner의 결과 저장을 기존 원장 반영 완료로 보고하지 않으며 B02는 계속 draft다. 운영
schema/자료는 변경하지 않았고 실제 provider 호출·태그·배포는 수행하지 않았다.

## Range candidate provenance and local insured association (2026-09-07)

기준 `21db1a5` 이후 migration `0030`과 range publication을 추가했다. 완료 범위·후보·실제
Evidence FK·원문 node/문자 위치를 한 transaction으로 저장하고, provider 후보 ID를 범위별로
구분한다. 240자 excerpt는 미리보기이며 전체 최소화 입력과 원문 위치는 별도로 유지한다.
문서 일부가 실패해도 해당 가정/구성원의 검토 후보와 교정 lineage가 조회된다.

필드 grounding은 명시 label·단위·날짜·담보명을 대조하고 보험료/한도를 가입금액으로 쓰지
않는다. 로컬 피보험자/계약 resolver는 정확한 역할/값·인접 셀과 같은 계약 anchor를 사용하며
잘못된 대상자·중복 이름·다른 계약·자료 역할·변경된 구성원 identity를 보수적으로 처리한다.
원문 이름/계약 식별자는 provider DTO나 연결 metadata에 복사하지 않는다. 연결에 필요한
opaque scope·구성원 revision·원문 위치만 유지한다. 미지원 문서 형식의 자동 판독을 주장하지
않는다. 기존 job 단위 projection은 새 범위 계약/피보험자 연결을 건너뛰므로 새 범위 후보와
그 교정 자손이 해당 경로로 가입되는 것을 막았다. 자동 원장 projector·판본·공통 담보 식별과
기본 Worker 실행 전환은 아직 남아 있다.

새 모듈/저장 컬럼 부재, 중복 provider ID, 부분 후보 조회 누락, 과도한 필드/중복 인용,
잘못된 product, 범위 교정의 피보험자 우회, 희소 셀과 약관 예시 이름의 잘못된 연결을 RED로
확인하고 수정했다. 빈 합성 DB에서 `0030` downgrade/upgrade를 검증했다. 개발 중 컬럼 추가
전 DB를 수정된 downgrade로 되돌리는 시도가 실패했으며, 새 컬럼의 조건부 drop을 적용한
후 downgrade/upgrade와 대상 통합이 통과했다. 실제 runtime DB는 변경하지 않았다.

2026-09-07 08:39–08:46 UTC, `21db1a5` + 이 절의 변경에서 필수 검사를 직렬 실행했다.
`web:check`의 format/lint/typecheck·164 tests·build, Ruff format 557개/lint, mypy 232개,
기본 pytest `1773 passed, 226 deselected, 3 subtests passed`, 계약/생성 타입·container/workflow
정적 검사, 전체 합성 PostgreSQL `225 passed, 1500 deselected`가 통과했다. 문서 50개·저장소
안전 750 paths·diff 검사도 통과했다. Web 이후 변경은 API/Worker와 문서이며 Web source는
동일하다. 새 image build는 다음 PR CI에서 확인한다. 이전 head `21db1a5`의 CI 7개는 모두
통과했다. 실제 문서 수용·외부 provider·Windows/모바일·운영 전환·태그/배포는 미실행이다.

마일스톤 설명·#59 진행 원장·#60·WP02 #62·WP03 #63·PR #76의 본문을 실제 단계에 맞춰
동기화했다. PR push마다 세부 진행과 검증을 함께 갱신한다. 기본 구현 PR 8개 중 B01 merge,
B02 진행이며, 이 단계 완료를 WP02/WP03 전체 수용이나 마일스톤 완료로 표시하지 않는다.


## API enrollment publication (2026-09-07)

기준 `e203e04` 이후 API가 보관 범위 후보를 자동 원장으로 반영한다. `0031`은 원본 후보와
원장 ID/version/권위의 불변 이력 및 재시도 순서를 저장한다. 계약 scope와 원래 담보명의
원문 위치가 identity이며 범위 하나에 담보가 여럿 있어도 충돌하지 않는다. 정확한 피보험자
Evidence와 범위 Evidence를 사용해 기존 원장 조회 경로까지 연결했다. 사용자 교정은 같은
transaction helper를 사용하며 직접 원장 수정/soft delete는 자동 덮어쓰기 대상이 아니다.
동일 값의 header 재판독은 원래 교정 권위를 유지한다. 최신 상태는 unknown이다.

API lifespan consumer는 Compose에서 활성화되며 GET은 읽기만 한다. DB 실패는 고정 코드로
기록하고 보류/값 오류 이후 다른 후보를 계속 처리한다. 기본 Worker는 범위 경로를 사용한다.
새 실행 연결 부재 RED, 한 블록의 두 담보 ID 충돌 RED, 원장 read의 NEEDS_REVIEW Evidence
거부 RED와 다른 행 금액 차용 RED를 확인한 뒤 수정했다. 새 PostgreSQL 13개를 포함한
범위/기존 private confirmation 32개와 관련 unit 48개가 통과했다. 빈 합성 DB의 `0031`
upgrade와 downgrade/upgrade를 확인했다. 개발 중 테이블 추가 전 revision에 대한 첫
내림은 미생성 테이블 때문에 실패했고, 빈 개발 DB 호환 처리 후 다시 통과했다.

다른 원문 행/문서 버전의 담보 동등성, private snapshot canonical mapping과 판본 연결은
아직 별도 후속이다. 실제 자료·provider·운영 DB·태그/배포는 이 변경 검증에서 사용하지 않았다.


최종 검증은 2026-09-07 18:19~18:28 KST, `e203e04` + 이 변경의 API/Worker/Compose·테스트·
문서에 대해 실행했다. `web:check`는 164개 테스트·format/lint/typecheck/build 통과,
Ruff format 562개·lint 및 mypy 234개 source가 통과했다. 기본 pytest는 1,781 passed /
239 integration deselected / 3 subtests passed다. 전체 합성 PostgreSQL은 238 passed /
1,508 deselected이며 계약 생성·container/workflow 정적 검사·문서 50개·안전 755 paths·
`git diff --check`도 통과했다. 첫 전체 PostgreSQL 실행은 테스트의 다른 합성 fixture 조회로
1 failed / 237 passed였으며, 테스트 source/job 필터 수정 후 전체 재실행에서 통과했다.
전체 정적 검사 중 grounding iterator 타입 충돌도 수정 후 재실행으로 해소했다.
새 이미지 빌드와 브라우저 검사는 로컬에서 재실행하지 않았으며 새 push의 CI에서 확인한다.

## Continued tables and unclassified enrollment (2026-09-07)

기준 `5ea3971` 이후 변경이다. 검증된 이전 가입 표에 이어지는 unknown 페이지의 data row만
가입 역할/계약 근거를 이어받는다. 다른 계약/피보험자·명시적 약관/모호한 페이지는 거부한다.
`table_grounding.py`는 불변 generation의 담보명·가입금액 열과 header/unit 문맥을 대조하고
그 Evidence를 필드에 보존한다. 같은 이름의 다른 행에서 금액을 가져오거나 보험료 열·충돌
header·빠진 문맥을 일반 텍스트로 우회하지 못한다. provider 원래 응답은 유지하며 검증 결과에
`range-grounding-v2`, 요청에 envelope v2를 남긴다.

가입 행이 확인되어도 분류 근거가 없으면 유형 `unknown`과 원문 가입금액을 저장한다.
`0032`는 담보·청구 후보 유형을 확장하며 미분류 이력이 있으면 downgrade를 거부한다.
API v2 후보는 UNKNOWN으로 표시하고 해당 담보를 실손 계산에 보내지 않는다. Web은 ‘유형
미분류 · 가입금액’으로 표시한다. 미가입/예시 행은 AI 검토 상태와 무관하게 `NOT_ENROLLED`를
남겨 일반 확인으로 가입시킬 수 없게 한다. 첫 publication 전 이름 교정은 원래 유효한 원문
위치를 먼저 사용하며, 이미 반영된 담보는 기존 ID를 유지한다. 원래 이름을 원문에서 찾을 수
없는 경우에만 사용자 교정의 근거 위치를 사용한다.

연속표/열 근거 부재, 보험료 차용, header/행 이름 충돌, 누락 문맥, 미가입 각주, 같은 이름의
다른 행 차용, 검토 상태의 미가입 확인 우회와 첫 반영 전 교정 실패를 RED로 확인했다.
합성 PostgreSQL의 범위 원장·기존 결정·Worker 저장 경로 40개가 통과했다. Web 첫 전체 검사는
새 issue 문구 누락의 TypeScript 오류로 실패했고 문구 추가/format 후 전체 166개 테스트와
format/lint/typecheck/build가 통과했다. 이는 실제 backend/모바일 브라우저 수용이 아니다.

다른 extraction/블록·표 표현의 담보 동등성, private/operational canonical 연결, component/
약관 판본 연결과 보호된 자료 수용은 남아 있다. 전용 합성 DB에서만 작업했으며 실제 자료
열람·외부 AI 호출·운영 DB 변경·태그/배포는 수행하지 않았다. 최종 소스 `5ea3971` + 이 절의 미커밋 변경에 대해 2026-09-07 19:48~19:57 KST에
필수 검사를 직렬 실행했다. Web 166개·format/lint/typecheck/build, Ruff format 567개/lint,
mypy 235 sources, 기본 pytest 1,810 passed / 245 integration deselected / 3 subtests passed,
전체 PostgreSQL 244 passed / 1,537 deselected가 통과했다. 문서 50개·안전 760 paths·생성
계약·container/workflow 정적 검사·diff 검사도 통과했다. 기본 pytest 첫 실행은 새 issue의
계약 검사 enum 누락으로 1 failed / 1,809 passed였으며 checker 갱신 후 전체 재실행이
통과했다. 빈 합성 `0032` downgrade/upgrade와 미분류 이력 존재 시 downgrade 거부를
확인했다. 새 이미지 빌드는 다음 push의 CI에서 확인한다.

Chromium mock E2E 최초 실행은 이전 약관 전용 후보의 확인 허용을 기대한 두 시나리오에서
13 passed / 2 failed였다. 제외 항목(약관 전용·미가입) 확인 차단과 정상 가입 후보 확인 후
Web Storage/IndexedDB 미기록을 분리한 뒤 16 passed를 확인했다. 변경한 E2E의 Prettier,
ESLint와 Web typecheck도 통과했다. API/Worker 소스는 이 후속 E2E 변경으로 바뀌지 않았다.

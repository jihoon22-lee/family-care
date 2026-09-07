# v0.5 B02 document structure and linking

- 상태: in_progress; 로컬 구조·자동 가입·범위 runtime 검증, 공통 출처/판본 연결과 보호된 수용은 미완료
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

## Native word lines and extraction-stable enrollment (2026-09-07)

기준 `28a60b3` 이후 변경이다. 독립 helper `7734a96`을 `9fa29e1`로 통합하고 projector에
연결했다. `document-structure-v2`는 원본 단어 BLOCK을 보존한 TEXT_LINE view와 문자별
source_spans를 만든다. 같은 layer·높이·가까운 수평 간격을 확인하고 표 안의 단어를 중복
예약하지 않는다. 먼 열/상태 표시는 `LINE_COLUMN_CONTEXT_UNRESOLVED`, 너무 긴 줄은
`LINE_EXCEEDS_CONTENT_BUDGET`으로 남긴다. 준비 primary/context는 각각 4096자이며
range envelope은 v3다. 등록되지 않은 bare Insured 이름도 최소화하고 최소화 revision이
바뀌면 이전 pending envelope을 거부한다. 줄 끝 미가입/예시 표시는 가입으로 반영하지 않는다.

`enrollment_locator.py`는 원래 이름의 native 단어 bbox·content hash·물리 페이지를 사용한다.
기존 household/계약과 publication을 함께 확인하여 같은 DocumentVersion의 새 extraction,
TEXT_LINE/TABLE_ROW를 한 Rider에 연결하고 source별 이력을 유지한다. source node ID와
원본 generation은 불변이다. 같은 이름의 다른 물리 행은 분리하고, 좌표가 불명확한 같은
이름의 재추출은 추가 가입으로 만들지 않는다. 원장 직접 수정·사용자 교정과 기존 primary
insured가 다른 구성원인 경우를 보호한다. 일반 private 재가져오기는 새 DocumentVersion을
만들기 때문에 그 계약 동등성과 private knowledge 연결은 아직 후속 작업이다.

새 line 부재 RED와 실제 ReportLab PDF→extractor의 단어 분리, 다른 열·가까운 미가입 단어,
미등록 Insured 이름 최소화, 이전 minimization 입력 재사용, 같은 PDF 재추출의 Rider 중복,
구성원 이름 변경 뒤 다른 구성원의 기존 계약 반영을 RED로 확인했다. 독립 locator 23개는
module 부재 RED 후 통과했다. shared primary header가 담보 위치로 취급되는 교차 view 실패도
추가 회귀로 수정했다. 관련 API PG 26개, 범위/준비/native PG 30개와 pure 73개가 통과했다.
실제 합성 PDF→저장 추출→범위→원장 경로를 포함하며 provider는 합성 응답이다.

개발 중 테스트 schema/tuple 형식과 준비 범위 수 기대값을 수정했다. 구성원 변경 테스트의
첫 cleanup은 FK 때문에 실패했고, 전용 합성 DB에서 해당 fixture만 정리한 뒤 cleanup 순서를
고쳐 재실행했다. 실제 데이터나 외부 AI 호출은 없었다. 별도 문서 identity·판본·private
canonical 연결은 완료로 주장하지 않는다.

2026-09-07 20:36~20:44 KST, `9fa29e1` + 이 절의 API/Worker/테스트/문서 변경에서 직렬
검증했다. Ruff format 571 files/lint, mypy 236 sources, 기본 pytest 1,846 passed /
254 integration deselected / 3 subtests passed, 전체 PostgreSQL 253 passed / 1,573 deselected가
통과했다. 문서 50개·안전 764 paths·생성 계약·container/workflow 정적 검사·diff도 통과했다.
Web/manifest/lock/Node 입력은 `28a60b3`과 동일함을 diff로 확인했으며 해당 Web 166개·전체
정적/빌드와 Chromium mock E2E 16개 통과 증거를 유지한다. 이 변경에서 Web/브라우저를 다시
실행했다고 표현하지 않는다. 새 이미지 빌드는 다음 push의 CI에서 확인한다.

## Reimport contract aliases and downstream evidence (2026-09-07)

기준 `83f77dc` 이후 변경이다. 원문 계약 locator helper `0ca2f5e`를 `db242cb`, 약관 context
helper `a465b75`를 `2ee3653`으로 통합했다. 일반 가져오기처럼 같은 bytes의 새 문서/버전과
새 추출을 만들어도, 정확한 로컬 계약번호 anchor·가정·피보험자와 기존 publication을
대조해 한 계약을 재사용한다. 원문 native 이름 위치가 같은 담보도 기존 ID를 유지한다.
다른 bytes는 합치지 않고, 과거의 중복 계약이 여럿이면 자동 삭제/합병하지 않는다.

새로 발견한 담보의 Evidence가 재가져온 문서에 있을 때 원장 조회와 약관 연결 검증 모두
`enrollment_alias.py`를 사용한다. 원래 계약과 새 담보의 publication, 정확한 Evidence,
현재 문서 hash·추출·가정·피보험자·원문 계약을 재검증한다. 약관 링크 context의 새 boolean은
서버 내부 기본 false이며 외부 API/JSON Schema는 바꾸지 않는다. 두 번째 import 계약의
사용자 교정도 원래 PolicyContract/Party 문서 근거를 유지한다. 별도 migration은 없다.

재가져오기 시 두 계약 생성 RED, 새 담보 원장 조회 실패 RED, 두 번째 계약 교정 후 조회
실패 RED, 지원하지 않는 기존 locator 때문에 같은 문서의 다른 계약까지 보류되는 RED,
새 문서 담보의 약관 연결 실패 RED를 각각 확인 후 수정했다. 새 helper 단위 39개와
약관 context 단위 31개(11 RED 후 통과)를 검증했다. 관련 PostgreSQL 46개가 통과했으며
사용자 교정/직접 원장 수정·구성원 변경 보호, 동등 bytes 반복/별도 행, 근거 없는 Evidence
교체·변경 hash·누락 Party 거부, 다른 bytes/과거 복수 계약 보존, 실제 약관 연결 확인을 포함한다.

초기 테스트는 잘못된 합성 DB 계정 때문에 guard에서 중단되었으며 데이터 검증 결과가
아니다. fixture에 없는 documents 가정 열을 제거했고, 약관 fixture의 RESTRICT 정리 순서를
수정했다. 실패로 남은 fixture는 destructive guard를 통과한 전용 합성 test DB에서만
정리했다. 초기 alias SQL 괄호/정적 검사 오류도 수정했다. 실제 자료나 provider는 사용하지 않았다.

2026-09-07 21:06~21:10 KST, `2ee3653` + 이 절의 API/테스트/문서 변경에서 직렬 검증했다.
`ruff format --check .` 574 files, `ruff check .`, `mypy apps/api/src workers/analyzer/src scripts`
238 sources, 기본 `pytest apps/api/tests workers/analyzer/tests scripts/tests -q` 1,896 passed /
267 deselected / 3 subtests passed, 전용 합성 DB의 전체 `pytest -m integration apps/api/tests
workers/analyzer/tests -q` 266 passed / 1,623 deselected가 통과했다. 문서 50개·안전 767 paths·
생성 계약·container/workflow 정적 검사·diff도 통과했다. Web/계약/lock/toolchain 입력이
`83f77dc`와 동일함을 diff로 확인했으며 기존 Web 166개·Chromium mock E2E 16개 증거를
유지한다. 새 Web/브라우저/이미지 빌드를 이 로컬 실행으로 주장하지 않는다. 현재 기준
`83f77dc` CI 34118286689는 required 7개(이미지 3개 포함)가 모두 통과했다. 다음 push의 CI는
별도로 확인한다. 운영/private snapshot 공통 담보 identity, 자동 component/약관 판본 연결과
보호된 자료 수용은 아직 남아 있다.

## Exact knowledge source manifests and identity proposals (2026-09-07)

기준 `2b35773` 이후다. 순수 proposal helper `5da2715`를 `bffbe7d`로 통합했다. 기존 package의
문서 alias는 실제 PDF digest를 갖지 않아 basename 또는 package line→IR ordinal 추정을 하지
않는다. `0033`과 `source_bindings.py`는 외부 exact manifest를 검증해 별도 append-only 이력을
저장한다. 원래 snapshot은 그대로 유지하고 현재 run/package·가정·alias·bytes·페이지 수·문서
종류·성공한 extraction/Evidence를 대조한다. 로컬 명령은 private 경로/식별자를 환경변수로만
받고 hash·외부 regular file·1 MiB·JSON 중복 key를 검증하며 count/고정 오류만 출력한다.

DB module 부재 8 RED 후 통과했고, 재시도의 alias metadata 검증 누락 RED를 추가해 수정했다.
문서 source 연결 PG 15개는 atomic replacement/실패 전체 rollback, concurrent retry 1회 반영,
metadata/삭제/이전 run 재검증, snapshot 불변과 nonempty downgrade 거부를 포함한다. 전용
합성 DB에서 빈 0033 downgrade/upgrade도 통과했다. CLI module 부재와 duplicate JSON key,
env-only 경로 회귀를 RED→PASS로 검증했다. 같은 고정 Python 3.14의 깊은 JSON도 고정 오류로
거부되는지 실제 입력으로 확인했으며, RecursionError 우려는 이 환경에서 재현되지 않았다.

순수 matcher는 37개(모듈/generation 필드 부재 RED 후 PASS)를 통과했다. 결과는 정확한
publication/Evidence/generation/native 위치와 원래 private 위치를 가진 proposal이며, 현재
원장의 전체 source inventory·기존 사용자 연결·1:1 충돌 검사와 실제 canonical publication/
조회/청구 소비는 후속이다. 동일 이름의 다른 행·계약·구성원, inherited evidence와 잘못된
권위/OCR는 보류하며 금액으로 대상을 고르지 않는다. 실제 자료/외부 AI/운영 변경은 없었다.

최종 검토에서 soft-deleted primary_insured가 range 재반영과 별도 문서의 Rider 근거 조회에
포함되는 조건을 확인했다. 새 합성 PG 3개가 잘못된 재반영/조회 허용으로 실패한 뒤, 두
쿼리에서 삭제 관계를 제외해 통과했다. 다른 사용자 수정/원래 이력은 유지한다.

2026-09-07 21:52~21:57 KST, `bffbe7d` + 이 절의 source binding/CLI/삭제 관계 수정/문서에서
최종 검증했다. 기본 `pytest apps/api/tests workers/analyzer/tests scripts/tests -q`는
1,942 passed / 285 deselected / 3 subtests passed, `ruff format --check .` 581 files,
`ruff check .`, `mypy apps/api/src workers/analyzer/src scripts` 242 sources가 통과했다.
전용 합성 DB 전체 `pytest -m integration apps/api/tests workers/analyzer/tests -q`는
284 passed / 1,660 deselected로 통과했다. 앞선 281개 통과 뒤 삭제 관계 회귀가 추가되어
이 후속 실행을 최종 기준으로 쓴다. 문서 50개·저장소 안전 774 paths·생성 계약·container/
workflow 정적 검사·diff 검사도 통과했다. Web/계약/lock/toolchain 입력은 `2b35773`과 동일함을
diff로 확인했으며 Web 166개와 Chromium mock E2E 16개의 기존 증거를 유지한다.
`2b35773` CI 34120477553의 7개 작업(이미지 3개 포함)은 모두 통과했다. 새 source의 이미지
빌드/CI와 보호된 실제 자료 적용은 별도이며, 실제 canonical writer/조회는 아직 후속이다.

## Current canonical identity publication

`0034`와 canonical repository는 exact source binding·전체 native 원문 inventory·가입
publication으로 공통 담보 identity를 별도 저장한다. 원본 coverage snapshot을 수정하지 않고
현재 원장 version/필드 차이, 원래 package 위치와 publication 근거를 보존한다. 조회 때 현재
가정/구성원/primary insured·원본 문서/추출/Evidence·사용자 결정·1:1 관계를 재검사한다.
재추출의 아직 publication되지 않은 동일 이름도 전체 IR에서 확인한다. 사용자 교정 금액을
덮어쓰지 않으며 다른 Evidence로 바뀐 원장은 새 자동 연결로 승격하지 않는다.

기존 API consumer가 30초 간격으로 갱신하고 통합 계약 조회에서 현재 유효한 프로그램
연결을 제공한다. Web은 원본 기반 자동 연결과 재검토 동작을 표시한다. 기존 사용자 mutation
계약은 그대로 사용하며 응답 authority enum에 `PROGRAM_VERIFIED_SOURCE_IDENTITY`를
추가하고 neutral schema/OpenAPI/Web consumer를 함께 갱신했다. 코드 의미와 후속 경계는
[대사 설계](../docs/design/insurance-ledger-reconciliation.md#v05-source-verified-canonical-coverage-identity)에 기록했다.

모듈 부재, 미처리 두 번째 원문 위치의 stale 조회, Web 표시 부재, API 자동 갱신 부재,
원장 Evidence 차용을 각각 RED로 확인했다. 수정 후 신규 canonical PostgreSQL 13개,
기존 대사와 묶은 14개(신규 12개 시점), API consumer 4개, Web 관련 5개가 통과했다.
동시 갱신은 1회만 기록하며 불변 이력 수정/삭제 거부도 PostgreSQL에서 확인했다.
실제 자료/provider 호출·runtime migration·태그·배포는 실행하지 않았다. B02는 상세 담보 소비, 사용자 최초 publication의 program 검증, component/판본
연결과 보호된 수용이 남아 있다.


2026-09-07 22:22~22:31 KST, `30f6368` + 이 절의 canonical writer/조회/consumer·API/Web 계약과
테스트/문서 변경에서 검증했다. 기본 Python은 **1,971 passed / 298 deselected / 3 subtests**,
전체 합성 PostgreSQL은 **297 passed / 1,689 deselected**다. 한글 source record의 기존
UTF-8 canonical digest와 불일치를 추가 RED로 확인하고 수정한 뒤 전체 PG가 통과했다.
`0034`의 빈 합성 DB downgrade/upgrade도 통과했으며 다른 환경에는 적용하지 않았다.
`ruff format --check .` 586개, `ruff check .`, `mypy apps/api/src workers/analyzer/src scripts`
244개와 생성 계약·container/workflow 정적 검사·diff 검사가 통과했다.

`corepack pnpm@11.22.0 web:check`는 format/lint/typecheck·167 tests·production build가
통과했다. 이후 추가한 E2E 파일의 Prettier/ESLint와 전체 Chromium mock browser **17개**도
통과했다. 신규 320px 키보드 재검토에서 요청의 expected ID·scope 계약, 외부 요청 0회와
Web Storage/IndexedDB 쓰기 0회를 확인했다. 실제 backend browser, 실제 자료/provider,
Windows/mobile, 새 image build/CI와 protected 수용은 이 로컬 증거에 포함하지 않는다.


## Canonical identity consumers and field-specific correction

상세 담보와 로컬 안내가 공통 identity 계약을 소비한다. 새 common model은 운영 ref,
원래 두 source ref, ledger version, 검증 digest와 필드 차이를 보존한다. 연결 fingerprint에
필드별 차이를 포함하므로 이전 연결 이력은 보존하면서 background 갱신 후 최신 형식으로
조회한다. 새 migration이나 기존 snapshot 수정은 필요하지 않다.

원장과 다른 가입금액/통화는 해당 계산 입력에서 제외하고 원문 값은 상세 분석에 그대로
남긴다. 관련 후보와 산식은 유지하고 이름 차이는 독립적인 계산을 막지 않는다. 기존 청구
이력을 확인된 공통 Rider로 읽으며 없는 이력을 0회로 만들지 않는다. canonical 조회의 SQL
실패는 savepoint로 복구하여 기존 catalog/대사/안내를 숨기지 않는다.

common model 부재, 상세 DTO/결과의 identity 부재, 연결 Rider의 청구 이력 누락과 선택
identity 조회 실패가 catalog를 숨기는 RED를 확인했다. Web의 연결/필드 차이 설명도
표시 부재 RED 후 구현했다. 신규 순수 5개와 claim-history reader 회귀, 실제 합성
publication→연결→상세→사건 분석→원장 교정→새 결과 및 과거 JSON 보존을 확인했다.
통합 fixture 정리 실패는 보호된 전용 test DB의 정리 순서를 고치고 해당 합성 DB만 reset/
전체 migration 후 재검증했다. 이 작업에서 실제 runtime/원문/provider에는 접근하지 않았다.

사용자 최초 publication 재검증, 판본/component 연결과 보호된 수용은
남아 있다. 로컬 안내는 공통 ref를 사용하고 기존 v2의 두 평가 경로 기록은 유지한다.
운영 전용 담보까지 로컬 안내에 통합하는 작업은 B04 범위다.


2026-09-07 23:08~23:20 KST, `3270db9` + 이 절의 consumer/계약/테스트/문서에서 기본 Python
**1,977 passed / 301 deselected / 3 subtests**, 전체 합성 PostgreSQL **300 passed /
1,695 deselected**가 통과했다. `ruff format --check .` 588개·`ruff check .`, `mypy` 245개,
문서 50개·안전 781 paths·생성 계약·container/workflow 정적 검사와 diff 검사가 통과했다.
Web 전체 format/lint/typecheck·**169 tests·build**, Chromium mock **17 tests**도 통과했다.
마지막 문구는 필드 차이를 사용자 수정으로 단정하지 않도록 중립적으로 바꾸고 관련 Web
17개와 typecheck/production build를 다시 통과했다. 실제 backend/browser·실자료·provider·
Windows/mobile·image build는 로컬 결과에 포함하지 않는다. 기존 runtime·tag·배포 변경은 없다.


## Independently verified user enrollment identity

`0035`는 사용자 확인으로 처음 등록한 담보를 기존 native identity 검사에 연결한다.
원래 publication의 `USER_CONFIRMED`와 정확한 이름 출처 후보 ID를 proof에 보존한다.
원래 이름이 실제 원문 위치에 있으면 그것을 유지하며, 오독한 이름의 위치가 없을 때는
교정된 이름을 원문에서 다시 검증한다. canonical 표시명은 명시된 동일 계약/담보 ID 아래의
증권 검토 이름과 달라도 허용한다. 이름 유사도나 금액으로 합병하지 않는다.

최초 단순 확인·오독 이름 교정·표시명 변경의 3개 PG RED 후 모두 통과했다. 이후 잘못된
publication 종류/이름 출처를 삽입하는 경우는 실제 DB guard의 CheckViolation으로 거부했다.
음성 fixture의 JSONB 문자 매개변수 형식을 명시하여 SQL parse 오류를 거부 증거로 쓰지
않도록 정정했다. 사용자 proof가 있으면 downgrade도 거부한다. 표시명 alias는 helper와
DB 경로를 각각 RED로 확인했고, 편집 시작만으로 마지막 연결이 사라지는 2개 RED를 고쳐
프로그램/사용자 publication의 기존 원장·identity를 유지했다.

이 절은 사용자 확인 값 자체를 프로그램 검증 값으로 바꾸지 않는다. 원문 필드 차이와
기존 사용자 연결 결정은 이전 consumer의 독립 경계로 유지한다. 관련 순수 69개와 기존
canonical PG 21개 및 추가 편집 대기 2개가 통과했다.
실제 자료·provider·runtime·tag·배포 변경은 없다. component/판본과 보호된 수용은 남아 있다.


`4a5d140` + 이 절의 변경에서 기본 Python **1,981 passed / 308 deselected / 3 subtests**,
Ruff format 589개/lint, mypy 245개, 문서 50개·안전 782 paths·생성 계약·container/workflow
정적 검사·diff 검사가 통과했다. 최종 전체 합성 PostgreSQL은 2026-09-08 00:10~00:13 KST에
**307 passed / 1,699 deselected**로 통과했다. 빈 합성 DB의 `0035` downgrade/upgrade도
통과했으며 사용자 proof가 있으면 rollback을 거부하는 회귀도 포함한다. Web·공유 계약·
manifest/lock 입력은 `4a5d140`과 동일함을 diff로 확인하여 Web 169개/build·Chromium 17개의
기존 증거를 유지한다. 이 source의 image/CI와 보호된 실제 자료 수용은 별도다.


## Bounded source-page reads

공통 담보 연결은 증권 검토에서 실제 참조한 alias/페이지만 조회한다. 같은 bytes의 모든
재추출본에서 해당 페이지 전체 노드와 명시된 문맥 노드를 SQL로 투영한다. native/OCR,
미처리 단어, 표와 원문 위치는 그대로 검사하며 중복 source snapshot을 Python으로 읽지
않는다. 64 MiB JSON 한도는 한 페이지의 모든 재추출본 합계에 적용하며 넘는 페이지는
연결을 보류한다. 다른 페이지의 연결은 계속 가능하고 캐시는 마지막 페이지 하나다.

기존 전체 source JSON 때문에 작은 조회 예산에서도 연결이 누락되는 PG RED를 확인한 뒤
수정했다. 원문 snapshot 제외·한도 초과 보류·다른 큰 페이지 격리·문맥 노드 보존·다른 가정
거부와 기존 미발행 중복 이름 회귀를 포함해 canonical PostgreSQL 25개가 통과했다.
첫 실행은 예시 test DB 이름으로 접속하여 identity guard에서 중단됐고 결과가 없었다.
올바른 전용 합성 DB 설정 후 위 RED/GREEN을 확인했다. 실제 runtime DB로 fallback하지 않았다.

페이지 투영은 원본 저장 형태나 보험 계약/HTTP 응답을 바꾸지 않는다. 큰 문서의 전체
IR 보존 용량과 남은 component/판본 연결은 별도 후속 작업이며 이 조회 개선만으로
전체 문서 처리나 보호된 자료 수용을 완료했다고 보고하지 않는다.


Worker의 진행/취소/재개는 전체 IR·범위 계획 대신 완료 여부와 미처리 개수만 조회한다.
metadata 전용 조회 부재의 PG RED 후 해당 저장소 8개 회귀가 통과했다. 원본·범위 이력,
취소 상태와 재개 결과는 보존한다. 독립 변경 `677cc25`를 `0dc22d7`로 통합했다.

처음부터 만든 100페이지×1,000단어 실험에서 source JSON 6,247,065 bytes가 원본 단어와
줄/좌표/출처를 보존하는 IR 77,891,754 bytes로 확장됐다. plan 2,410,180 bytes,
105,000 nodes·5,000 chunks·누락 0이며 build 2.964초/전체 6.234초, peak RSS
465,476 KiB였다. 이는 구조화 성공 측정이며 현재 64 MiB DB 보존 성공은 아니다.
parser 한도를 올리지 않고 전체 보존 방식을 개선할 필요가 확인됐다. 실제 자료의 크기나
본문에서 만든 fixture가 아니며 외부 전송은 없었다.


2026-09-08 00:46~00:51 KST, `0dc22d7` + 이 절의 canonical 조회/테스트/문서 변경에서
기본 Python **1,983 passed / 311 deselected / 3 subtests**, 전체 합성 PostgreSQL
**310 passed / 1,701 deselected**가 통과했다. 이름 충돌 변수의 mypy 오류와 후속 줄 길이
오류를 수정한 뒤 Ruff format 590개/lint·mypy 245개, 생성 계약·container/workflow 정적
검사·diff가 통과했다. Web/공유 계약/manifest/lock은 `c9fe22c`와 동일하여 앞선
Web 169개/build·Chromium mock 17개 증거를 유지한다. image/CI는 push 후 별도로 확인한다.
실제 원문·provider·runtime migration·태그·배포 변경은 없다.

## Lossless page retention for large structures

`0036`과 `structure_storage.py`는 큰 논리 IR을 정확한 header와 페이지 part로 보존한다.
작은 inline generation과 기존 history를 유지하고, 저장 형식과 무관하게 같은 canonical
identity를 streaming 계산한다. native/OCR 원문·노드/셀·좌표/배열 순서·문맥/문자 출처를
보존하며 PostgreSQL `json`으로 음의 0·지수 숫자의 원래 직렬화도 복원한다. codec의
19개 순수 회귀를 독립 소스 `de6b184`에서 `eb12d87`로 통합했다.

논리 IR이 8 MiB를 넘으면 페이지 저장을 사용한다. 페이지 part 64 MiB/전체 파생 저장 512 MiB와
기존 plan 64 MiB를 각각 제한하고, parser/source 한도나 외부 전송 예산은 늘리지 않는다.
metadata와 페이지는 atomic하게 저장하며 불완전 manifest·변경/삭제·추가 part와 지원하지
않는 downgrade를 거부한다. 공유 SQL 함수가 두 형식에서 가정별 전체 요청 페이지 노드와
명시 문맥을 읽는다. API/Worker는 필요한 페이지로 조회하고 전체 원문 복원은 로컬 감사용이다.

기존 저장 함수/형식 부재의 PG RED, 페이지 저장에서 가입 publication이 0건인 RED를
확인한 뒤 원장/alias/공통 담보·Worker grounding 소비를 연결했다. 단일 증권 재추출과
새 DocumentVersion 재가져오기를 inline/page 두 형식에서 검증하며 기존 교정·근거를 유지한다.
기존 사용자-history downgrade 시험의 고정 schema 예상값은 실행 전 schema 보존 검사로
바꿨다. 최초 migration의 예약어 문법 오류와 개발 중 중간 schema 차이는 수정했고,
전용 합성 DB만 guard 후 재생성하여 최종 migration 전체를 적용했다. 실제 runtime은 미변경이다.

100페이지×1,000개 합성 단어의 IR은 기존 64 MiB monolithic 경로에서 거부되고, 페이지
저장과 정확한 원문 복원에서는 통과했다. 105,000 nodes/5,000 chunks·누락 0과 1/50/100쪽의
모든 노드를 확인했다. 해당 PG 시험은 43.56초, 전체 명령 44.09초, peak RSS 576,072 KiB였다.
이는 이 합성 규모의 실제 DB 보존 증거이며 모든 parser 허용 입력이나 실제 자료의 성공을
뜻하지 않는다. 큰 단일 페이지/총량 초과는 자원 실패로 남기고 기존 정상 결과를 보존한다.

정적 리뷰에서 JSON 위치 배열의 노드별 반복 파싱을 병렬 배열 순회로 바꾸고, SQL aggregate
전에 노드 크기를 확인했다. 공통 담보는 generation ID를 먼저 읽고 남은 예산 안에서 하나씩
투영하며 일부 재추출본만으로 연결을 승인하지 않는다. 문서 alias의 필요한 투영이 NULL이면
전체 alias 검증을 보류한다. 마지막 경우는 순수 RED 후 통과했다.

지연 DB constraint가 바깥 commit에서만 실패하면 준비 실패 이력도 사라지는 PG RED를
확인했다. 같은 manifest 검사를 writer savepoint 안에서 실행하여 실패·재시도 횟수를 남기고
최종 commit의 DB guard도 유지한다. 새 시험은 필요한 SQL 함수 존재도 검사하여 함수
미적용 오류를 의도한 실패 복구 증거로 사용하지 않는다. 최신 관련 합성 PG 73개와
codec/NULL 투영 순수 20개가 통과했으며 최종 전체 검증을 이어간다.


페이지를 빼먹은 준비는 1/2회에 재시도 상태, 3회에 terminal 실패가 되며 이후 다시 예약되지
않는 회귀를 통과했다. terminal 상태의 시간을 수정하려던 합성 test setup은 재시도 행만
갱신하도록 수정했다. 이 수정 전에 착수한 전체 검사는 29개 뒤 중단하여 완료 증거로 쓰지
않았고, 수정된 대상 검사 통과 후 전체를 새로 실행했다. 새 저장 형식에서도 이전 페이지의
연속표 header 문맥과 요청 페이지 전체 노드를 함께 읽는 PG 회귀가 통과했다.

2026-09-08 01:24~01:33 KST, `eb12d87` + 이 절의 저장/소비/테스트/문서 변경에서 기본
Python **2,003 passed / 330 deselected / 3 subtests**, 전체 합성 PostgreSQL **330 passed /
1,721 deselected**가 통과했다. 마지막 PG 전체는 230.59초이며 큰 문서 저장/복원은
43.89초였다. 기본 Python 뒤 추가한 연속표 테스트는 integration 전용이며 최종 PG에 포함한다.
문서 50개·안전 789 paths·Ruff format 596개/lint·mypy 247개·생성 계약·container/workflow
정적 검사·diff가 통과했다. 최종 빈 합성 DB의 `0036` downgrade/upgrade도 통과했다.
Web/공유 계약/manifest/lock은 `3f255a4`와 동일하여 Web 169개/build·Chromium mock 17개
기존 증거를 유지한다. 최신 독립 정적 리뷰의 저장·scope·원자성·투영 발견 사항도 반영했다.
실제 source/원문·provider·운영 migration·태그·배포는 실행하지 않았다. component/판본과
보호된 수용, 최신 push 후 CI/image 검증은 별도 후속이다.

## Independently verified document components

`document-metadata-v1` 제안은 기존 IR을 바꾸지 않고 원문 역할·보험사·상품/특약/판본 코드와
서로 다른 날짜 의미를 보존한다. API가 전체 상충 값, 원문 offset·라벨·인접 셀·페이지/lineage를
독립 검증한 component만 `PROGRAM_VERIFIED`로 등록한다. 본문 제출 목록의 역할명은 제목으로
쓰지 않으며, 제목보다 앞선 표·모호한 배치는 미분류로 남긴다. 사용자 확인·제외·삭제와 동일
bytes의 겹치는 범위는 자동 재생성하지 않는다. 생성자는 가짜 AppUser 대신 immutable
publication FK로 구분하고, component와 publication의 양방향 참조를 transaction 끝에 검사한다.

Worker/API는 큰 IR을 페이지 단위로 읽는다. metadata 실패/DB 저장 실패는 기존 IR을 보존하며
독립적으로 최대 3회 시도한다. 잘못된/중복 component 식별자는 PREPARED 저장에서 거부하여
후속 작업을 막지 않는다. neutral schema와 생성 타입·어휘의 drift 검사도 연결했다.
Worker와 API startup 소비, inventory 응답·연결 화면을 함께 연결했다. HTTP 요청에서는
프로그램 검증 상태를 주장할 수 없으며, 역할 검증과 사용자 확인한 적용 관계를 구분한다.
계약변경서 역할을 별도 보존한다. 자동 set/약관 판본/적용 관계는 이 변경에 포함하지 않는다.

검증 소스는 `afe9005` 위의 해당 metadata·migration·inventory·생성 계약 변경이다.
모듈 부재, 위조/누락 근거, paged 준비 실패, INSERT 실패 재시도, 잘못된 origin 참조,
제출 목록 오분류의 RED를 확인한 뒤 구현했다. 전용 합성 PostgreSQL에서 빈 metadata 이력의
`0038 → 0036 → head` 왕복과 전체 통합 352개가 통과했다 (`pytest -m integration
apps/api/tests workers/analyzer/tests -q`, 245.53초). 마지막 표 위치 비교의 반복 연산 제거 후
관련 순수 46개·PG 22개·mypy 2개를 재검증했다.

`web:check`는 170개·빌드, `CI=true ... test:e2e`는 Chromium mock 17개를 통과했다.
전체 기본 pytest는 2,053개·3 subtests 통과/통합 353개 제외, Ruff 609 files와 전체 mypy
254 sources, 계약·컨테이너 정의·workflow·문서 50개·안전 803 paths·diff 검사가 통과했다.
실제 문서/provider·운영 migration·Windows/모바일·이미지 빌드/배포 검증은 이번 변경에서
실행하지 않았다. B02의 판본 연결·보호된 수용과 최신 원격 CI는 후속이다.

## Component-scoped Terms editions

`0039_component_terms`와 API startup 소비자로 같은 파일의 검증된 terms component마다
판본을 등록한다. 원래 component·페이지·판본일·metadata snapshot과 등록/보류 이력은
불변이며 상품명이 없으면 명시된 상품코드만 표시한다. 같은 bytes의 수동 판본과 삭제
결정을 보존하고, 수동/자동 등록을 같은 transaction lock으로 직렬화한다.

Clause 생성·검색·링크·현재 규칙/계산 사용은 판본의 페이지 범위와 현재 component를
확인한다. 제외·삭제·범위 교정은 현재 사용에서 제외하되 과거 규칙 버전 조회는 유지한다.
누락·상충·잘못된 적용 날짜를 무제한 적용으로 해석하지 않으며 판본일은 적용일과 별개다.
복원 실패가 먼저 commit되던 경로도 고쳐 tombstone/version을 보존한다. API와 생성 Web
계약에 component/page/date를 추가하고 판본 선택에서 날짜·페이지를 표시한다.

2026-09-08 03:00~03:40 KST, `1a8d494` 위의 해당 migration/API/소비/UI/테스트/설계 변경을
검증했다. 범위·기간·복원·재가져오기·정규화 회귀와 API/소비/UI 누락의 RED를 확인했다.
`web:check`는 포맷 수정 후 **171개·빌드 통과**. 전체 기본 pytest는 추가 nullable 응답의
기존 기대값 수정 후 **2,063 passed / 374 deselected / 3 subtests**였다. 전체 합성
PostgreSQL은 **374 passed / 2,063 deselected**, 292.69초였다. 그 뒤 Unicode 정규화 확장의
DB 길이 오류를 재현하여 원장과 같은 key 길이 제한을 적용했고 관련 PG **22개**를 재검증했다.
`CI=true ... test:e2e`의 Chromium mock **17개**도 통과했다. 실제 Windows/모바일은 미검증이다.
Ruff format 614개/lint, mypy 255개, 생성 계약·container/workflow·문서/안전·diff도 통과했다.
빈 합성 DB의 `0039 → 0038 → head` 왕복을 실행했다. 독립 정적 리뷰의 정규화/기간/복원/
동일 bytes 보존 발견 사항을 반영했다. 실제 자료/provider·운영 변경·태그·배포는 실행하지
않았다. 다중 근거 자동 적용 연결, 기존 프로그램 component 보강·본문 범위 확장과 보호된
수용은 B02 후속이며 이 판본 등록만으로 완료를 주장하지 않는다.

## Retained geometry and revised cover metadata

검증된 보관 추출을 읽다가 유한 숫자 네 개지만 rectangle을 만들지 못하는 BLOCK 좌표
때문에 문서 전체의 구조화가 실패하는 경로를 확인했다. 원문과 raw 좌표는 그대로 보존하고
파생 bbox만 비워 `SOURCE_BBOX_UNAVAILABLE`을 표시한다. 잘못된 타입과 table/cell 좌표는
기존처럼 거부하며, 좌표 없는 BLOCK을 줄/물리 위치로 결합하지 않는다. 문자열·페이지
근거의 기존 가입 검증은 유지한다. 준비 revision은 이전 FAILED 이력을 보존하며 다시
처리하고, 정상 source의 기존 IR identity는 바꾸지 않는다.

`document-metadata-v2`는 상품 표제와 정식 약관 제목, 공백 라벨과 검증 가능한 단일 표지
셀을 읽는다. 원본 표 셀과 native 순서·좌표가 모두 증명될 때만 metadata용 순서를 만들고
원래 IR은 수정하지 않는다. 열 제목, 라벨이 섞인 제출 목록, 공백 라벨로 오인한 본문,
native 순서를 뒤집는 열 배치는 각각 RED로 재현한 뒤 거부하도록 보완했다. API도 원문
전체 필드와 span을 독립 검증한다. `0040_metadata_revisions`는 v1/v2 제안과 해당 validator의
대응을 검사하고 v1의 검증/게시 이력을 그대로 읽는다. 겹치는 기존 component의 자동
보강과 본문 범위 확장은 아직 구현하지 않았으며 기존 사용자 결정을 덮지 않는다.

`31182bb` 위의 해당 Worker/API/schema/migration·테스트 변경으로 순수 구조/metadata
94개, preparation PostgreSQL 8개를 통과했다. 그 전에 metadata Worker/API PostgreSQL
25개에서 새 revision·판본 등록·v1 이력·잘못된 validator 대응을 검증했다. 이후 추가한
정상 generation 재사용과 table/cell 거부 테스트는 최종 회귀 검사에 포함한다.
승인된 기존 보관 추출에 대해 읽기 전용·메모리 내 adapter를 실행하고 허용된 집계만
출력했다. 수정 후 해당 추출과 모든 저장 페이지를 구조화할 수 있었지만 약관 자동 분류는
여전히 충분하지 않다. 기존 지식의 후보 metadata와 필요한 원문 구역을 재사용하는 경로가
후속이며 이 결과를 약관 적용/지식화/보호된 전환 완료로 확대하지 않는다. 실제 원문·값은
저장소/fixture/로그에 넣지 않았고 provider·운영 쓰기·태그·배포는 실행하지 않았다.

2026-09-08 04:40~04:55 KST 최종 회귀: `web:check` **171개·build**, 전체 기본 pytest
**2,091 passed / 380 integration deselected / 3 subtests**, 전체 합성 PostgreSQL
**380 passed / 2,091 deselected** (285.61초)가 통과했다. 마지막 정상 참조 라벨 보완 후
관련 순수 **99개**와 metadata/판본 PostgreSQL **45개**를 재검증했다. Ruff format **615개**,
lint·mypy **255개**, 생성 계약·container/workflow·문서 **50개**·안전 **809 paths**·diff가
통과했다. 별도 빈 합성 DB에서 `head → 0039 → head`를 검증하고 그 테스트 DB만 정리했다.
최초 왕복 검사는 테스트 URL의 driver 표기 누락으로 시작하지 못했으며 `postgresql+psycopg`
표기를 고쳐 성공했다. Web UI/외부 응답 형태는 그대로이므로 Chromium mock E2E는 이번에
반복하지 않았다. 실제 Windows/모바일, PDF 재추출/OCR·외부 API·운영 DB 적용은 미실행이다.

## Immutable component refinements

`0041_component_supersession`와 API projector는 같은 DocumentVersion의 미교정 프로그램
분류에 더 강한 원문 metadata가 생기면 successor를 등록한다. 기존 해결 필드는 모두 보존하고
새 필드 또는 허용된 범위 확장이 있어야 한다. 이전 component/publication/TermsEdition의
원문 snapshot을 보존하고 현재 조회에서만 successor를 사용한다. 기존 판본이 있으면 새 판본
등록과 교체를 같은 transaction으로 처리하며 실패 시 이전 판본이 계속 조회된다.

사용자 확인·거부·삭제·버전 변경·과거 set 연결·조항 이력은 자동 교체를 막는다. 같은 bytes의
다른 import 이력도 보존한다. 불변 receipt와 현재 component 제약은 직접 DB 변경에도 적용한다.
문서 등록·판본 변경·set 연결·자동 projector는 같은 content 잠금을 먼저 획득한다. HTTP/생성
계약은 바뀌지 않았다. 증권·청약·변경 문서는 상품 identity만으로 범위를 확장하지 않는다.

`4789e4a` 위의 해당 migration/API/테스트/설계 변경에서 최소 pure test의 모듈 부재 RED와
이전 판본 표시가 남는 PostgreSQL RED를 확인한 뒤 구현했다. SQL trigger alias 충돌을 수정했고,
강제 잠금 순서 테스트의 `LockNotAvailable` RED와 receipt 없는 retired INSERT의 미거부 RED를
각각 수정했다. 순환 receipt는 거부되었으며 조상 탐색도 중복 제거로 제한했다. 관련 PostgreSQL
**24개**는 두 번 연속 보강·이력 보존·동시 삭제/연결·강제 잠금·등록 실패 rollback/재시도·삭제된
조항을 포함해 통과했다. 최초 조항 이력 테스트의 불완전한 extraction/page fixture는 보완했다.

2026-09-08 05:43~05:53 KST 전체 검증: Web **171개·build**, Ruff format **623 files**/lint,
mypy **259 sources**, 기본 pytest **2,109 passed / 404 integration deselected / 3 subtests**,
전용 합성 PostgreSQL **403 passed / 1,827 deselected** (316.67초)가 통과했다. 전체 PG 명령은
API/Worker 경로이며 기본 pytest는 scripts 경로도 포함한다. 별도 빈 합성 DB에서
`head → 0039 → head` 왕복을 통과했다. 생성 계약·container/workflow 정책·문서 **50개**·안전
**817 paths**·diff도 통과했다. HTTP/UI 변경이 없어 기존 Chromium mock 결과는 이전 증거로
유지하고 이번에 반복하지 않았다. 실제 자료/PDF/OCR·외부 AI·운영 schema/data·Windows/모바일·
태그·배포는 미실행이다. 약관 분류/본문 범위와 다중 근거 적용 연결·보호된 수용은 B02 후속이다.

## Source-bound terms applicability

`0042_terms_applicability`와 API projector는 증권 Evidence의 실제 계약/피보험자·version·
페이지에 연결된 metadata로 적용 판본을 대조한다. 같은 보험사의 명시적 적용 약관/판본
참조가 모두 맞거나 상품코드·실제 계약일·인쇄 적용기간이 맞으면 `MATCH`다. 표시명·일반
참조·판본일만으로 적용을 확정하지 않는다. 결정적 모순, 정보 부족, 사용자 선택과 복수 판본
모호성을 구분하며 원래 source publication·입력 digest·판정 revision을 불변 이력에 보존한다.

보장 시작일에서 유래한 원장 날짜는 실제 range/legacy publication에서 식별하며 기존 원장
날짜를 재작성하지 않는다. 조항 확인·규칙 게시·현재 판정/계산은 같은 적용 gate를 소비하고,
과거 규칙 버전 ID 조회는 보존한다. 기존 사용자 set item/actor를 만들지 않는다. 문서 보유 및
통합 원장 조회는 현재 `MATCH`를 반영하고 Web은 자동 적용과 사용자 선택을 구분한다.
기존 증권 fallback도 실제 Evidence의 확인 출처를 표시한다. neutral inventory schema,
OpenAPI와 생성 TypeScript에 읽기 전용 `terms_applicability` 및 검증 출처가 추가됐다.

`57c513f` 위의 해당 API/migration/Web/계약/테스트 변경으로 적용 연결 부재, 보유 현황,
가짜 사용자 확인 출처, 표시명/날짜 중복 gate, 판정·계산 소비를 RED로 재현하고 수정했다.
실제 합성 range와 legacy publication에서 날짜 출처를 검증했다. 정적 리뷰에서 찾은 이력별
반복 계산은 DB 호출 계수 **8 대 1** RED, 시간 초과의 뒤 계약 처리 중단은 실제 PostgreSQL
`statement_timeout` RED, 날짜 호환 경로의 보장 시작일 차용은 **True 대 False** RED로
확인한 뒤 수정했다. 현재 조회는 범위별 근거를 한 번 계산하고 실패 계약은 30초 후 재시도한다.
관련 PostgreSQL **27개**가 이력/사용자 결정/동시성/rollback/범위/날짜/소비 경로를 포함해
2026-09-08 07:28~07:29 KST 통과했다. 첫 호출 계수 테스트는 함수 인자명 불일치로 실패했고
fixture를 수정한 뒤 실제 8회 호출 RED를 확인했다.

Web 전체 **172개·build**와 Chromium mock **17개**가 통과했다. 최초 Web 검사는 두 변경
파일의 Prettier 형식으로 중단됐고 해당 파일만 정리한 뒤 전체 검사를 통과했다. 별도 빈 합성
DB의 `head → 0039 → head`도 통과했다. 전체 기본 Python **2,165 passed / 431 integration
deselected / 3 subtests**, 전체 합성 PostgreSQL **430 passed / 1,883 deselected** (342.14초)가
07:29~07:37 KST 통과했다. Ruff format **633 files**/lint·mypy **261 sources**, 생성 계약·
container 정의·workflow 정책·diff가 통과했다. 전체 PG는 API/Worker, 기본 pytest는 scripts도
포함한다. 컨테이너 정의 검사는 이미지 build가 아니다.
이 변경에서는 실제 자료·외부 provider·운영 schema/data·Windows/모바일·태그·배포를 실행하지
않았다. 더 넓은 metadata/본문 범위와 변경·갱신 적용, 보호된 수용과 B02 merge는 남아 있다.

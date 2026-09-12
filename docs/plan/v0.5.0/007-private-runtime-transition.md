# v0.5 B07: Existing data transition and recovery

- 상태: complete (전환·보존·명시 수용 범위) / PARTIAL (자료 지원) — 최종 barrier·activation·HTTPS 수용 완료
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP09 #69](https://github.com/jihoon22-lee/family-care/issues/69)
- 기반: B01–B06 통합 코드, [PR #81](https://github.com/jihoon22-lee/family-care/pull/81) merge `f59c8a9e989ea2822ec6e57174a1a7055948828b`, CI 34306662287 필수 7/7 통과.
- 요구사항 R02/R17/R18/R19/R20, 시나리오 S03/S04/S09/S11/S14.

## Tasks

1. complete — API/Worker는 설치된 지원 schema와 필수 계약을 검사한다. PR84의 개인정보 변경은 0066이며, 이를 포함한 PR85 원문 근거 후속 코드는 0068(metadata v9·retained v3)을 요구한다. 기존 보호 환경의 0064/0065 수용 및 PR84 검증과 별도로 확인한다. 비밀/개인 자료 runner 생성 전·다음 작업 전과 API 업무 경로를 막으며 AI-off는 정상 준비 상태를 유지한다.
2. complete — 기존 backup/import/structure/projector를 재사용한 격리 복원·재구성 계획, source→destination journal, 교정/이력 보존 비교와 source 변경 거부를 구현했다. 실제 activation barrier 적용은 Task 6에 남긴다.
3. complete — 실제 PostgreSQL custom dump 복원, archive/key 복구, 저디스크·부분 원문 누락·중단/재개·동시 변경 거부를 합성 자료로 검증했다. 전체 행·검수 기반 청구 snapshot·암호화 archive roundtrip과 metadata 이력 보존/downgrade 거부를 통과했다.
4. complete — 승인된 기존 WSL 자료와 실행체를 보호 환경에서 inventory하고 일관된 백업을 취득했다. 별도 DB/보관소 복원과 전체 원문 복호화·hash 대조 및 0063 migration 후 기존 행 보존을 확인했다. 실제 값은 공개 artifact에 기록하지 않는다.
5. complete (검증) / PARTIAL (자료 지원) — 격리 복원에서 재구성·교정/청구 snapshot 비교·원문 접근·AI-off 결과·인증된 앱 경로를 검증한다. 첫 부분 구조와 검증된 표 앞부분의 metadata를 보존하며 v8/0064로 이전 이력과 구분한다. 별도 clone의 인증된 ASGI·원문 발췌·기존 사건 로컬 안내/저장 결과 조회를 통과했다. 브라우저·원본 별칭 연결·최종 전환은 별도 확인하며 불완전 자료를 빈 성공으로 처리하지 않는다.
6. complete (운영 전환·재시작 검증) / PARTIAL (자료 지원) — 2370761/0069와 동일 source의 검증한 세 digest로 전환했다. 기존 writer 중지 후 원본 0024/HMAC 일치, 원래 DB 컨테이너·마운트·원문/이력 보존, 재시작 후 health·socket·실제 HTTPS 조회·새 이미지의 원문 복호화/hash를 확인했다. 자동 약관·별칭 연결과 전체 운영 장애 복구 훈련은 완료로 집계하지 않는다.

## Earlier Task 5 implementation notes

Task 5 후속 구현은 보관 구조의 피보험자 복합 필드와 명시적 재처리 경로를 다룬다. 이름은
기존 구성원과 정확히 비교하며 허용된 부가 필드에서 새 개인정보를 만들지 않는다. 재처리는
기존 작업·원문·교정·완료 범위를 보존한 새 작업으로 구분하고, 현재 원문 세대와 선택한 작업을
고정한다. 기본 Worker가 이 작업을 자동 소비하지 않도록 하며, 검증 전 실제 처리 성공으로
집계하지 않는다. 과거 패키지의 PDF 별칭 연결과 원문에서 직접 만드는 가입 근거는 각각
수용한다. 별칭 대응표가 없다는 사실만으로 직접 원문 처리까지 중단하지 않는다.

Task 5의 아래 추가 재사용 경로는 당시 in_progress였으며 최신 실행은 0082 갱신에 기록한다. 원본 provider 응답과 동일한 최소화 원문을
고정하고, 근거 없는 선택 필드는 별도 파생 초안에서 제외한다. 새 retained v4 작업의
불변 receipt에 손실을 기록하며 독립 검수·프로그램 검증·기존 게시 경계를 다시 통과한다.
원본 응답을 현재 prompt의 cache hit로 취급하거나 기존 요청 예산을 초기화하지 않는다.

약관 후속 작업에서는 원문 줄 생성기와 근거 검증기의 기하 기준 차이를 해결한다. 새
metadata v9와 독립 API/후속 source 검증을 연결하고, 개인정보 migration 0066 뒤의 0067로
과거 v1–v8 이력을 보존한 동일 원문 재처리를 진행한다. 최소화 v3와 retained 처리 v2는
유지한다. PR84 변경 통합과 migration 순서 정리는 진행 중인 Task 5 범위이며, 통합된
0067의 관련 PostgreSQL 21건과 후속 날짜/가입 38건, 0068의 새/역사 재처리 8건과
확장 37건·readiness 24건을 통과했다. 통합 0068 소스의 기본 Python 3716건/3 subtests,
동일 Web 입력 238건/build·계약·정적 정책 검사도 통과했다. 전체 PG/이미지 CI와 실제 약관 지원률·판본
등록·의미 지식 수용은 별도로 확인한다.

## Decisions and boundaries

새 pipeline을 live DB에 부분 활성화하지 않는다. 기존 원문·추출물·교정·사건·청구와 검수 이력을
포함한 별도 복원 DB에서 기존 importer와 projector를 실행하고, 전후 비교를 통과한 기준점을
전환한다. 처리 중 active generation 변경은 격리 대상에만 영향을 준다.

전환 직전 기존 writer를 중지하고 source 기준점이 변하지 않았는지 확인한다. 이미 실행 중인
구 Worker를 새 readiness 검사로 통제할 수 있다고 가정하지 않는다. 변경이 발견되면 다시
대사하며 새 사용자 기록을 덮는 rollback/downgrade를 수행하지 않는다.

DB dump의 HMAC은 무결성 검증이다. 평문 dump는 저장소 밖 0700 디렉터리/0600 파일로만
보관하고 외부로 전송하지 않는다. archive master key는 백업 묶음과 별도로 재사용한다.
백업 포장 성공, 실제 DB 복원 성공, 원문 복호화/앱 수용 성공을 각각 구분한다.

실제 수용·운영 전환은 실행한 뒤에만 완료로 표시하고 해당 소스·이미지·환경을 함께 기록한다.

## 0069 protected acceptance update

PR #84는 `4440e33`, #85는 `1df428e`, #86은 `c8938ba`로 병합했다. #86의 최종
`49e5679`/CI 34356951882는 필수 7/7과 PostgreSQL 822건을 통과했다. `a6445be`/0069의
격리 DB·새 clone에서 기존 행/교정/청구 이력과 원문 식별·hash를 보존했다. 원본 응답을
로컬로 축소하고 실제 verifier 1회 후 프로그램 검사·선택 후보 원장 반영을 확인했다.
미증명 금액·날짜를 추가하지 않았고 과거 v2/v3와 범위 손실을 보존했다.

인증된 AI-off 앱·원문·이력과 Windows Chrome 사용 경로, `49e5679`의 첫 프로세스 조회·
반복 조회·저장 원문 검증/합성 native 추출 경합·idle을 측정했다. 전체 제공 source identity와
모든 시도·현재 실패, native/기존 catalog 담보와 연결 미해결을 별도 분모로 남긴다. 자동 약관
판본/의미 지식은 이 보호 자료에서 등록하지 못했으며 전체 지원은 PARTIAL이다. 이를 성공으로
숨기거나 원문 전체를 사용자 재검수 과제로 돌리지 않는다. Task 6의 실제 writer barrier·
최종 image/DB/archive/key 전환과 재시작 증거는 실행 뒤에만 추가한다.


## Earlier runtime deployment acceptance

Task 6의 실제 실행·릴리스의 수동 노트 복구·Windows 임시 세션 수용과 한계는
[최종 수용 기록](../../release/v0.5.0-verification.md#v052-publication-and-wsl-deployment)에
연결했다. 보존된 원본 0024와 활성 대상 0069를 분리하며 새 쓰기 뒤 원본 DB로 자동
되돌리지 않는다. digest 고정 관리 명령은 비공개 운영 설정에 설치했다. Task 5의 자료
지원 PARTIAL과 #69의 전체 새 구조 연결 수용은 열린 상태다.

## 0082 protected acceptance update

2026-09-13 기준 `39a63bcf4e3ed61eb0be44c0f27ab6eebf1205ed`의 격리 DB를
`0082_proven_draft_context`로 전환했다. 기존 owned clone의 함수·constraint를 갱신하고
이전 원문·처리·후보·교정·게시·청구 행을 보존했다. 이는 기존 source `2370761`/schema 0069의
실행 앱을 전환했다는 뜻이 아니며 최종 이미지·운영 배포는 PENDING이다.

선택 범위 v13은 새 verifier 3회로 37개 담보를 게시해 해당 계약을 16→53개로 늘렸다.
별도 계약의 기존 9개와 과거 이력을 유지했다. 전체 추가 승인 예산 원장은 HTTP 22회,
USD 0.52531240 사용량 기준 누계이며 기존 문서/일일 예약과 별도로 관리했다. 이 비용을
과거 고정 20건 검수 평가에 합산하거나 모델 품질 점수로 쓰지 않는다.

기존 2개 문서 출처 연결에 정확한 원문 프로필의 선언 1개를 기존 repository로 추가했다.
기존 bindings와 원장·검수·청구 이력은 불변이며 새로운 가입/담보와 canonical 갱신은 이
선언 작업에서 만들지 않았다. 이후 인증·원문 발췌·AI-off 조회·저장·이력 보존은 67.883초에 통과했다.
선택 canonical은 50/53개(전체 59개), 결합 100개는 공통 identity로 50개가 됐다.
대표 입력은 total 191/evaluated 0/unsupported 191/candidate 0이다. 분모는 private 94 +
operational 147 − canonical 50이며 해당 구성원의 실행 규칙·계산이 없는 지원 한계다.
다른 두 구성원의 기존 규칙/계산을 사용하는 대표 앱 수용은 별도로 확인한다. 이전 helper 실패/복구는
[최종 수용 기록](../../../workthrough/2026-09-13-source-unit-currency.md)에 함께 기록한다.

#69의 전환·보존 수용과 자료 지원률을 분리한다. 약관 신원·일부 연결이 PARTIAL이어도
그 상태와 전체 분모를 보존하는 전환은 평가할 수 있다. 모든 자료의 자동 해석 완료나
모바일 실기기 확인을 새 전환 선행 조건으로 만들지 않는다. 실제 최종 배포는 해당
소스·schema·이미지·전후 보존·재시작 결과를 받은 뒤에만 완료 처리한다.

## 0083 transition candidate

최종 후보 source는 `54496fa915b80e2dcdb043268473eb3ffe72399a`, schema는
`0083_source_unit_currency`다. 같은 owned clone에서 원문 단위에 의한 금액/통화 초안
보강을 적용했다. schema 준비는 1.762초, 실제 verifier 1회는 43.978초였으며 누적 예산은
HTTP 23회 / USD 0.57333290이다. 기존 12개 금액/통화 보강과 담보 1개 추가로 선택
원문의 54개 가입을 native에 반영했다. 다른 필드와 원래 이력·8개 REVIEW 범위는 보존했다.

canonical 갱신은 24.044초에 통과했다. 변경 11개는 기존 10개 갱신과 신규 1개이며 선택
51/54개·전체 60개(다른 계약 9개)다. 금액 충돌 0개, 표시명 충돌 6개와 원래 인용/identity
한계의 미연결 3개를 유지한다. range 유래 두 계약의 native 54+9개를 legacy 원장까지
포함한 전체 담보 수로 표시하지 않는다.

`54496fa`/0083의 지원 규칙이 있는 구성원에서 기존 입력을 복제한 사건 1개를 확인했다.
59.831초에 후보 6개·POINT 3개·FORMULA 1개·RANGE 0개, support 238개 중 evaluated 5개/
unsupported 233개였다. 실패 코드 0개, SUMMARY 근거 2개 열람, 저장 재조회 일치·기존
사건 불변·청구 조회·새 사건 soft delete·logout과 외부 HTTP 0을 확인했다. 마지막 helper는
정상 LOCAL_SEARCH_ONLY/SUCCEEDED/attempts 0 행을 provider 증가로 세어 실패했다.
새 분석 없이 수행한 후속 대사에서 provider 관련 5개 job table의 신규 행·work state·
attempts는 모두 0이었다. 신규 assistance job 1개는 정상 로컬 검색 행으로 확인했다.
원래 source event API 동등성은 직접 통과했고, 보조 root-delta 기준의 40개 table/116,681개
기존 행도 missing 0/changed 0이었다. 추가 10개는 복수 root 작업의 delta이며 단일 앱의
전후 증거로 확대하지 않는다. 처음의 FAILED 기록은 남기되 AI-off·기존 이력 조건은 해소했다.

최종 운영 전환은 PENDING이다. 아래 사전 병합 전에는 live에 이후 생긴 분석/청구 결과
약 9천 행의 원래 key 보존이 미완료였으며 이 조건은 다음 기록에서 해소했다. 기존 Rider 7개 차이는 승인된 금액 보강·독립 API 원문
증명으로 확인했고 사용자 보험정보 교정 충돌은 확인되지 않았다. session 상태와 약관 확인
시각 차이를 보험정보 변경으로 합치지 않는다. 새 live 이력 보존, 전환 직전 source barrier와
배포 후 재시작·조회 결과를 확인하기 전에는 격리 DB 준비를 운영 전환 완료로 표시하지 않는다.

## Final release and activation preparation

PR #106은 `34df397`/필수 CI 7/7·PG 944개, PR #107은 `7d1a53d`/필수 CI 7/7·PG
947개로 통합됐다. 태그 `v0.5.0`은 `7d1a53d`를 가리키며
[release 34719394060](https://github.com/jihoon22-lee/family-care/actions/runs/34719394060)는
SUCCESS이며 [GitHub Release](https://github.com/jihoon22-lee/family-care/releases/tag/v0.5.0)는
2026-09-12T22:00:32Z에 정식 게시됐다. 인증된 manifest/digest 확인 뒤 이미지 순차 pull이
완료됐으며 `7d1a53d`/0083 activation이 성공했다. 세 이미지 digest/리비전/health와
API readiness가 일치했고 별도 restart는 실행하지 않았다. 외부 네트워크 수용도 아래 범위에서 통과했다.

사전 live→owned83 이력 병합은 16.128초에 9,208행 INSERT로 완료했다. 첫 시도는 동등한
로컬 작업의 unique key 충돌로 전체 rollback됐으며 실패를 보존했다. 후속에서는 정확히
같은 household/event/version/candidate digest·terminal local 상태인 작업 6개의 원행과
원래 ID/시각을 보호 manifest에 남겼다. 새 run 12개를 INSERT할 때만 기존 동등 job을
참조했고, 원래 run/decision/result/claim ID·본문과 기존 owned 행을 보존했다. live 쓰기,
UPDATE/DELETE, 제약 우회는 없었다. 명시 alias를 적용한 비교의 누락은 0이었다.

이 사전 병합은 최종 writer barrier를 대신하지 않는다. 전환 직전 새 missing 행의 재대사,
원래 live 이력·원문·키·교정 보존, 네트워크 경로와 순차 up으로 생성한 새 컨테이너의 실제 activation·인증 조회 결과를
주 작업의 실행 증거에 연결한다. 최종 barrier의 history 9개 table 재호출은 16.268초에
추가 INSERT 0·누락 0·source/owned 기존 행 보존으로 통과했다. 나머지 105개 table의
live 35,160행도 보존했고 source app_sessions 19행·refresh 시각 20행·승인 Rider
금액 보강 7개를 명시적으로 대사했다. 총 114개 table 확인을 완료했다. 새 stage를
활성화하고 이전 DB는 보존했으며 네트워크 수용도 통과했다.

최초 네트워크 helper는 Windows curl 실행의 `OSError errno 8`로 HTTPS 요청 전에 실패했다.
HTTPS 요청은 0이었다. 버전 실행 실패를 감지한 뒤 WSL curl을 선택해 영향받는 네트워크
읽기 조회만 다시 실행했으며 재배포·restart·재분석은 없었다.
이 실패는 서비스 자체의 HTTPS 응답 실패나 실제 Windows 브라우저 수용으로 집계하지 않는다.

최종 WSL curl 수용은 30.765초에 HTTPS 10회·readiness 1회를 통과했다. 인증·schema 0083·
이미지 3개·health를 확인했고, 기존 저장 결과는 후보 6개·POINT 3개·FORMULA 1개·RANGE
0개, support 238=evaluated 6+unsupported 232였다. `saved_guidance_stale=true`인 과거
저장 결과 조회이므로 최신 입력으로 새로 분석한 결과나 앞선 fresh 수용(5+233)의 재현으로
보고하지 않는다. SUMMARY 근거 1개·청구·원래 사건 보존·no-store·logout도 통과했다.
provider activity와 local projection delta는 불변, 새 event·analyze·restart는 모두 0이었다.

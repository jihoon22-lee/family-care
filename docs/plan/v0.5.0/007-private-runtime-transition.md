# v0.5 B07: Existing data transition and recovery

- 상태: in_progress
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
6. in_progress — 검증한 기준점을 재확인해 전환하고 재시작·복구를 확인한다. 코드 `04fbbda`의 CI 34318679642 필수 7/7과 PR #82 merge `18dc981`은 완료했다. 실제 자료는 terminal PARTIAL이며 원문 연결 수용·activation을 완료로 집계하지 않는다. B08 수용표에 이 경계를 인계한다.

Task 5 후속 구현은 보관 구조의 피보험자 복합 필드와 명시적 재처리 경로를 다룬다. 이름은
기존 구성원과 정확히 비교하며 허용된 부가 필드에서 새 개인정보를 만들지 않는다. 재처리는
기존 작업·원문·교정·완료 범위를 보존한 새 작업으로 구분하고, 현재 원문 세대와 선택한 작업을
고정한다. 기본 Worker가 이 작업을 자동 소비하지 않도록 하며, 검증 전 실제 처리 성공으로
집계하지 않는다. 과거 패키지의 PDF 별칭 연결과 원문에서 직접 만드는 가입 근거는 각각
수용한다. 별칭 대응표가 없다는 사실만으로 직접 원문 처리까지 중단하지 않는다.

Task 5의 추가 재사용 경로는 in_progress다. 원본 provider 응답과 동일한 최소화 원문을
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

현재 단계는 공개 구현·계획이다. 실제 수용·운영 전환은 실행한 뒤에만 완료로 표시한다.

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

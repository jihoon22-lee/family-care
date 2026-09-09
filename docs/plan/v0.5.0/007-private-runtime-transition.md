# v0.5 B07: Existing data transition and recovery

- 상태: in_progress
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP09 #69](https://github.com/jihoon22-lee/family-care/issues/69)
- 기반: B01–B06 통합 코드, [PR #81](https://github.com/jihoon22-lee/family-care/pull/81) merge `f59c8a9e989ea2822ec6e57174a1a7055948828b`, CI 34306662287 필수 7/7 통과.
- 요구사항 R02/R17/R18/R19/R20, 시나리오 S03/S04/S09/S11/S14.

## Tasks

1. complete — API/Worker는 설치된 지원 schema와 필수 계약을 검사한다. PR84의 개인정보 변경은 0066이며, 이를 포함한 PR85 약관 후속 코드는 0067을 요구한다. 기존 보호 환경의 0064/0065 수용 및 PR84 검증과 별도로 확인한다. 비밀/개인 자료 runner 생성 전·다음 작업 전과 API 업무 경로를 막으며 AI-off는 정상 준비 상태를 유지한다.
2. complete — 기존 backup/import/structure/projector를 재사용한 격리 복원·재구성 계획, source→destination journal, 교정/이력 보존 비교와 source 변경 거부를 구현했다. 실제 activation barrier 적용은 Task 6에 남긴다.
3. complete — 실제 PostgreSQL custom dump 복원, archive/key 복구, 저디스크·부분 원문 누락·중단/재개·동시 변경 거부를 합성 자료로 검증했다. 전체 행·검수 기반 청구 snapshot·암호화 archive roundtrip과 metadata 이력 보존/downgrade 거부를 통과했다.
4. complete — 승인된 기존 WSL 자료와 실행체를 보호 환경에서 inventory하고 일관된 백업을 취득했다. 별도 DB/보관소 복원과 전체 원문 복호화·hash 대조 및 0063 migration 후 기존 행 보존을 확인했다. 실제 값은 공개 artifact에 기록하지 않는다.
5. in_progress — 격리 복원에서 재구성·교정/청구 snapshot 비교·원문 접근·AI-off 결과·인증된 앱 경로를 검증한다. 첫 부분 구조와 검증된 표 앞부분의 metadata를 보존하며 v8/0064로 이전 이력과 구분한다. 별도 clone의 인증된 ASGI·원문 발췌·기존 사건 로컬 안내/저장 결과 조회를 통과했다. 브라우저·원본 별칭 연결·최종 전환은 별도 확인하며 불완전 자료를 빈 성공으로 처리하지 않는다.
6. pending — 검증한 기준점을 재확인해 전환하고 재시작·복구를 확인한다. 코드 `04fbbda`의 CI 34318679642 필수 7/7과 PR #82 merge `18dc981`은 완료했다. 실제 자료는 terminal PARTIAL이며 원문 연결 수용·activation을 완료로 집계하지 않는다. B08 수용표에 이 경계를 인계한다.

Task 5 후속 구현은 보관 구조의 피보험자 복합 필드와 명시적 재처리 경로를 다룬다. 이름은
기존 구성원과 정확히 비교하며 허용된 부가 필드에서 새 개인정보를 만들지 않는다. 재처리는
기존 작업·원문·교정·완료 범위를 보존한 새 작업으로 구분하고, 현재 원문 세대와 선택한 작업을
고정한다. 기본 Worker가 이 작업을 자동 소비하지 않도록 하며, 검증 전 실제 처리 성공으로
집계하지 않는다. 과거 패키지의 PDF 별칭 연결과 원문에서 직접 만드는 가입 근거는 각각
수용한다. 별칭 대응표가 없다는 사실만으로 직접 원문 처리까지 중단하지 않는다.

약관 후속 작업에서는 원문 줄 생성기와 근거 검증기의 기하 기준 차이를 해결한다. 새
metadata v9와 독립 API/후속 source 검증을 연결하고, 개인정보 migration 0066 뒤의 0067로
과거 v1–v8 이력을 보존한 동일 원문 재처리를 진행한다. 최소화 v3와 retained 처리 v2는
유지한다. PR84 변경 통합과 migration 순서 정리는 진행 중인 Task 5 범위이며, 통합된
0067의 전체 검사·전용 PostgreSQL 검증은 아직 실행하지 않았다. 실제 약관 지원률·판본
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

# v0.5 B07: Existing data transition and recovery

- 상태: in_progress
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP09 #69](https://github.com/jihoon22-lee/family-care/issues/69)
- 기반: B01–B05 통합 코드와 B06 검증 중인 source `dcd52f0`; B06 최종 merge를 반영한 뒤 B07 PR을 완성한다.
- 요구사항 R02/R17/R18/R19/R20, 시나리오 S03/S04/S09/S11/S14.

## Tasks

1. in_progress — 설치된 API/Worker의 지원 schema와 필수 읽기/쓰기 계약을 검사한다. Worker는 비밀/개인 자료 runner 생성 전과 다음 작업 시작 전 검사하며 AI-off는 정상 준비 상태를 유지한다.
2. in_progress — 기존 backup/import/structure/projector를 재사용할 격리 복원·재구성 계획과 source→destination journal, 교정/이력 보존 비교 및 activation barrier를 구체화한다.
3. pending — 실제 PostgreSQL custom dump 복원, archive/key 복구, 저디스크·부분 원문 누락·중단/재개·동시 변경 거부를 합성 자료로 검증한다.
4. pending — 승인된 기존 WSL 자료와 실행체를 보호 환경에서 inventory하고 일관된 백업을 취득한다. 실제 값은 공개 artifact에 기록하지 않는다.
5. pending — 격리 복원에서 재구성·교정/청구 snapshot 비교·원문 접근·AI-off 결과·인증된 앱 경로를 검증한다. 불완전 자료를 빈 성공으로 처리하지 않는다.
6. pending — 검증한 기준점을 재확인해 전환하고 재시작·복구를 확인한다. 필수 검사·PR/CI/merge·보호된 수용 상태를 B08에 인계한다.

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

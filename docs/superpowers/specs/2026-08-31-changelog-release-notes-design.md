# Changelog-derived GitHub Release Notes Design

- 상태: PR #49 구현·기존 `v0.1.0`~`v0.3.2` Release 정비와 후속 job-level
  `runner.temp` GitHub parser regression 수정 완료; 다음 실제 tag run 미실행
- 작성일: 2026-08-31
- 적용 범위: `CHANGELOG.md`, 릴리스 노트 생성 도구, GHCR 태그 검증, GitHub Release 게시, 기존 v0.1.0~v0.3.2 본문 정비

## 목적

FamilyCare의 GitHub Release 본문을 버전마다 수기로 다시 쓰거나 GitHub 자동 PR 목록으로 생성하지 않는다. 태그에 포함된 `CHANGELOG.md`의 해당 버전 섹션을 사용자 영향 변경사항의 단일 원본으로 사용하고, 검증된 워크플로·커밋·컨테이너 digest 정보만 별도 증거 섹션으로 추가한다.

## 구현 전 확인된 문제와 현재 결과

- v0.1.0과 v0.2.0의 서로 다른 수기 양식, v0.3.0/v0.3.1의 PR 목록, v0.3.2의 문자
  그대로인 `\n`은 PR #49 merge 뒤 공통 CHANGELOG-derived 본문으로 교체했다.
- 다섯 Release를 API로 다시 읽어 `Changes`, `Release evidence`,
  `Privacy and deployment boundary`, 실제 줄바꿈과 서로 다른 세 image digest를 확인했다.
- renderer, digest evidence, release audit, workflow policy와 v0.1.0 CHANGELOG 정규화는 구현됐다.
- 후속 검토에서 확인한 `publish-release` job-level `env`의 `${{ runner.temp }}` 오류는 해당
  경로를 step-level `env`로 이동하고 저장소 validator에 회귀 검사를 추가해 수정했다. 기존
  Release 정비 결과는 바뀌지 않았고, 다음 실제 tag-run 검증은 아직 수행하지 않았다.

## 결정

### 변경사항의 단일 원본

`CHANGELOG.md`의 `## [MAJOR.MINOR.PATCH] - YYYY-MM-DD` 아래에서 다음 버전 제목 직전까지를 정확히 추출한다. 추출 결과의 `### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, `### Security` 순서와 문장은 변형하지 않는다.

다음 입력은 게시 전에 실패한다.

- 버전 섹션이 없거나 중복됨
- semantic version 또는 ISO 날짜 형식이 아님
- 허용되지 않은 3단계 제목이 존재함
- 분류 제목 아래 항목이 없음
- 본문에 문자 그대로의 `\n`이 존재함

v0.1.0 역사 섹션은 사용자 영향 중심으로 한 번 정리한다. 상세 PR·테스트·운영 증거는 `docs/release/v0.1.0-verification.md`에 유지하고 CHANGELOG에는 제품 변화와 보안 경계만 남긴다.

### 공개 Release 본문 형식

모든 릴리스 본문은 다음 순서를 사용한다.

1. `## Changes` 아래에 CHANGELOG 버전 섹션을 그대로 삽입
2. `## Release evidence` 아래에 전체 커밋 SHA와 GitHub Actions 실행 URL 기록
3. Web/API/Worker의 immutable digest reference를 정확히 한 개씩 기록
4. `## Privacy and deployment boundary`에서 GHCR 게시와 운영 배포를 구분

Release 본문은 mode `0600` 임시 Markdown 파일로 생성하고 `gh release create|edit --notes-file`로만 전달한다. 셸 인라인 문자열과 `--notes`는 사용하지 않는다.

### 이미지 증거 계약

기존 registry 검증은 version tag와 12자리 SHA tag가 같은 digest를 가리키는지 계속 확인한다. 성공한 경우에만 다음 공개 가능한 JSON을 mode `0600` 파일로 쓴다.

```json
{
  "schema_version": "release-image-evidence.v1",
  "version": "0.3.2",
  "commit_sha": "1111111111111111111111111111111111111111",
  "images": [
    {"component": "web", "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    {"component": "api", "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
    {"component": "worker", "digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}
  ]
}
```

검증 finding이 하나라도 있으면 증거 파일과 GitHub Release를 만들지 않는다. token, registry response body, 실제 경로, 개인 데이터는 JSON과 로그에 포함하지 않는다.

### Workflow 권한과 순서

새 `publish-release` job은 `verify-publication` 성공 후에만 실행한다.

- `contents: write`: `publish-release` job에만 허용
- `packages: read`: registry digest를 다시 확인하는 `verify-publication`과 `publish-release`에만 허용
- `packages: write`: 기존 `publish` job에만 허용
- 장기 비밀값 없이 `github.token`만 사용

job은 태그 checkout에서 image evidence와 Release Markdown을 다시 생성한다. 동일 태그의 Release가 이미 있으면 같은 생성물로 수정하고, 없으면 `--verify-tag`로 생성한다. 부분 publish나 digest mismatch 상태에서는 공개 Release를 만들지 않는다.

### 기존 릴리스 정비

코드와 workflow 변경이 PR·CI·main merge를 통과한 뒤 v0.1.0~v0.3.2 각각에 대해 다음을 수행한다.

1. 현재 정규화된 CHANGELOG에서 버전 섹션 추출
2. 해당 태그의 실제 commit과 성공한 release workflow 확인
3. GHCR version/SHA tag digest 재검증
4. mode `0600` 임시 파일에 동일 양식 본문 생성
5. `gh release edit --notes-file`로 갱신
6. GitHub API에서 본문을 다시 읽어 CHANGELOG 포함, digest 3개, literal `\n` 부재 확인

태그, 커밋, 이미지, 배포된 FamilyCare runtime은 변경하지 않는다.

## 실패 처리

- CHANGELOG 파싱 오류: 안정적인 오류 메시지와 exit 1, 출력 파일 없음
- image evidence 불일치: 기존 stable finding과 exit 1, 출력 파일 없음
- 기존 Release 갱신 실패: 다음 버전을 진행하지 않고 이미 갱신된 버전과 실패 버전을 보고
- 임시 파일: 성공·실패 모두 정확한 작업 디렉터리만 삭제
- 공개 본문 검증 실패: 재편집을 반복하지 않고 원문과 예상 digest를 대조

## 검증

- 합성 CHANGELOG로 추출·중복·누락·빈 분류·literal `\n` 테스트
- 합성 registry 응답으로 세 digest evidence JSON 테스트
- 생성된 Markdown이 CHANGELOG 섹션을 byte-for-byte 포함하는지 테스트
- mode `0600`, 정확히 세 이미지, 실제 줄바꿈, 개인정보 금지 패턴 테스트
- workflow policy에서 job 의존성·권한·`--notes-file` 사용 테스트
- 현재 CHANGELOG의 v0.1.0~v0.3.2 전체 렌더링 테스트
- 기존 필수 repository/Web/Python/PostgreSQL/container CI 유지

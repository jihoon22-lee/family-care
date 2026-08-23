# Security policy

FamilyCare는 보험과 의료 관련 정보를 다루므로 일반적인 개인 프로젝트보다 엄격한 제보 절차를 사용합니다.

## Reporting a vulnerability

저장소의 GitHub Security 탭에서 private vulnerability reporting이 제공되면 해당 기능을 사용합니다. 공개 이슈, 공개 Discussion, PR 본문에 취약점 재현용 비밀값이나 민감 데이터를 올리지 않습니다.

제보에 포함할 수 있는 내용:

- 영향을 받는 커밋 또는 버전
- 합성 데이터로 재현한 단계
- 예상 동작과 실제 동작
- 공격자가 필요한 권한과 도달 가능한 sink
- 개인정보 없이 작성한 영향 설명

실제 보험 문서, 의료정보, 인증 토큰, 실제 계정, 운영 URL 비밀값은 제보에 첨부하지 않습니다.

## Accidental sensitive-data exposure

실제 자료나 비밀값이 Git에 추가됐다고 의심되면 다음 순서를 따릅니다.

1. 추가 commit, push, PR 업데이트를 중단합니다.
2. 노출된 데이터 종류와 Git 도달 범위를 읽기 전용으로 확인합니다.
3. 관련 인증정보가 있으면 저장소 수정과 별개로 폐기·교체합니다.
4. 사용자와 저장소 관리자가 이력 정리 방식과 영향 범위를 승인합니다.
5. 공개 응답에는 실제 값을 다시 인용하지 않습니다.
6. 합성 회귀 검사와 예방 규칙을 추가합니다.

공유 이력은 사용자 승인 없이 force push하거나 다시 쓰지 않습니다.

## Supported versions

현재는 출시 전 Foundation 단계이므로 `main`의 최신 커밋만 보안 수정 대상입니다. 정식 버전 지원 범위는 첫 릴리스 전에 CHANGELOG와 함께 갱신합니다.

## Security boundaries

- 공개 CI에는 운영 비밀값이 없습니다.
- 실제 PDF와 파생 데이터는 저장소 밖에 있습니다.
- AI와 Google Drive는 Foundation에 연결하지 않습니다.
- GHCR 이미지 게시와 운영 배포는 별개의 완료 경계입니다.
- 발견 사항은 source에서 sink까지 확인하고, 실행하지 않은 동적 검증을 통과로 보고하지 않습니다.

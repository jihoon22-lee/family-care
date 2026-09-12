# Contributing to FamilyCare

FamilyCare는 공개 저장소이지만 실제 보험·의료 자료를 다루기 위한 코드는 엄격한 비공개 데이터 경계를 전제로 합니다. 기여하기 전에 `AGENTS.md`, 관련 `docs/design/`, `docs/plan/`을 읽어야 합니다.

## Workflow

1. 최신 `main`에서 `<type>/<kebab-case>` 브랜치를 만듭니다.
2. PR의 계획 범위를 정하고 구현·필요 테스트·문서를 함께 완성합니다. 개발 중에는 구체적 문제 해결에 필요한 최소 재현 검사만 실행합니다.
3. 커밋마다 변경 파일의 문법·형식과 계획 범위를 가볍게 확인합니다. 전체 테스트·통합·빌드는 커밋 조건이 아닙니다.
4. `<type>(<optional-scope>): <imperative description>` 형식으로 커밋합니다.
5. PR의 계획 작업이 모두 끝나면 상세 검증과 전체 diff 검토를 한 번 집중 수행하고 결과를 기록합니다. 동일 입력의 CI 결과를 활용하며, 실패 수정 후에는 영향받은 검사만 다시 실행합니다.
6. 세션의 요청이 merge를 포함하고 필수 GitHub Actions가 모두 성공한 뒤 merge합니다.
7. 승인된 merge·정리 작업에서는 merge를 확인한 뒤 완료된 원격·로컬 브랜치와 전용 worktree를 삭제합니다. 다른 세션이
   사용하는 worktree나 미병합 변경은 삭제하지 않습니다.

현재 마일스톤과 PR 묶음은 [로드맵](docs/plan/000-project-roadmap.md)을 확인합니다.
문서·설정·기능 변경의 검사 선택은 [검증 전략](docs/design/test-strategy.md#verification-by-change)을 따릅니다.

## Branch and commit conventions

브랜치는 `<type>/<kebab-case-description>` 형식입니다.

| Type | 목적 |
|---|---|
| `feat` | 사용자 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 전용 |
| `build` | 빌드·환경·컨테이너 |
| `ci` | CI/CD와 저장소 자동화 |
| `chore` | 제품 동작을 바꾸지 않는 유지보수 |
| `refactor` | 동작을 유지하는 코드 구조 변경 |
| `test` | 테스트 전용 |
| `release` | 릴리스 준비 |

예: `build/project-foundation`, `feat/policy-ledger`.
`main`, 대문자, 밑줄, 공백, 의미 없는 번호만 있는 이름은 feature 브랜치에 사용하지 않습니다.
GitHub Dependabot이 소유한 `dependabot/<ecosystem>/<slug>`만 자동화 예외이며 사람이 만든 브랜치에는 적용하지 않습니다.
사용자 요청 없이 force push하거나 공유 이력을 다시 쓰지 않습니다.

커밋 제목은 `<type>(<optional-scope>): <imperative description>` 형식입니다.

- 허용 type: `feat`, `fix`, `docs`, `build`, `ci`, `chore`, `refactor`, `test`, `perf`, `style`, `revert`.
- 영문 소문자 type으로 시작하고 72자를 넘지 않으며 마침표로 끝내지 않습니다.
- 하나의 커밋은 하나의 검토 가능한 목적이며 코드·관련 테스트·필요 문서를 함께 포함합니다.
- 호환성을 깨는 변경은 `type(scope)!:`와 `BREAKING CHANGE:` footer를 사용합니다.
- 임시 저장이나 의미 없는 메시지는 공유하지 않습니다. 커밋의 경량 확인과 PR 완료의 상세 검증을 구분하며, 상세 검증 전 커밋을 전체 검증 완료로 표시하지 않습니다.

예: `docs(plan): define foundation implementation`, `feat(api): add health endpoints`, `ci: validate repository safety`.

## Synthetic data only

fixture, 테스트, 문서 예시는 처음부터 만든 합성 데이터만 허용합니다.

허용 예:

- `Family Member A`
- `Sample Policy`
- `synthetic-policy-001`
- 실제 보험 문구를 복사하지 않고 작성한 짧은 가상 조항

허용하지 않는 예:

- 실제 문서에서 이름과 번호만 가린 자료
- 실제 상품명을 바꾼 PDF나 OCR 결과
- 실제 증권의 표 구조와 금액을 그대로 옮긴 fixture
- 실제 진단·치료 사건을 일부 변형한 테스트

합성 PDF는 `fixtures/synthetic/` 아래에만 두며 출처가 합성임을 README에 기록합니다.

## Pull requests

PR은 한 가지 검토 목적을 가져야 하며 다음을 설명합니다.

- 해결하는 문제와 제외 범위
- 변경한 주요 파일과 계약
- 실행한 정확한 검증과 결과
- 실행하지 못한 외부·플랫폼 검증
- 개인정보·로그·캐시 영향
- 스키마나 호환성 변경 여부

CI를 통과시키기 위해 테스트나 개인정보 검사를 약화하지 않습니다. 실패는 원인을 확인하고 필요한 회귀 사례와 함께 수정합니다. 영향 범위 밖의 성공한 검사를 반복하거나 확인 목적의 중간 push로 CI를 계속 재시작하지 않습니다.

## License

이 저장소에는 라이선스가 부여되어 있지 않습니다. 공개 접근 가능하다는 사실만으로 재사용·수정·배포 권한이 제공되지 않습니다. 외부 기여를 제출하기 전에 해당 기여를 이 저장소에 포함할 권한이 있는지 확인해야 합니다.

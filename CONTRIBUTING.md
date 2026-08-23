# Contributing to FamilyCare

FamilyCare는 공개 저장소이지만 실제 보험·의료 자료를 다루기 위한 코드는 엄격한 비공개 데이터 경계를 전제로 합니다. 기여하기 전에 `AGENTS.md`, 관련 `docs/design/`, `docs/plan/`을 읽어야 합니다.

## Workflow

1. 최신 `main`에서 `<type>/<kebab-case>` 브랜치를 만듭니다.
2. 기능 변경은 실패하는 테스트를 먼저 작성합니다.
3. 작은 논리 단위로 구현하고 관련 검증을 직렬 실행합니다.
4. `<type>(<optional-scope>): <imperative description>` 형식으로 커밋합니다.
5. PR 템플릿의 개인정보와 검증 항목을 실제로 확인합니다.
6. 필수 GitHub Actions가 모두 성공한 뒤 merge합니다.

허용된 브랜치 type과 커밋 type의 전체 목록은 `AGENTS.md`가 기준입니다.
`dependabot/<ecosystem>/<slug>` 형식은 GitHub Dependabot이 만든 브랜치에만 허용됩니다.

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

CI를 통과시키기 위해 테스트나 개인정보 검사를 약화하지 않습니다. 실패는 원인을 재현하고 회귀 검사를 추가한 뒤 수정합니다.

## License

이 저장소에는 라이선스가 부여되어 있지 않습니다. 공개 접근 가능하다는 사실만으로 재사용·수정·배포 권한이 제공되지 않습니다. 외부 기여를 제출하기 전에 해당 기여를 이 저장소에 포함할 권한이 있는지 확인해야 합니다.

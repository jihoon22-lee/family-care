# ADR 0002: Public repository data boundary

- Status: Accepted
- Date: 2026-08-23

## Context

GitHub Actions 비용과 공개 개발을 위해 소스 저장소는 public입니다. 실제 보험증권과 의료 사건에는 직접·간접 개인정보와 계약 정보가 포함되며, 추출 텍스트·OCR·임베딩도 원본과 같은 민감도를 가질 수 있습니다.

## Decision

공개 저장소에는 코드, 설계, 비밀값 없는 설정 예시, 처음부터 만든 합성 fixture만 허용합니다. 실제 문서와 모든 파생 데이터, 데이터베이스, 로그, 인증정보, Drive 식별자는 저장소 밖에 둡니다.

`.gitignore`, 저장소 안전 검사, secret scanning, PR checklist, CI를 중첩 적용합니다. 실제 데이터로 검증하는 단계는 사용자 승인 후 비공개 로컬 절차로 분리합니다.

## Alternatives

### Private repository

노출 가능성을 낮추지만 public 운영 결정과 맞지 않으며 잘못된 commit 자체를 막지는 못합니다.

### Redacted real fixtures

문서 현실성은 높지만 재식별과 원문 복제 위험이 있고 공개 경계를 불명확하게 만듭니다.

### Encrypted real files in Git

키 관리와 이력 노출 위험이 남고 공개 CI에 실제 자료가 도달할 수 있어 선택하지 않습니다.

## Consequences

- 파서 테스트용 문서를 별도로 합성해야 합니다.
- 실제 형식 정확도는 별도 acceptance 단계에서만 확인할 수 있습니다.
- 실제 자료를 직접 확인하지 않은 기능은 미검증으로 보고합니다.
- 민감정보 사고 시 이력 정리와 credential 회전을 별개로 수행합니다.

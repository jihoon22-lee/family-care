# FamilyCare glossary

이 문서는 코드, API, 화면, 설계에서 같은 의미를 사용하기 위한 용어집입니다.

| Term | 의미 |
|---|---|
| `AppUser` | FamilyCare에 로그인하는 사용자 계정. 보험 대상 가족과 별개입니다. |
| `HouseholdSpace` | 동일 권한 관리자 두 명이 공유하는 하나의 관리 공간입니다. 다중 가정 기능은 제공하지 않습니다. |
| `FamilyMember` | 보험 보장과 사건 검색의 대상이 되는 가족 구성원입니다. 로그인 계정이 아닐 수 있습니다. |
| `PolicyParty` | 특정 보험 계약에서 계약자, 주피보험자, 종피보험자, 수익자 역할을 연결하는 엔터티입니다. |
| `Document` | 저장소 밖 원본을 가리키는 메타데이터, 해시, 버전, 문서 종류, 처리 상태입니다. |
| `Extraction` | PDF 파서 또는 OCR이 만든 페이지별 구조화 후보와 품질·검수 상태입니다. |
| `PolicyContract` | 보험사, 상품 표시명, 계약일, 보험기간, 상태를 가진 계약 단위입니다. |
| `Rider` | 증권에서 실제 가입이 확인된 주계약 또는 특약과 가입금액·기간입니다. |
| `Clause` | 약관의 장·절·조·항 또는 별표와 페이지 근거입니다. |
| `CoverageRule` | Rider 지급 조건, 면책, 대기·감액기간, 횟수 제한, 계산식을 표현한 명시적 규칙입니다. |
| `Evidence` | 판정 입력이나 규칙을 재확인할 수 있는 문서, 페이지, 조항, 좌표 참조입니다. |
| `MedicalEvent` | 질병·상해, 진단, 수술, 입원, 통원 등 사용자가 검색하려는 사건입니다. |
| `ClaimCandidate` | 실제 가입 Rider와 사건·규칙을 비교해 만든 청구 검토 후보입니다. 지급 결정이 아닙니다. |
| `ClaimCase` | 준비, 접수, 보완, 심사, 지급, 거절 등 실제 청구 진행 기록입니다. |
| `PrivateKnowledgeSnapshot` | 저장소 밖에서 검토한 보험 지식을 lossless·immutable하게 보존하는 household-scoped catalog version입니다. Operational 원장을 덮어쓰거나 자동 실행 권한을 만들지 않습니다. |
| `Coverage disposition` | Private knowledge coverage의 실행 경계인 `PUBLISHED`, `ADVISORY`, `BLOCKED`, `NOT_APPLICABLE` 중 하나입니다. |
| `PUBLISHED` | 검토된 eligibility rule과 필요한 citation/calculation이 publication gate를 통과해 결정론적 평가에 사용할 수 있는 상태입니다. |
| `ADVISORY` | catalog와 관련 검색에는 포함하지만 eligibility는 `UNKNOWN`으로 유지하며 허용된 조건부 정보만 보여 주는 상태입니다. |
| `BLOCKED` | 실행 근거나 검토가 부족해 자동 규칙 평가에서 제외하는 상태입니다. |
| `NOT_APPLICABLE` | 검토 결과 benefit execution 대상이 아닌 상태입니다. `NO_MATCH`와 같지 않습니다. |
| 조건부 정액 추정 | 검토된 formula 또는 제한된 증권 가입금액 근거로 계산하지만 eligibility와 `confirmed_amount`는 확정하지 않는 금액 trace입니다. |
| `MATCH` | 현재 확인된 사실이 평가한 조건과 일치합니다. 전체 지급 확정을 뜻하지 않습니다. |
| `NO_MATCH` | 확인 가능한 근거가 평가한 조건과 결정적으로 일치하지 않습니다. |
| `UNKNOWN` | 입력, 근거, 계약 상태가 부족하거나 충돌해 해당 조건을 판단할 수 없습니다. |
| 정액형 | 계약에서 정한 금액이나 비율을 지급하는 담보 유형입니다. |
| 실손형 | 실제 부담 의료비, 자기부담금, 한도, 비례보상 조건으로 계산하는 담보 유형입니다. |
| 합성 fixture | 실제 문서나 사건을 변형하지 않고 테스트 목적으로 처음부터 만든 자료입니다. |
| lease | Worker가 제한된 시간 동안 작업 처리 권한을 소유하고 장애 시 회수할 수 있게 하는 장치입니다. |
| 멱등성 | 같은 작업을 여러 번 요청해도 중복 결과나 부작용이 생기지 않는 성질입니다. |

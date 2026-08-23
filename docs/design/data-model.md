# Data model design

- 상태: 설계 기준
- 적용 단계: Policy Ledger 이후

## Scope

보험 대상 가족, 계약, 문서, 가입 담보, 약관, 사건, 판정, 청구 이력을 서로 다른 수명주기와 증거 수준으로 관리하는 데이터 경계를 정의합니다. 이 문서는 물리 스키마보다 도메인 의미와 불변조건을 우선합니다.

## Inputs

- 검수된 문서 메타데이터와 페이지 Evidence
- 관리자 두 계정의 인증 주체
- 가족, 계약 당사자, 가입 Rider의 검수값
- 약관 조항과 규칙 버전
- MedicalEvent와 ClaimHistory

원시 추출값은 입력 후보이며 관리자 확정값과 구분합니다.

## Outputs

- 수명주기가 분리된 도메인 엔터티와 관계
- 필드별 Evidence와 검수 상태
- soft delete와 감사 가능한 상태 전이
- 판정 엔진이 읽을 시점 기준 계약·Rider 상태

## Identity boundary

### AppUser

앱에 로그인하는 계정입니다. Foundation 이후 허용된 관리자 두 명만 생성하며 같은 `HouseholdSpace`에 동일 권한으로 연결합니다.

핵심 필드:

- 내부 UUID
- 인증 제공자와 제공자 subject
- 표시 이름
- 활성 상태
- 생성·수정·비활성 시각

이메일은 인증 allowlist에 필요할 때 최소 수집하며 공개 fixture에 실제 주소를 넣지 않습니다.

### HouseholdSpace

두 관리자가 공유하는 하나의 논리 공간입니다. 다중 가정 기능은 구현하지 않지만, 가족 데이터가 전역 singleton에 묶이지 않도록 명시적 소유 경계를 둡니다.

### FamilyMember

보험 계약과 사건의 대상입니다. AppUser와 일치할 수도 있지만 외래키로 동일 엔터티를 재사용하지 않습니다.

핵심 필드:

- 내부 UUID
- HouseholdSpace UUID
- 표시 이름
- 가족 내 구분용 합성·내부 별칭
- soft delete 시각

실제 주민번호와 상세 주소는 핵심 모델에 저장하지 않습니다.

## Document boundary

### Document

저장소 밖 원본의 논리 식별자입니다.

- 문서 종류: policy, terms, application, amendment, claim, supporting
- 원본 제공자와 비공개 외부 참조
- SHA-256 콘텐츠 해시
- MIME, 크기, 페이지 수
- 문서 작성·수집·수정 시각
- 처리와 검수 상태
- soft delete 시각

외부 참조는 API 응답과 일반 로그에 노출하지 않습니다.

### DocumentVersion

같은 논리 문서가 교체되거나 재발급됐을 때 원본 버전을 보존합니다. 해시가 같으면 새 버전을 만들지 않습니다.

### Extraction

파서별 결과와 품질을 저장합니다.

- 추출기 이름·버전·설정 해시
- 페이지별 텍스트 블록과 좌표
- 표 셀과 읽기 순서
- 품질 지표와 경고 코드
- 생성 상태와 관리자 검수 상태

관리자 수정값은 원시 추출값을 덮어쓰지 않고 별도 확정 레코드로 보존합니다.

## Policy boundary

### PolicyContract

보험 계약의 논리 단위입니다.

- FamilyCare 내부 계약 UUID
- 보험사와 상품의 표시용 정규화 값
- 계약일, 보장 시작·종료일
- 계약 상태와 상태 근거
- 증권 DocumentVersion
- 마지막 검수 시각과 검수자

증권번호는 기능상 필요성이 확인될 때 암호화·마스킹 정책과 함께 추가합니다. 공개 fixture에는 넣지 않습니다.

### PolicyParty

PolicyContract와 FamilyMember 사이의 역할 연결입니다.

- policyholder
- primary_insured
- additional_insured
- beneficiary

역할은 기간을 가질 수 있으며 한 가족 구성원이 여러 역할을 가질 수 있습니다.

### Rider

증권에서 실제 가입이 확인된 주계약 또는 특약입니다.

- 원문 표시명과 정규화 키
- 가입금액과 통화
- 납입기간과 보장기간
- 갱신형 여부
- 현재 상태와 상태 확인 시각
- 증권 페이지 Evidence
- 추출·검수 상태

약관에만 나타난 특약은 Rider를 만들 수 없습니다.

### PolicyStatusSnapshot

사고일 기준 계약과 Rider 유효성을 평가하기 위한 시점별 상태입니다. 최신 상태 근거가 없으면 현재 활성으로 추정하지 않습니다.

## Terms and rules boundary

### TermsEdition

상품·특약의 특정 약관 판본입니다. 계약일과 판매 시기, DocumentVersion을 연결합니다.

### Clause

장·절·조·항·별표의 구조와 페이지 Evidence를 저장합니다. 같은 원문 조항이 여러 Rider에 연결될 수 있습니다.

### RiderClauseLink

실제 가입 Rider와 해당 약관 조항의 연결입니다. 자동 후보 점수, 관리자 확정 상태, 연결 근거를 분리합니다.

### CoverageRule

지급사유, 정의, 보장개시, 감액, 면책, 횟수 제한, 계산식을 명시적 구조로 표현합니다. 규칙 버전과 Clause Evidence가 필수입니다.

## Event and decision boundary

### MedicalEvent

한 가족 구성원의 사건 입력입니다.

- 사전 탐색 또는 사후 상세 모드
- 사건일과 방문일
- 질병·상해 분류
- 진단명과 분류코드
- 수술, 입원, 통원, 응급실 정보
- 원인과 비용 자료 가용성
- 입력 출처와 확인 수준

필드 부재는 null로 보존하고 임의 값으로 보완하지 않습니다.

### RuleEvaluation

하나의 CoverageRule을 평가한 결과입니다.

- `MATCH`, `NO_MATCH`, `UNKNOWN`
- 사용한 입력 사실
- 부족하거나 충돌한 정보
- 적용 규칙 버전
- Evidence 집합
- 평가기 버전과 시각

### ClaimCandidate

Rider별 평가 집계입니다. 지급 결정이나 ClaimCase가 아닙니다. 정액형 추정, 실손형 계산 보류, 추가 질문, 제외 근거를 분리합니다.

## Claim boundary

### ClaimCase

실제 청구 단위입니다. 상태는 preparing, submitted, supplementation_requested, reviewing, paid, partially_paid, denied, closed를 사용합니다.

### ClaimHistory

지급일, 지급 결과, 횟수 제한에 필요한 최소 이력을 보존합니다. 제출 문서 원본은 저장소 밖 참조로 관리합니다.

## Evidence

Evidence는 다음을 가집니다.

- DocumentVersion UUID
- PDF 기준 1부터 시작하는 페이지 번호
- 조항·별표 식별자
- 선택적 페이지 좌표
- 콘텐츠 해시
- 검수 상태

사용자 화면 페이지와 내부 0-based 인덱스를 혼용하지 않습니다.

## Invariants

1. Rider는 증권 Evidence 없이 verified 상태가 될 수 없습니다.
2. CoverageRule은 Clause Evidence와 규칙 버전 없이 활성화할 수 없습니다.
3. RuleEvaluation은 세 판정값 중 하나만 사용합니다.
4. `NO_MATCH`에는 결정적 불일치 사유가 필요합니다.
5. 갱신형 Rider는 최신 상태 근거가 없으면 current-active가 아닙니다.
6. AppUser 삭제가 FamilyMember·PolicyContract를 연쇄 삭제하지 않습니다.
7. soft-deleted 엔터티는 기본 조회에서 제외되고 명시적 휴지통 조회에서만 나타납니다.
8. 원시 추출과 관리자 확정 데이터는 서로 덮어쓰지 않습니다.

## Failure behavior

- 필수 근거가 누락되면 확정 전이 요청을 거부하고 안정적인 오류 코드를 반환합니다.
- 중복 해시는 기존 DocumentVersion을 반환하고 새 분석 작업을 만들지 않습니다.
- 충돌하는 계약 상태는 최신 값을 임의 선택하지 않고 conflict와 `UNKNOWN`을 만듭니다.
- 삭제·복원 충돌은 상태 전이 버전으로 감지합니다.

## Security considerations

- 내부 UUID와 외부 문서 참조를 분리합니다.
- API 응답은 증권번호와 외부 파일 경로를 기본 제외합니다.
- 민감 필드 추가는 데이터 분류, 암호화, 로그, 보존, 삭제 설계를 함께 변경해야 합니다.
- 감사 레코드는 실제 원문 대신 변경 필드와 행위자·시각을 저장합니다.

## Tests

- AppUser와 FamilyMember 독립 수명주기
- 약관 전용 특약의 Rider 생성 거부
- 갱신 상태 미확인의 `UNKNOWN`
- Evidence 없는 검수 확정 거부
- soft delete, 휴지통, 복원, 중복 복원
- 문서 해시 중복과 버전 교체
- 1-based 페이지 일관성

## Deferred decisions

물리 테이블, 인덱스, 암호화 키 관리, 보존 기간, 실제 증권번호 저장 필요성은 해당 구현 단계에서 별도 ADR로 확정합니다. 이 지연은 외부 입력 없이 구현 가능한 Foundation 범위에 영향을 주지 않습니다.

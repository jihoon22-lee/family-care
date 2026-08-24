# FamilyCare architecture

이 문서는 FamilyCare의 장기 시스템 구조와 변경 경계를 설명한다. Phase 0 Foundation과 Phase 1 Synthetic PDF Ingestion은 완료되었고 Phase 2 core Policy Ledger, Phase 4 Clause search, Phase 5 Rider-Clause/CoverageRule review boundary가 구현되었다. Phase 2 candidate review부터 Phase 8까지는 첫 사용 가능 버전인 `v0.1.0`을 구성하며, 상세 제품 기준은 `docs/design/v0.1-product.md`, 구현 순서는 `docs/plan/000-project-roadmap.md`를 따른다. 결정론적 CoverageRule 실행은 다음 Phase의 범위다.

## Architectural goals

- 실제 가입 담보와 약관 조건을 분리해 정확히 연결한다.
- 모든 판정을 입력 사실, 버전 있는 규칙, 원문 페이지까지 추적한다.
- 정보 부족을 실패로 숨기지 않고 `UNKNOWN`으로 표현한다.
- AI를 문서·입력 구조화에 사용하되 판정과 금액의 권위로 사용하지 않는다.
- 실제 문서와 개인정보를 공개 소스·CI 경계 밖에 둔다.
- 개인 WSL과 가족 내부 사용에 맞는 단순한 운영 구조를 유지한다.
- 하위 시스템을 독립적으로 테스트하고 교체할 수 있게 계약을 명확히 한다.

## System context

```text
동일 권한 관리자 두 명
        |
   Tailscale private access
        |
        v
FamilyCare Web gateway
        |
        v
FastAPI modular monolith ------------------------+
        |                                        |
        v                                        v
PostgreSQL <------ Analyzer Worker      encrypted managed archive
                         |
                         +---- local Korean/English OCR
                         +---- OpenAI structurer + verifier
```

원본 PDF는 Google Drive에 계속 보관하지만 v0.1은 Drive API를 사용하지 않는다. 사용자가 저장소 밖 import directory로 수동 다운로드한 문서를 FamilyCare가 처리하고, 성공한 문서는 application-level encrypted archive에 관리한다. Cloud Run과 공용 인터넷 운영 배포는 별도 미래 설계이며 v0.1의 선행 조건이 아니다.

## Trust boundaries

### Public source boundary

Git 저장소, GitHub Actions, 공개 컨테이너 build context에는 코드, 문서, 완전 합성 fixture만 들어간다. 실제 PDF, 추출 text, OCR image, AI response, 데이터베이스, archive, key와 비밀값은 금지한다.

### Browser boundary

브라우저는 화면에 필요한 최소 데이터만 받는다. 서비스 워커는 app shell만 cache하고 API response, PDF, 의료정보, 판정 결과는 cache하지 않는다. 인증 token은 Web Storage에 두지 않고 server-side session의 Secure/HttpOnly cookie를 사용한다.

### Gateway and API boundary

Compose에서 host에 노출되는 진입점은 Web gateway 하나다. gateway는 정적 PWA와 `/api` reverse proxy를 제공한다. API, Worker, PostgreSQL은 내부 network에 있고 host port를 직접 publish하지 않는다.

API는 입력 검증, 인증·인가, use case, 결정론적 판정·계산과 외부 계약을 소유한다. 로그에는 요청 본문, 검색어, 의료사건 text, 실제 경로를 남기지 않는다.

### Worker boundary

Worker는 문서 분석 작업을 격리된 임시 directory에서 수행한다. 작업은 idempotent하며 lease 만료 후 재처리할 수 있다. 복호화 평문과 OCR page image는 모든 종료 경로에서 삭제한다.

Phase 1 parser isolation의 descriptor-only input, 25 MiB/500 page, parent wall 120초, child CPU 90초, address space 1536 MiB, output 64 MiB, descriptor 64개 제한은 유지한다. v0.1의 암호·OCR·AI 단계는 이 parser 결과를 후속 입력으로 사용하며 원본 path를 다시 여는 우회 경로를 만들지 않는다.

### External AI boundary

OpenAI는 v0.1 document structuring 기능이다. Worker만 외부 호출을 수행하고 기존 WSL `OPENAI_API_KEY`를 runtime에 주입한다. PDF binary, image, password, archive key, local path, Drive ID는 보내지 않는다. 필요한 page text와 Evidence token만 bounded batch로 보내며 별도 verifier와 deterministic validator를 모두 통과한 후보만 실행할 수 있다.

Google Drive 자동 연동과 Gemini provider는 v0.1에 없다.

## Runtime components

### Web PWA

책임:

- 가족·문서 batch와 작업 상태
- 자연어·구조화 사건 입력과 선택 질문
- AI 후보 수정과 원장 검토
- 행동 우선 결과 카드와 Evidence 탐색
- 영수증 항목, 청구 checklist, 접수·지급 metadata
- device session 관리

비책임:

- 보험 규칙과 금액 계산
- 보험사 직접 청구
- 의료 문서 file 보관
- PDF·API response offline cache

### FastAPI modular monolith

모듈 경계:

- `identity`: AppUser, 공동 HouseholdSpace, session, CSRF
- `documents`: batch, 문서, 추출, Evidence, archive 참조
- `policies`: 계약, 당사자, 실제 가입 Rider, 상태 snapshot
- `clauses`: 약관 판본, 조항, Rider link, 전문검색, executable rule
- `decisions`: MedicalEvent, 영수증 항목, rule evaluation, 예상액
- `claims`: checklist, 접수·보완·지급 metadata와 감사 이력

모듈은 다른 모듈의 내부 table을 직접 변경하지 않고 public use case와 명시적 read model을 사용한다.

### Analyzer Worker

책임:

- file validation, password 적용, content hash
- native text·table·coordinate extraction
- `OCR_REQUIRED` page의 local Korean/English OCR
- policy, rider, clause, rule candidate structuring
- independent AI verification과 deterministic schema/Evidence validation
- encrypted archive write와 임시 file lifecycle
- job state, retry, lease, version publish

Worker는 보험 가입을 단독 확정하거나 `MATCH`, `NO_MATCH`, `UNKNOWN`, 보험금 예상액을 만들지 않는다.

### PostgreSQL

초기에는 다음 역할을 함께 담당한다.

- 정규화된 업무 데이터
- PostgreSQL full-text search
- 비동기 job queue와 lease
- session, audit, 상태 전이
- AI candidate와 published version metadata

Redis, 별도 search engine, vector DB는 측정된 필요 없이 추가하지 않는다.

### Managed encrypted archive

import 성공 문서는 document별 data key로 암호화하고, data key는 저장소 밖 master key로 wrap한다. DB는 encrypted object metadata와 wrapped key만 가진다. archive는 고정 크기를 사전 할당하지 않고 실제 사용량만큼 증가한다. Google Drive 원본을 수정·삭제하지 않는다.

### Contracts

FastAPI OpenAPI가 동기 HTTP 계약의 기준이다. Worker job, AI candidate, Rider-Clause/CoverageRule review payload, CoverageRule DSL은 versioned JSON Schema를 사용한다. TypeScript와 Python 소비자는 계약에서 생성하거나 검증하고 구조를 수동 복제하지 않는다. CoverageRule version 목록은 optimistic publication에 필요한 `expected_version`을 함께 반환한다.

### Rider-Clause and CoverageRule review boundary

`clauses` 모듈은 실제 가입이 검증된 Rider와 계약일에 적용되는 TermsEdition의 Clause만 연결한다. 연결 확인·제외는 서버가 계산한 `HouseholdScope` 안에서 실행되며, 모든 전이는 예상 버전을 요구한다. 약관에만 존재하는 Rider, 계약일과 맞지 않는 판본, 다른 문서 버전의 Clause, 누락·불일치 Evidence는 자동으로 다른 후보로 대체하지 않고 검토 상태로 남긴다.

CoverageRule 후보는 버전이 지정된 data-only DSL allowlist로 구조·필드·연산자·단위와 Evidence 참조를 검증한다. 검증기는 규칙을 저장할 수 있는 형태로 정리할 뿐 MedicalEvent를 평가하거나 `MATCH`·`NO_MATCH`·`UNKNOWN`을 계산하지 않는다. 정확한 Clause/Policy Evidence를 가진 저장 후보 중 `AI_VERIFIED` 또는 `USER_CONFIRMED`인 버전만 immutable executable version으로 게시할 수 있다. `NEEDS_REVIEW`, 지원하지 않는 DSL, 상충·손실 Evidence는 정보성 후보이며 결정 엔진이 소비하지 않는다.

사용자 검토 화면 `/app/clauses/review`는 Rider-Clause 연결과 CoverageRule 예외를 별도 대기열로 제공한다. 화면은 bounded Evidence drawer와 생성된 typed field/operator/unit control만 사용하며 raw DSL textarea나 원문 문서·private path·provider payload를 노출하지 않는다. 후보 수정은 원 버전을 덮어쓰지 않고 typed child version을 만든다. Rule version 목록의 `expected_version`과 저장된 `version_id`를 함께 제출해야 게시되므로 충돌 시 최신 근거를 다시 확인해야 한다.

## Core data flows

### Document to executable rule

```text
family-scoped PDF batch
  -> password in process memory
  -> descriptor-safe intake and native extraction
  -> OCR_REQUIRED pages only -> local ko/en OCR
  -> OpenAI structurer
  -> independent OpenAI verifier
  -> deterministic schema/unit/Evidence validation
  -> AI_VERIFIED | NEEDS_REVIEW
  -> Policy Ledger, Clause, CoverageRule versions
  -> encrypted managed archive
```

AI-verified candidate는 즉시 사용할 수 있고 예외만 사용자에게 노출한다. 사용자 수정은 새 version이며 원시 extraction과 AI result를 덮어쓰지 않는다.

### Coverage discovery

```text
natural-language situation
  -> AI structured, user-editable MedicalEvent facts
  -> incident-date actual/valid Rider filter
  -> published CoverageRule evaluation
  -> MATCH | NO_MATCH | UNKNOWN per rule
  -> fixed-benefit or indemnity calculation
  -> action-first ClaimCandidate with Evidence and questions
```

AI는 input fact와 rule candidate를 구조화할 뿐 tri-state와 금액 계산을 직접 반환하지 않는다.

Phase 5의 link/rule boundary는 위 흐름에서 `published CoverageRule evaluation` 직전까지를 담당한다. 즉, 가입 담보·Clause 연결과 실행 가능한 immutable rule version을 준비하지만, 실제 MedicalEvent evaluation과 tri-state 집계는 별도 구현 단계에서 수행한다.

### Indemnity calculation

```text
manual receipt lines
  -> visit/admission/pharmacy and covered/non-covered categories
  -> verified deductible/rate/limit rules
  -> confirmed amount + needs-check amount + excluded amount
  -> conditional estimate and Evidence
```

복수 실손 계약의 독립 예상액은 더하지 않는다. 공통 청구 대상과 계약별 조건을 보여주고 최종 비례분담은 `UNKNOWN`으로 둔다.

### Claim tracking

ClaimCandidate와 ClaimCase는 별도 entity다. ClaimCase는 당시 후보·규칙·예상액·Evidence snapshot, 필요서류 checklist, 사용자가 수동 기록한 접수번호·상태·실제 지급액을 보존한다. v0.1은 의료 document file을 보관하거나 보험사에 직접 제출하지 않는다.

## Data ownership and lifecycle

- `AppUser`는 local login 주체다.
- `FamilyMember`는 보험 대상이며 AppUser와 독립적이다.
- `PolicyParty`는 특정 계약의 계약자·피보험자·수익자 역할이다.
- `DocumentVersion`은 content identity를 대표한다.
- `Extraction`은 native/OCR layer와 parser version을 보존한다.
- `PolicyContract`와 `Rider`는 증권 Evidence가 있어야 verified 상태가 된다.
- `TermsEdition`, `Clause`, `CoverageRule`은 판본과 Evidence로 versioning한다.
- `RuleEvaluation`과 calculation은 input/rule/engine version을 보존한다.
- `ClaimCase`는 생성 당시 result snapshot을 보존한다.
- 사용자 수정은 version/audit record이며 raw result를 overwrite하지 않는다.
- 삭제는 soft delete와 trash를 기본으로 한다.

## Decision semantics

`MATCH`, `NO_MATCH`, `UNKNOWN`은 개별 rule과 Rider 집계에 적용한다. 결정적 불일치가 있을 때만 `NO_MATCH`다. 필수 정보·계약 상태·갱신·Evidence·규칙이 부족하거나 충돌하면 `UNKNOWN`이다. probability score 하나로 이 정보를 대체하지 않는다.

AI explanation은 구조화 결과와 reason code를 사용자 언어로 풀어 쓸 수 있지만 판단·계산·Evidence를 변경할 수 없다.

## Failure and recovery

- batch의 file별 상태와 transaction을 분리한다.
- 일시적 network/provider failure만 bounded retry한다.
- password failure는 자동 반복하지 않고 해당 file만 다시 요청한다.
- 새 analysis는 new version이며 full validation 뒤 atomic publish한다.
- 한 Rider·rule·receipt line 실패가 다른 확인 결과를 제거하지 않는다.
- calculation error를 0 또는 `NO_MATCH`로 변환하지 않는다.
- job lease와 content/config idempotency로 restart 후 작업을 회수한다.
- temp cleanup과 archive encryption failure를 성공으로 숨기지 않는다.

## Deployment and release boundaries

- v0.1 runtime은 개인 WSL Docker Compose와 Tailscale private access다.
- LUKS, BitLocker, WSL swap, 고정 크기 encrypted volume 변경은 수행하지 않는다.
- `vMAJOR.MINOR.PATCH` tag는 Web/API/Worker image를 GHCR에 publish한다.
- `v0.1.0` tag는 Phase 2~8 기능·local acceptance 완료 뒤 만든다.
- GHCR publish는 running service deployment를 의미하지 않는다.
- Cloud Run과 public production deployment는 별도 승인 전까지 구성하지 않는다.

## Verification boundaries

PR/main CI는 합성 PDF와 합성 AI response만 사용하고 외부 secret, OpenAI, Google Drive를 호출하지 않는다. 로컬 acceptance는 사용자가 지정한 저장소 밖 source만 사용한다. Rider-Clause/CoverageRule review의 합성 Web 시나리오는 320px viewport에서 Evidence disclosure, stored-version publication, no-store/browser-storage 경계를 확인한다. Windows browser, 실제 mobile PWA, 실제 보험 format, Tailscale device 확인은 실행 증거와 미검증 범위를 각각 보고한다.

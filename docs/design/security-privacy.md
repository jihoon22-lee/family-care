# Security and privacy design

- 상태: Foundation 및 Phase 1 보안 기준
- 대상: 공개 저장소, 합성 전용 로컬 개발, 향후 운영

## Scope

보험·의료 자료의 기밀성, 판정 무결성, 서비스 가용성, 공개 저장소의 공급망 위험을 다룹니다. Foundation은 완료되었고 Phase 1은 실제 데이터·private external root·운영 인증 없이 합성 fixture만 사용하지만, 이후 단계가 넘어서는 안 될 경계를 먼저 강제합니다.

## Inputs

- 저장소 파일과 Git 이력
- 브라우저·API·Worker의 데이터 흐름
- 외부 문서 참조와 임시 작업공간
- 인증·세션·workflow 권한
- 의존성, 컨테이너, 외부 제공자 경계

## Outputs

- 데이터 분류와 허용 저장 위치
- 계층별 보안 불변조건
- 로그 allowlist와 금지 필드
- 예방·탐지·사고 대응 통제
- 기능 단계별 보안 수용 조건

## Assets

### Restricted

- 실제 보험·의료 원본
- 원본에서 추출한 텍스트·표·이미지·OCR·임베딩
- 의료 사건 입력과 청구 이력
- 인증정보, 세션, 서비스 계정, PDF 암호
- 외부 파일 식별자와 실제 로컬 경로

### Confidential

- 구조화된 계약, Rider, 약관 연결, 판정 결과
- 내부 사용자·가족 UUID 연결
- 운영 구성과 감사 이벤트

### Public

- 소스 코드와 설계 문서
- 처음부터 만든 합성 fixture
- 비밀값이 없는 환경변수 이름
- 공개 CI와 컨테이너 빌드 정의

Restricted와 Confidential 자료를 Public 경계로 낮추려면 새로 합성해 작성해야 하며 단순 마스킹으로 분류를 바꾸지 않습니다.

## Threat model

### Accidental Git disclosure

개발자가 PDF, DB, 로그, 키, 추출 결과를 add할 수 있습니다.

대응:

- `.gitignore`
- 확장자·경로·크기 safety scanner
- gitleaks
- PR 체크리스트
- CI에서 tracked/untracked 공개 파일 검사
- 민감 값을 검사 allowlist에 넣지 않는 규칙

### Malicious document

PDF가 파서 취약점, 과도한 메모리·CPU 사용, embedded action, 경로 문제를 유발할 수 있습니다.

대응:

- 비특권 격리 Worker
- 읽기 전용 원본과 별도 임시 root
- 크기·페이지·시간·메모리 제한
- parser dedicated child process와 64 open descriptor limit
- 25 MiB input, 500 page, 120초 parent wall, 90초 child CPU, 1536 MiB address-space, 64 MiB output-file 제한
- child에는 부모가 no-follow 방식으로 연 read-only source descriptor와 canonical JSON settings만 전달하고 external URL resolution을 제공하지 않음
- fork 후 parser 호출 전 source와 supervision pipe 외의 inherited application file·socket descriptor를 닫음
- parser child의 stdout·stderr를 폐기해 library 출력이 Worker log로 유입되지 않게 함
- child 결과 IPC는 64 MiB 이하 canonical JSON만 허용하고 parent에서 `pickle` 같은 임의 객체 역직렬화를 사용하지 않음
- 외부 실행·네트워크 client 비활성화
- 파서 버전 갱신과 합성 회귀 corpus

OS-level egress enforcement는 production hardening 항목입니다. approved runtime boundary가 마련되기 전에는 private-data acceptance를 실행하지 않습니다.

### Browser persistence

PWA 캐시, localStorage, IndexedDB, 브라우저 기록에 보험·의료 데이터가 남을 수 있습니다.

대응:

- 서비스 워커 앱 셸 전용 cache
- API·PDF `Cache-Control: no-store`
- 장기 토큰의 Web Storage 금지
- 로그아웃·세션 만료 시 메모리 상태 정리
- 브라우저 저장소 자동 검사

### Broken access control

허용되지 않은 계정 또는 한 가족 내부의 잘못된 객체 참조가 데이터에 접근할 수 있습니다.

대응:

- 두 관리자 allowlist
- 서버 측 HouseholdSpace scope
- 클라이언트가 보낸 household·user ID를 권위로 사용하지 않음
- UUID만으로 권한을 대체하지 않음
- 객체별 인가 테스트

### Decision manipulation

검색 결과, AI 설명, 오래된 계약 상태가 지급 가능성을 과장할 수 있습니다.

대응:

- 증권 기반 실제 가입 확인
- 버전 있는 규칙과 Evidence
- tri-state와 stale 결과
- AI 비권위 원칙
- 검수 상태와 변경 감사

### Supply-chain compromise

npm, Python, Docker, GitHub Action 의존성이 빌드와 공개 이미지를 오염시킬 수 있습니다.

대응:

- lockfile
- 지원 런타임 버전
- GitHub Action 전체 SHA 고정
- Dependabot
- 최소 workflow 권한
- 빌드 컨텍스트 최소화와 비특권 런타임

## Data minimization

- 주민번호 전체·일부를 기능 데이터로 수집하지 않습니다.
- 증권번호는 필요성이 확인되기 전 저장하지 않습니다.
- 사용자 화면에는 최소 식별 정보만 표시합니다.
- 원문은 외부 저장소에 두고 DB에는 필요한 구조와 Evidence만 저장합니다.
- Phase 1 source key는 `FAMILYCARE_DOCUMENT_ROOT`에 상대적인 값이며 absolute path를 request, job payload, log에 넣지 않습니다.
- password는 DB, job payload, log에 저장하지 않습니다.
- 로그와 telemetry는 allowlist 필드 방식으로 구성합니다.

## Security considerations

- 데이터 수집 필요성을 암호화 가능성과 별도로 검토합니다.
- 모든 객체 접근은 인증 주체의 HouseholdSpace에서 서버가 scope를 계산합니다.
- 신뢰하지 않는 PDF는 API 프로세스와 분리하고 resource limit을 적용합니다.
- 브라우저 cache와 로그를 데이터 저장소로 간주해 같은 최소화 원칙을 적용합니다.
- GitHub workflow는 외부 PR 코드와 write 권한·secret이 만나는 경로를 만들지 않습니다.
- Drive·AI 연동은 최소 권한과 전송 필드 allowlist가 승인된 뒤에만 추가합니다.

## Secrets

- `.env`는 로컬 전용이며 commit하지 않습니다.
- 공개 CI는 외부 비밀값 없이 실행됩니다.
- GHCR 릴리스는 단기 `GITHUB_TOKEN`만 사용합니다.
- 운영은 장기 서비스 계정 키 파일보다 workload identity를 우선합니다.
- 비밀값 회전과 폐기는 Git 이력 정리와 별도 작업입니다.

## Logging allowlist

허용:

- request ID
- job ID
- 모듈
- 안정적인 오류 코드
- 처리 시간
- retry count
- 서비스·빌드 버전

금지:

- 요청 본문과 검색 자연어
- 진단명과 치료 내용
- 문서 본문·파일명·절대경로
- 실제 이름·이메일·증권번호
- 인증 header·cookie·token
- SQL parameter 값

## Encryption

전송 구간 TLS는 운영 필수입니다. 저장 암호화는 운영 PostgreSQL과 외부 원본 저장소에 적용합니다. 필드 수준 암호화는 증권번호 등 저장 필요성이 확정된 민감 필드에 대해 키 수명주기와 함께 설계합니다. 암호화가 과도한 수집을 정당화하지 않습니다.

## Retention and deletion

- 애플리케이션 삭제는 soft delete와 휴지통을 사용합니다.
- 물리 삭제 기간은 운영 백업·법적 요구와 함께 결정합니다.
- 임시 평문·페이지 이미지는 작업 종료 즉시 삭제 대상입니다.
- 외부 AI 제공자 보존 정책은 연동 승인 조건입니다.
- 감사 기록은 원문 대신 행위와 변경 메타데이터만 보존합니다.

## Incident response

1. 관련 push·배포·외부 전송을 중단합니다.
2. 실제 값을 재출력하지 않고 데이터 종류와 노출 범위를 확인합니다.
3. 비밀값을 폐기·회전합니다.
4. 사용자 승인으로 Git 이력·공개 asset 정리 범위를 결정합니다.
5. 회귀 안전 검사를 추가합니다.
6. 영향과 직접 확인하지 못한 범위를 구분해 기록합니다.

공유 이력 force push, 데이터 물리 삭제, 프로세스 중지는 별도 승인을 받습니다.

## Invariants

1. 실제 자료는 Git과 기본 CI에 없습니다.
2. 보험 결과는 가입 Rider와 Evidence 없이 확정되지 않습니다.
3. 브라우저 persistent cache에 보험·의료 데이터가 없습니다.
4. Worker 임시 산출물은 성공·실패·취소 후 남지 않습니다.
5. 외부 제공자 전송은 명시적 기능 설계와 최소 필드 정책이 있어야 합니다.
6. 각 계층은 내부 UUID만으로 접근 권한을 결정하지 않습니다.
7. Phase 1 implementation과 CI는 실제 PDF나 private external root를 열지 않습니다.
8. Phase 1 endpoint는 인증 provider가 없는 local synthetic-only 개발 경계이며 production-safe로 취급하지 않습니다.

## Failure behavior

- 민감정보 가능성이 발견되면 commit, push, 외부 전송을 중단합니다.
- 권한 검사가 실패하면 객체 존재 여부를 추가로 노출하지 않는 거부 응답을 반환합니다.
- 임시 파일 삭제, secret scan, 필수 보안 검사가 실패하면 작업 전체를 성공으로 표시하지 않습니다.
- 외부 제공자가 실패하면 기존 근거 조회와 규칙 판정은 계속 가능해야 합니다.
- 조사하지 못한 영향 범위는 안전하다고 추정하지 않고 미확인으로 기록합니다.

## Tests

- 금지 파일·크기·경로 scanner의 positive/negative cases
- 합성 비밀값에 대한 gitleaks
- PWA cache manifest와 no-store header
- 경로 정규화와 symlink 탈출
- PDF resource limit와 임시 파일 삭제
- PDF path symlink traversal, magic bytes, 1 MiB streaming hash, and quality-v1 thresholds
- encrypted PDF `PASSWORD_REQUIRED`와 direct one-shot `PASSWORD_INVALID`
- 객체 scope 인가
- 로그 capture에서 금지 필드 부재
- Evidence 없는 판정 거부
- workflow 최소 권한과 immutable action pin

## Deferred decisions

운영 리전, 데이터베이스 서비스, field encryption, 백업 보존, 인증 제공자, Drive·AI 데이터 처리 계약은 관련 단계에서 승인합니다. Foundation에 실제 secret placeholder나 운영 리소스를 미리 만들지 않습니다.

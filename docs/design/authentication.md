# Local authentication design

- 상태: v0.1 대화 설계 승인 완료, 문서 검토 대기
- 적용 단계: Phase 7
- 배포 전제: 개인 WSL Docker Compose와 Tailscale private access

## Scope

v0.1은 하나의 공동 HouseholdSpace에 동일 권한을 가진 로컬 관리자 계정 두 개를 제공한다. 외부 인증 제공자, 공개 가입, 이메일 reset, 초대와 역할 관리는 구현하지 않는다.

## Account provisioning

- 관리자 계정은 서버 측 관리 명령으로만 생성·비활성화한다.
- fresh migration 뒤에는 one-time `familycare-admin init`이 sole HouseholdSpace와 첫 관리자를 한 transaction에서 생성한다. PostgreSQL transaction advisory lock으로 concurrent init을 직렬화하고 기존 또는 soft-deleted HouseholdSpace가 하나라도 있으면 `HOUSEHOLD_ALREADY_INITIALIZED`로 거부한다.
- `familycare-admin create`는 초기화가 끝난 sole active HouseholdSpace의 선택적 두 번째 관리자만 생성한다. migration seed나 일반 Web bootstrap으로 HouseholdSpace를 만들지 않는다.
- 명령은 username과 password를 TTY 또는 stdin으로 받고 raw password를 argument, 환경변수, shell history, log에 넣지 않는다.
- password는 Argon2id hash로 PostgreSQL에 저장한다.
- 활성 관리자 계정은 최대 두 개이며 모두 같은 HouseholdSpace에 연결된다.
- 초기 계정이 없을 때 일반 Web 요청으로 bootstrap하지 않는다.
- recovery credential은 사용자가 관리하는 외부 password vault에 보관할 수 있지만 앱과 저장소가 그 vault를 자동 동기화하지 않는다.

## Login and session

- username과 password가 맞으면 opaque session ID를 생성한다.
- session 원본 token은 `Secure`, `HttpOnly`, `SameSite=Strict`, host-only cookie로만 전달한다.
- DB에는 session token hash, user, created/last-seen/expires/revoked 시각, 최소 device label을 저장한다.
- 비활성 7일 또는 생성 후 30일 중 먼저 도달한 시점에 만료한다.
- 로그인 성공 후 session ID를 회전하고 logout·password 변경·계정 비활성화 시 관련 session을 폐기한다.
- 사용자는 자신의 다른 device session을 확인하고 폐기할 수 있다.

## CSRF and sensitive actions

- 상태 변경 API는 same-origin과 CSRF token을 함께 검사한다.
- login과 password 검증 endpoint에는 속도 제한과 일정한 오류 응답을 적용한다.
- password 변경, 다른 session 폐기, 관리자 계정 변경, archive key 관련 작업은 최근 재인증을 요구한다.
- username 존재 여부와 계정 비활성 여부를 로그인 오류로 구분 노출하지 않는다.

## Authorization

- 인증 middleware가 session에서 AppUser와 HouseholdSpace를 결정한다.
- Phase 2 이후 모든 업무 use case는 이 server-derived scope를 필수 입력으로 받는다.
- request body, query, route의 household/user ID만으로 scope를 변경하지 않는다.
- 두 관리자는 동일 권한이며 다른 FamilyMember와 계약을 모두 관리할 수 있다.

## Browser boundary

- bearer token을 localStorage, sessionStorage, IndexedDB에 저장하지 않는다.
- 모든 인증·업무 API 응답은 `Cache-Control: no-store`다.
- logout과 session 만료 시 in-memory query cache와 화면 상태를 지운다.
- PWA service worker는 로그인 HTML, API 응답, 문서, Evidence를 cache하지 않는다.

## Failure behavior

- DB unavailable이면 fail closed하고 offline login을 허용하지 않는다.
- 만료·폐기 session은 동일한 unauthenticated 응답을 반환한다.
- Argon2 parameter upgrade는 성공 login 뒤 hash를 새로 만들되 실패 시 기존 hash를 보존한다.
- 두 계정 한도를 넘는 provisioning은 안정적인 관리 오류로 거부한다.
- first-run household와 첫 관리자 중 하나라도 저장에 실패하면 같은 transaction 전체를 rollback한다.
- 계정 비활성화는 FamilyMember, 계약, 청구 기록을 삭제하지 않는다.

## Tests

- Argon2id hash와 raw password 비저장
- 최대 두 관리자와 동일 HouseholdSpace
- 성공·실패 login, session fixation 방지, logout
- 7일 inactivity와 30일 absolute expiry 경계
- CSRF와 same-origin 성공·거부 쌍
- client-supplied HouseholdSpace scope 무시
- 다른 session 목록·폐기와 재인증
- local/session storage와 service-worker cache 비저장
- password·cookie·token이 log와 오류 응답에 없는지 확인

## Invariants

1. raw password와 session token은 DB, log, Git에 없다.
2. 인증 성공만으로 client가 HouseholdSpace scope를 선택할 수 없다.
3. 두 관리자 계정의 수명주기는 FamilyMember와 독립적이다.
4. 공개 signup, email reset, 초대 endpoint가 없다.
5. Tailscale 접속 여부는 app login을 대체하지 않는다.

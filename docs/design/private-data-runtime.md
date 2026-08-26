# Private data and local runtime design

- 상태: encrypted import 계약 구현·문서화, private runtime acceptance 대기
- 적용 단계: 암호 PDF batch, selective OCR, Phase 8 private-data acceptance
- 실행 위치: 개인 PC의 WSL Docker Compose

## Scope

이 문서는 실제 보험 PDF를 수동으로 가져와 FamilyCare가 로컬에서 처리하고, Tailscale을 통해 허용 기기에서 사용하는 v0.1 runtime 경계를 정의한다. 호스트 전체 보안을 재설계하지 않고 제품 기능에 필요한 최소 통제만 적용한다.

## Target runtime topology

```text
Tailscale private device
  -> single Web gateway
       -> Web static app
       -> /api reverse proxy
            -> API on internal Compose network
                 -> PostgreSQL
                 -> Analyzer Worker
                 -> managed encrypted document archive
```

- host에 노출하는 서비스는 Web gateway 하나다.
- API, Worker, PostgreSQL은 Compose 내부 네트워크에서만 연결한다.
- Tailscale private access와 app login을 함께 사용한다.
- TLS와 Secure cookie가 필요한 device access는 기존 tailnet의 HTTPS proxy 방식에 맞춘다. 시스템 명령 적용 전 현재 Tailscale 구성을 읽기 전용으로 확인한다.
- 다른 프로젝트의 container, port, Tailscale route를 중지하거나 변경하지 않는다.

이 topology와 Compose mount는 목표 설계이며, 현재 Compose/private-data acceptance에서 아직 확인하지 않았다. 실제 mobile·Windows·Tailscale device, provider, OCR acceptance도 완료로 간주하지 않는다.

## Runtime path and socket contract

환경변수에는 실제 경로를 넣지만 저장소 문서에는 변수 이름과 외부 경로 형식만 기록한다.

| 계약 | 소유자·접근 | 경계 |
|---|---|---|
| `FAMILYCARE_DOCUMENT_ROOT` | Phase 1 Worker/test 전용 | 합성 PDF만 사용하는 Phase 1 synthetic root다. private batch 입력이나 실제 자료용으로 재사용하지 않는다. |
| `FAMILYCARE_IMPORT_ROOT` | 저장소 밖 absolute directory를 API와 Worker가 함께 read-only로 bind mount | API는 source catalog와 opaque ID 해석에, Worker는 descriptor-safe intake에 사용한다. API·Worker 모두 원본을 변경·삭제하지 않는다. |
| `FAMILYCARE_ARCHIVE_ROOT` | 저장소 밖 absolute directory, Worker 전용 | document별 application-encrypted archive를 저장한다. API와 Web에는 mount하지 않는다. |
| `FAMILYCARE_WORK_ROOT` | 저장소 밖 absolute directory, Worker 전용 | 복호화 평문과 중간 산출물만 작업별 mode `0700` directory 및 mode `0600` file로 둔다. API와 Web에는 mount하지 않는다. |
| `FAMILYCARE_ARCHIVE_MASTER_KEY_FILE` | Worker 전용 read-only file | 저장소 밖의 absolute regular file이며 정확히 32 bytes, mode `0600`이어야 한다. key 값은 환경변수·DB·log·image에 넣지 않는다. |
| `FAMILYCARE_SECRET_SOCKET` | Worker server, API client | API가 one-time handoff frame을 보내고 Worker가 수신·검증한다. archive/work/key mount와 분리된 Unix-domain socket만 공유한다. |

## Source and archive lifecycle

1. 원본 PDF는 Google Drive에 계속 보관한다.
2. 사용자가 필요한 파일을 저장소 밖 `FAMILYCARE_IMPORT_ROOT`로 수동 다운로드한다.
3. FamilyCare는 가족 구성원 하나를 지정한 batch로 파일을 가져온다.
4. 암호가 필요하면 batch scope의 in-memory password를 사용한다.
5. extraction에 성공한 source는 app-managed archive에 저장할 때 document별 data key로 암호화한다.
6. data key는 runtime master key로 wrap하고 DB에는 wrapped key와 암호화 metadata만 저장한다.
7. 복호화 평문과 OCR page image는 작업별 임시 directory에만 존재하고 모든 종료 경로에서 삭제한다.
8. import source를 자동 삭제하거나 Google Drive 원본을 변경하지 않는다.

Archive는 고정 크기 volume을 미리 할당하지 않고 실제 문서만큼 증가한다. 원본·DB·archive backup과 복구는 v0.1 운영 가이드에서 명령 단위로 분리한다.

## Password handling

- batch는 정확히 한 FamilyMember를 가진다.
- 사용자 입력 password는 process memory에만 있고 API response, DB, job payload, log에 없다.
- 동일 batch의 암호 문서에 우선 재사용하되 실패 파일만 새 password를 요청한다.
- password 값으로 문서 소유자나 가족 관계를 추론하지 않는다.
- Worker 반복은 scope expiry를 정리하고 재입력은 이전 scope를 교체·폐기한다. 실행 중인 batch cancellation은 해당 batch만 폐기하고 Worker shutdown은 전체 registry를 폐기한다.
- Worker가 아직 잡지 않은 대기 batch를 API에서 취소하면 별도 control frame을 보내지 않으므로 최대 5분 expiry에서 정리된다. batch 성공·실패 직후의 즉시 scope 폐기나 프로세스 종료 뒤 terminal memory disposal은 아직 확인하거나 주장하지 않는다.
- 재분석은 관리 archive를 사용하므로 정상 import 후 원본 PDF password를 매번 요구하지 않는다.

## Archive key

- `FAMILYCARE_ARCHIVE_MASTER_KEY_FILE`은 저장소 밖의 absolute regular mode `0600` file이며 내용은 정확히 32 bytes다.
- Compose는 key file을 Worker에만 read-only secret mount한다. API와 Web에는 key file 또는 key 값이 없다.
- key 자체는 환경변수 값, Compose YAML, image layer, DB, log에 넣지 않는다.
- 사용자가 Google Drive에 두는 encrypted KDBX vault는 recovery copy일 뿐 앱이 직접 읽지 않는다.
- master key가 없으면 archive를 새로 생성하거나 읽지 않고 fail closed한다.
- key rotation은 old/new key를 동시에 검증하고 wrapped data key만 교체한 뒤 완료하는 별도 관리 작업이다.

## Selective local OCR (acceptance pending)

다음은 목표 계약이며 현재 OCR runtime과 실제 자료 acceptance는 아직 검증하지 않았다.

- native extraction이 `OCR_REQUIRED`로 분류한 page만 OCR한다.
- OCR은 로컬 Worker에서 한국어와 영어 language pack으로 실행한다.
- page 전체 PDF나 image를 외부 OCR provider로 보내지 않는다.
- OCR text, coordinates, engine/language/version과 quality warning을 별도 extraction layer로 저장한다.
- native text를 덮어쓰지 않고 검토 화면에서 source layer를 구분한다.
- page image는 성공·실패·취소 후 삭제한다.

## OpenAI boundary (provider acceptance pending)

OpenAI document structuring은 목표 v0.1 기능이지만 provider smoke와 실제 private acceptance는 아직 수행하지 않았다. PDF binary와 page image는 전송하지 않고 Worker가 추출한 필요한 text batch만 전송한다. 세부 최소화와 검증은 `docs/design/ai-document-analysis.md`를 따른다.

## Pragmatic security baseline (target; acceptance pending)

v0.1 target:

- Tailscale private access
- local app login과 server-side session
- 공개 저장소 유출 방지
- browser cache와 민감 log 금지
- 임시 평문과 OCR image cleanup
- managed archive의 application-level encryption
- Compose 내부 서비스 격리와 최소 host port

v0.1에 포함하지 않음:

- BitLocker 활성화
- LUKS volume 또는 encrypted swap
- WSL 기본 swap disable·변경
- 별도 32/40/128 GB 고정 volume 사전 할당
- host compromise와 unlocked-PC forensic 방어

위 제외 항목은 기능 구현의 선행 조건이 아니며 사용자 요청 없이 다시 제안하거나 변경하지 않는다.

## Private-data acceptance (pending)

현재 실제 자료 acceptance는 수행하지 않았다. Compose/private mount, mobile, Windows, Tailscale, provider, OCR acceptance가 모두 별도 검증 대기 상태다. 실제 자료 검증은 synthetic private-runtime acceptance가 끝난 뒤 사용자가 지정한 저장소 밖 path와 문서에만 수행한다.

1. 정확한 source와 output root를 읽기 전용으로 확인한다.
2. `git status`와 safety scanner 기준선을 기록한다.
3. 적은 수의 문서로 import, archive, extraction, OCR, AI structuring, ledger, Evidence를 확인한다.
4. 오류를 실제 text 없이 document-shape와 안정적 error code로 기록한다.
5. 필요한 회귀는 실제 문구를 복사하지 않고 새 합성 fixture로 작성한다.
6. 임시 directory, browser storage, log, Git untracked path를 재검사한다.
7. 확인하지 않은 Compose/private runtime, 보험사·문서 형식·mobile·Windows·Tailscale·provider·OCR은 미검증으로 남긴다.

## Failure behavior

- 한 파일의 password/OCR/AI 실패가 batch의 다른 파일을 rollback하지 않는다.
- archive encryption 또는 key wrapping 실패 문서는 imported-ready 상태로 전이하지 않는다.
- 임시 파일 cleanup 실패는 성공으로 숨기지 않는다.
- key file 부재·권한 오류·잘못된 key는 일반 문서 오류와 분리한다.
- Docker restart 후 running job은 lease로 회수하고 같은 content/config의 성공 결과를 재사용한다.
- import source와 원본 Google Drive 파일은 어떤 성공·실패·취소 경로에서도 수정·삭제하지 않는다.

## Tests

- 가족별 batch scope와 cross-member 혼합 거부
- 한 번 입력 password 재사용, 부분 실패 재입력, DB/job/log 비저장
- encrypted archive round-trip, tamper, wrong/missing key, key-wrap rotation
- 성공·실패·취소·shutdown 임시 평문 cleanup
- `OCR_REQUIRED` page만 한국어·영어 local OCR 실행
- OCR layer와 native extraction의 provenance 분리
- Compose에서 gateway만 host port를 가지는지 검사
- API/Worker/PostgreSQL internal network 접근
- restart 후 DB·archive 읽기와 running job recovery
- service-worker/browser storage와 log leakage 검사
- 실제 자료 acceptance 전후 Git safety scan

## Invariants

1. 실제 source, archive, DB, key는 저장소 밖에 있다.
2. PDF password와 archive master key는 DB, job, log, Git, image에 없다.
3. import source와 Google Drive 원본은 v0.1이 수정하거나 삭제하지 않는다.
4. imported document는 archive encryption 성공 전 ready가 아니다.
5. OCR page image는 작업 종료 후 남지 않는다.
6. Tailscale과 app login 중 하나를 다른 하나의 대체물로 취급하지 않는다.
7. 호스트 암호화와 swap 변경은 v0.1 완료 조건이 아니다.

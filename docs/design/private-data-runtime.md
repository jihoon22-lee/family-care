# Private data and local runtime design

- 상태: encrypted import·selective OCR·private runtime, offline backup-set packaging과 read-only
  archive audit 구현; 보호된 backup/restore·catalog/result acceptance 완료, 전체 disaster-recovery
  drill·남은 문서 형식·Windows/mobile 검증 대기
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

이 topology와 Compose mount는 합성 설정·image·permission smoke에서 확인했다. 현재 변경의 PR·CI·merge와 실제 private data, mobile·Windows·Tailscale device, provider, OCR acceptance는 아직 완료로 간주하지 않는다.

## Runtime path and socket contract

host bind 환경변수에는 실제 경로를 넣지만 저장소 문서에는 변수 이름과 외부 경로 형식만 기록한다. archive/work/socket 환경변수는 Compose named volume의 고정 container path다.

| 계약 | 소유자·접근 | 경계 |
|---|---|---|
| `FAMILYCARE_DOCUMENT_ROOT` | Phase 1 Worker/test 전용 | 합성 PDF만 사용하는 Phase 1 synthetic root다. private batch 입력이나 실제 자료용으로 재사용하지 않는다. |
| `FAMILYCARE_IMPORT_ROOT` | 저장소 밖 absolute directory를 API와 Worker가 함께 read-only로 bind mount | API는 source catalog와 opaque ID 해석에, Worker는 descriptor-safe intake에 사용한다. API·Worker 모두 원본을 변경·삭제하지 않는다. |
| `FAMILYCARE_ARCHIVE_ROOT` | Worker-only named volume의 container absolute path | document별 application-encrypted archive를 저장한다. API와 Web에는 mount하지 않는다. |
| `FAMILYCARE_WORK_ROOT` | Worker-only named volume의 container absolute path | 복호화 평문과 중간 산출물만 작업별 mode `0700` directory 및 mode `0600` file로 둔다. API와 Web에는 mount하지 않는다. |
| `FAMILYCARE_ARCHIVE_MASTER_KEY_FILE` | Worker 전용 read-only bind file | 저장소 밖의 absolute regular file이며 정확히 32 bytes, mode `0600`, numeric owner UID `10002`여야 한다. key 값은 환경변수·DB·log·image에 넣지 않는다. |
| `FAMILYCARE_SECRET_SOCKET` | 고정 GID `10003`의 named volume, Worker server와 API client | API가 one-time handoff frame을 보내고 Worker가 수신·검증한다. archive/work/key mount와 분리된 Unix-domain socket만 공유한다. |

API UID `10001`과 Worker UID `10002`는 supplementary GID `10003`을 공유하고 socket directory는 mode `2770`이다. Worker가 mode `0660` socket을 만들면 API는 client 연결만 수행하며 db와 Web은 volume을 받지 않는다.

## Source and archive lifecycle

1. 원본 PDF는 Google Drive에 계속 보관한다.
2. 사용자가 필요한 파일을 저장소 밖 `FAMILYCARE_IMPORT_ROOT`로 수동 다운로드한다.
3. FamilyCare는 가족 구성원 하나를 지정한 batch로 파일을 가져온다.
4. 암호가 필요하면 batch scope의 in-memory password를 사용한다.
5. extraction에 성공한 source는 app-managed archive에 저장할 때 document별 data key로 암호화한다.
6. data key는 runtime master key로 wrap하고 DB에는 wrapped key와 암호화 metadata만 저장한다.
7. 복호화 평문과 OCR page image는 작업별 임시 directory에만 존재하고 모든 종료 경로에서 삭제한다.
8. import source를 자동 삭제하거나 Google Drive 원본을 변경하지 않는다.
9. archive를 쓰기 직전에 stop 상태와 item lease heartbeat를 확인하고, durable archive write 직후에도 다시 확인한다. 후자의 확인이 실패하면 DB metadata가 아직 archive를 참조하지 않으므로 새 ciphertext를 definite orphan으로 삭제한다.
10. `mark_succeeded()` outcome이 불명확해진 뒤에는 ciphertext를 보존하고 password scope를 폐기한다. `batch_archive_commit_uncertain` 안정 이벤트만 남기며 archive object key나 private content를 로그에 기록하지 않는다.

Archive는 고정 크기 volume을 미리 할당하지 않고 실제 문서만큼 증가한다. 일관된 backup 단위는 PostgreSQL custom dump, quiesced encrypted archive snapshot, 별도 보관한 동일 master-key recovery copy다. snapshot 생성과 실제 복구는 packaging·검증·materialization과 분리한다.

### Private PDF capacity

Private batch source, decrypted plaintext extent, and managed archive payload are each bounded to 128 MiB. This capacity boundary does not change parser isolation: PDF pages remain capped at 500; parser output and `RLIMIT_FSIZE` remain 64 MiB; child address space remains 1536 MiB, child CPU 90 seconds, parent wall timeout 120 seconds, and open descriptors 64.

## Password handling

- batch는 정확히 한 FamilyMember를 가진다.
- 사용자 입력 password는 process memory에만 있고 API response, DB, job payload, log에 없다.
- 동일 batch의 암호 문서에 우선 재사용하되 실패 파일만 새 password를 요청한다.
- password 값으로 문서 소유자나 가족 관계를 추론하지 않는다.
- Worker 반복은 scope expiry를 정리하고 재입력은 이전 scope를 교체·폐기한다. 실행 중인 batch의 cancellation·stop·parser/OCR cancellation·lease loss는 해당 batch scope를 폐기하고 secret-server batch identity를 deactivate한다. Worker shutdown은 전체 registry를 폐기한다.
- Worker가 아직 잡지 않은 대기 batch를 API에서 취소하면 별도 control frame을 보내지 않으므로 최대 5분 expiry에서 정리된다. 정상 sibling 성공 때는 같은 batch password를 재사용할 수 있도록 즉시 폐기하지 않으며, 불명확한 archive success commit에서는 scope를 폐기한다.
- 재분석은 관리 archive를 사용하므로 정상 import 후 원본 PDF password를 매번 요구하지 않는다.

## Encrypted archive orphan boundary

Archive write와 DB success transition 사이의 실패는 두 종류로 나눈다.

- DB persistence가 시작되기 전에 stop 또는 owned heartbeat가 실패하면 새 object는 definite orphan이다. Worker는 object key를 다시 구성해 정확히 그 ciphertext만 삭제하고, 원본 import source와 Google Drive 파일은 건드리지 않는다.
- `mark_succeeded()`가 시작된 뒤 예외가 발생하면 DB가 이미 commit되었는지 알 수 없다. 이 경우 ciphertext를 삭제하면 committed `managed_archives` row가 가리키는 object를 잃을 수 있으므로 보존하고, password를 폐기한 뒤 `batch_archive_commit_uncertain`만 기록한다.

`familycare-archive-audit`는 모든 `managed_archives` row의 object key·ciphertext size와 Worker archive root의 mode-`0600` regular object를 대조한다. DB 연결은 startup option과 transaction 양쪽에서 read-only이고 repeatable-read이며, filesystem은 `nofollow` metadata만 읽는다. 결과는 database reference, archive object, match, missing, size mismatch, unreferenced, temporary, unexpected 개수와 `clean`/`findings` 상태뿐이다. object key, path, ciphertext, 문서 metadata는 출력하지 않는다.

이 audit는 exit `0` clean, `1` findings, `2` configuration/database/filesystem error를 사용한다. 어떤 결과도 자동 삭제·격리·보존 정책 실행 권한을 주지 않으며 audit 자체에는 삭제 API가 없다. Worker 쓰기가 진행 중이면 temporary entry 또는 snapshot race가 관찰될 수 있으므로 authoritative report는 archive writer를 quiesce한 뒤에만 만든다. 실제 archive에 대한 audit 실행, findings별 object 식별·승인, 격리·삭제와 운영 UI는 별도 운영 작업으로 남는다.

## Archive key

- `FAMILYCARE_ARCHIVE_MASTER_KEY_FILE`은 저장소 밖의 absolute regular mode `0600` file이며 numeric owner UID `10002`, 내용은 정확히 32 bytes다.
- Compose는 key file을 Worker에만 read-only bind mount한다. API와 Web에는 key file 또는 key 값이 없다.
- key 자체는 환경변수 값, Compose YAML, image layer, DB, log에 넣지 않는다.
- 사용자가 별도로 보관하는 recovery copy는 앱이 직접 읽거나 동기화하지 않으며 Git·DB·container image에 들어가지 않는다.
- master key가 없으면 archive를 새로 생성하거나 읽지 않고 fail closed한다.
- key rotation은 old/new key를 동시에 검증하고 wrapped data key만 교체한 뒤 완료하는 별도 관리 작업이다.

## Offline backup-set boundary

`scripts/private_runtime_backup.py`는 live PostgreSQL이나 Compose volume을 직접 snapshot하지 않는다. 운영자가 writer를 quiesce하고 저장소 밖에 미리 만든 PostgreSQL custom-format dump와 flat encrypted archive snapshot만 입력으로 받는다. 실제 named-volume 취득, `pg_dump`, `pg_restore`, 서비스 전환은 이 도구 밖의 별도 승인 절차다.

- `capture`는 absolute external input과 새 mode-`0700` destination만 허용한다. DB artifact는 `PGDMP` custom-format magic과 mode `0600`, archive root는 mode `0700`, object는 32자리 opaque key·mode `0600`·128 MiB 이하 regular file이어야 한다. 임시·예상 밖 entry, symlink, repository/overlap path, 기존 destination은 fail closed한다.
- backup set은 mode-`0600` `database.pgcustom`, `archive.tar`, `manifest.json` 세 파일만 가진다. manifest는 artifact SHA-256·size·object count와 non-secret key version만 포함하고 object key·path는 포함하지 않는다. master key에서 domain-separated HMAC key를 파생해 manifest를 인증하지만 master-key bytes나 recovery copy를 set 안에 복사하지 않는다.
- `verify`는 directory/file mode와 exact shape, manifest HMAC, dump magic, artifact hash·size, bounded flat tar member를 다시 확인한다. 다른 recovery key, 변조, replacement race는 안정적인 error code로 거부한다.
- `materialize`는 검증한 DB dump와 archive objects를 저장소 밖의 완전히 새 destination에만 복사한다. 기존 경로를 덮어쓰지 않고 DB를 생성·복원하지 않으며 application service를 시작하지 않는다.
- CLI는 path를 argv로 받지 않는다. local operator가 환경에서 지정한 path만 읽고 `BACKUP_CAPTURED`, `BACKUP_VERIFIED`, `RESTORE_INPUTS_MATERIALIZED` 또는 path-free error code만 출력한다.

합성 custom-dump bytes와 실제 archive 암호화 구현으로 capture→verify→materialize→decrypt round trip을 검증했다. 이는 실제 PostgreSQL semantic restore, named-volume snapshot consistency, recovery time, 실제 자료 복호화 성공을 검증한 것이 아니다.

## Selective local OCR (merged; private acceptance pending)

선택적 OCR 계약과 Worker 구현은 `main`에 있으며 합성 renderer/engine/processor/cleanup/atomic-persistence 테스트와 Worker 이미지 language smoke 경계가 있다. 이는 실제 private PDF acceptance를 의미하지 않는다.

- native extraction이 `OCR_REQUIRED`로 분류한 page만 OCR한다. `TEXT_SUFFICIENT` page는 renderer와 engine을 호출하지 않는다.
- renderer는 이미 열린 read-only descriptor에서 128 MiB 이내 bounded bytes를 읽어 PDFium으로 한 page를 fixed 300 DPI로 렌더링한다. source PDF path를 OCR renderer에 전달하거나 다시 열지 않는다.
- OCR engine은 `/usr/bin/tesseract`를 fixed `kor+eng` argv로 `shell=False` 실행하고 `stdout ... tsv`를 bounded bytes로 파싱한다. `pytesseract` dependency와 TSV/image artifact file은 없다.
- page 전체 PDF나 image를 외부 OCR provider로 보내지 않는다.
- OCR text, coordinates, engine/language/version과 quality warning을 `ocr_layers`/`ocr_pages`/`ocr_blocks` 별도 extraction layer로 저장한다. native extraction rows와 Evidence는 덮어쓰지 않고 source layer를 구분한다.
- 각 selected page의 PNG는 recognition 직후 삭제하고, outer mode-0700 workspace는 성공·실패·취소·timeout·shutdown 경로에서 다시 삭제한다. cleanup failure는 성공으로 숨기지 않는다.
- batch status에는 `ocr_state`, 0..500 `ocr_pages_processed`, unique bounded warning-code allowlist만 projection한다. OCR text, coordinates, image path, filename, stderr는 반환하지 않는다.

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

현재 실제 자료 acceptance는 수행하지 않았다. 합성 Worker/API 테스트와 private Compose permission smoke는 실제 private PDF, mobile, Windows, Tailscale device/network, provider, private OCR acceptance를 검증하지 않는다. 해당 항목은 모두 별도 검증 대기 상태이며, 실제 자료 검증은 private runtime PR이 병합되고 synthetic private-runtime acceptance가 끝난 뒤 사용자가 지정한 저장소 밖 path와 문서에만 수행한다.

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
- backup capture/materialize 실패는 해당 호출이 새로 만든 destination만 정리하고 기존 source, backup set, DB, archive root, key를 수정하지 않는다.
- archive audit finding은 count-only report로 끝나며 자동 삭제·격리·재시도하지 않는다.

## Tests

- 가족별 batch scope와 cross-member 혼합 거부
- 한 번 입력 password 재사용, 부분 실패 재입력, DB/job/log 비저장
- encrypted plaintext 128 MiB extent, managed archive payload 128 MiB bound와 500-page pre-clone bound, parser/archive/persistence 미호출
- cancellation·stop·lease loss의 registry disposal과 secret-server deactivation callback
- archive 전후 heartbeat, definite-orphan 삭제, ambiguous commit ciphertext 보존과 안정 이벤트
- printable path-free display-label normalization과 API/OpenAPI/JSON Schema parity
- encrypted archive round-trip, tamper, wrong/missing key, key-wrap rotation
- 성공·실패·취소·shutdown 임시 평문 cleanup
- `OCR_REQUIRED` page만 한국어·영어 local OCR 실행, `TEXT_SUFFICIENT` skip, and fixed 300 DPI
- OCR layer와 native extraction의 provenance 분리, `ocr_layers`/`ocr_pages`/`ocr_blocks` atomic persistence
- descriptor-derived PDFium, direct no-shell `/usr/bin/tesseract` stdout TSV, no pytesseract/artifact dependency
- page별 PNG 즉시 삭제와 outer workspace cleanup, bounded batch progress projection
- Worker image build 뒤 `tesseract --list-langs`에서 synthetic `eng`/`kor` availability 확인
- Compose에서 gateway만 host port를 가지는지 검사
- API/Worker/PostgreSQL internal network 접근
- restart 후 DB·archive 읽기와 running job recovery
- service-worker/browser storage와 log leakage 검사
- 실제 자료 acceptance 전후 Git safety scan
- 합성 PostgreSQL custom dump와 encrypted archive의 authenticated backup capture·verify·fresh materialization·decrypt round trip
- backup tamper, wrong key, repository/overlap path, symlink, incomplete archive, existing destination, post-verification replacement 거부
- read-only archive audit의 clean/missing/size-mismatch/unreferenced/temporary/unexpected aggregate와 object key·path 비출력
- database audit connection의 startup/transaction read-only 강제와 audit 전후 archive entry byte identity

## Invariants

1. 실제 source, archive, DB, key는 저장소 밖에 있다.
2. PDF password와 archive master key는 DB, job, log, Git, image에 없다.
3. import source와 Google Drive 원본은 v0.1이 수정하거나 삭제하지 않는다.
4. imported document는 archive encryption 성공 전 ready가 아니다.
5. OCR page image는 page 처리 직후와 outer 작업 종료 후 남지 않는다.
6. native extraction과 OCR provenance는 각각 queryable한 별도 layer이고 OCR이 native block을 덮어쓰지 않는다.
7. Tailscale과 app login 중 하나를 다른 하나의 대체물로 취급하지 않는다.
8. 호스트 암호화와 swap 변경은 v0.1 완료 조건이 아니다.
9. backup set은 DB dump와 encrypted archive snapshot만 담고 master-key recovery copy는 별도 보관한다.
10. audit finding은 삭제 지시가 아니며 actual archive mutation에는 별도 식별·보존 정책·명시적 승인이 필요하다.

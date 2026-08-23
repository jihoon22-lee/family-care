# PDF ingestion design

- 상태: 후속 구현 기준
- 적용 단계: Synthetic PDF Ingestion

## Scope

저장소 밖 PDF를 안전하게 검증·추출하고 모든 결과를 원문 페이지로 추적할 수 있게 하는 파이프라인을 정의합니다. Foundation은 이 파이프라인을 구현하지 않으며 합성 fixture와 인터페이스만 준비합니다.

## Inputs

- 비공개 외부 파일 참조
- 문서 종류 후보
- 선택적 PDF 암호의 일회성 런타임 제공
- 작업 ID와 멱등 키
- 크기, 페이지 수, 처리 시간 제한

암호는 데이터베이스, 작업 payload, 로그에 저장하지 않습니다.

## Outputs

- SHA-256 콘텐츠 해시
- MIME, 파일 크기, 페이지 수
- 페이지별 텍스트 블록, 좌표, 읽기 순서
- 표 구조 후보
- 추출기와 설정 버전
- 품질 지표와 경고 코드
- OCR 필요 여부
- 관리자 검수 상태
- 실패 시 안정적인 오류 코드와 재시도 가능 여부

## Pipeline

### 1. Intake validation

파일을 열기 전에 경로가 승인된 외부 root 안에 있는지 확인하고 symlink 탈출을 거부합니다. PDF magic bytes, 크기, 일반 파일 여부를 확인합니다.

### 2. Content identity

원본 바이트의 SHA-256을 스트리밍 계산합니다. 같은 해시와 추출기 설정의 성공 결과가 있으면 재사용합니다.

### 3. Isolated workspace

작업 root 아래 무작위 작업 디렉터리를 만들고 현재 프로세스만 접근할 권한을 적용합니다. 디렉터리 이름에 실제 파일명이나 가족 이름을 넣지 않습니다.

### 4. Password handling

암호화 PDF는 런타임 제공 암호로만 엽니다. 해제본이 필요하면 작업 디렉터리에 만들고 원본 해시와 별개로 취급합니다. 암호와 해제본은 영구 저장하지 않습니다.

### 5. Native extraction

PyMuPDF로 페이지 텍스트 블록과 좌표를 우선 추출합니다. pdfplumber는 표와 좌표 해석이 필요한 페이지에서 보조로 사용합니다. 두 도구의 전체 문서 결과를 무조건 중복 저장하지 않습니다.

### 6. Quality assessment

페이지별 문자 수, 유효 한글·영문 비율, 비정상 반복, 빈 페이지 비율, 읽기 순서 경고를 계산합니다. 품질이 낮다는 이유만으로 추출 성공으로 표시하지 않습니다.

### 7. OCR classification

텍스트가 없거나 품질 임계치 미달인 페이지만 OCR 후보로 표시합니다. OCR 실행은 별도 작업 유형이며 전체 문서에 기본 적용하지 않습니다.

### 8. Structure candidates

증권 표, 약관 제목·조항·별표 후보를 만들되 가입 여부나 판정 규칙을 확정하지 않습니다.

### 9. Persist and cleanup

구조화 결과와 페이지 Evidence를 트랜잭션으로 저장합니다. 성공, 실패, 취소, 프로세스 종료 경로에서 임시 산출물을 삭제합니다.

## Job states

- `queued`
- `running`
- `succeeded`
- `retryable_failed`
- `permanently_failed`
- `cancelled`

작업은 lease와 heartbeat를 가지며, lease 만료 후 재개할 수 있습니다. 콘텐츠 해시와 설정 해시가 멱등성을 보장합니다.

## Error codes

| Code | 의미 | 재시도 |
|---|---|---|
| `DOCUMENT_NOT_FOUND` | 승인된 root에서 파일을 찾지 못함 | 입력 수정 후 |
| `DOCUMENT_PATH_ESCAPE` | symlink 또는 정규화 경로가 root 밖으로 나감 | 아니오 |
| `UNSUPPORTED_FILE_TYPE` | PDF가 아님 | 아니오 |
| `DOCUMENT_TOO_LARGE` | 크기 제한 초과 | 정책 변경 후 |
| `PAGE_LIMIT_EXCEEDED` | 페이지 제한 초과 | 정책 변경 후 |
| `PASSWORD_REQUIRED` | 암호화됐으나 암호 없음 | 입력 후 |
| `PASSWORD_INVALID` | 제공 암호가 틀림 | 입력 후 |
| `PDF_CORRUPT` | 파서가 구조를 읽지 못함 | 다른 원본 필요 |
| `EXTRACTION_TIMEOUT` | 시간 제한 초과 | 제한된 횟수 |
| `OCR_REQUIRED` | native text 품질 미달 | OCR 단계 |
| `TEMP_CLEANUP_FAILED` | 임시 산출물 삭제 실패 | 보안 대응 |

오류 메시지에 실제 파일명, 경로, 문서 본문을 포함하지 않습니다.

## Failure behavior

- 입력 검증 실패는 문서를 열거나 복사하기 전에 종료합니다.
- 암호·손상·제한 초과는 안정적인 오류 코드로 영구 실패와 입력 수정 필요를 구분합니다.
- 시간 제한과 일시적 자원 부족만 제한된 재시도 대상으로 분류합니다.
- 부분 추출 결과는 성공 결과로 노출하지 않고 작업 단위 transaction을 취소합니다.
- 어떤 실패 경로에서도 임시 파일 정리를 시도하며, 정리 실패는 원래 오류와 함께 별도 보안 상태로 보존합니다.

## Invariants

1. 모든 추출 블록은 DocumentVersion과 1-based 페이지를 가집니다.
2. 관리자 확정값은 원시 추출을 덮어쓰지 않습니다.
3. OCR은 native 추출 품질 기준을 통과하지 못한 페이지에만 실행합니다.
4. 같은 원본 해시·설정은 성공 결과를 하나만 가집니다.
5. 임시 평문과 이미지가 영구 저장소나 Git 작업트리에 생성되지 않습니다.
6. 실제 파일명은 작업 ID나 로그 상관관계 ID로 대체합니다.

## Security considerations

- PDF 파서는 신뢰하지 않는 입력을 처리하는 격리 프로세스로 실행합니다.
- CPU, 메모리, 파일 크기, 페이지 수, 실행 시간 제한을 둡니다.
- 외부 URL이나 첨부 파일 실행을 허용하지 않습니다.
- PDF JavaScript, launch action, embedded file을 실행하지 않습니다.
- 작업 root와 원본 root는 컨테이너에 필요한 권한만 읽기/쓰기로 mount합니다.
- 임시 삭제 실패는 운영자가 확인할 수 있는 보안 사건입니다.

## Tests

- 텍스트형·표형·스캔형 합성 PDF
- 손상 magic bytes와 잘린 xref
- 암호 필요와 잘못된 암호
- symlink root 탈출
- 파일·페이지 제한
- 동일 해시 중복 제출
- 프로세스 중단과 lease 회수
- 성공·실패·취소의 임시 파일 삭제
- 페이지와 좌표 round-trip
- 로그 금지 필드 검사

## Deferred decisions

OCR 엔진, 샌드박스 런타임, 구체적인 품질 임계값, PDF 암호 입력 UI는 합성 corpus 측정 후 결정합니다. 전체 PDF를 외부 AI에 보내는 방식은 선택지에 포함하지 않습니다.

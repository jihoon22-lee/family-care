# FamilyCare agent workflow

- 검토 기준일: 2026-09-07
- 적용 범위: 개발 에이전트 지침·스킬·검증 운영. 제품 런타임 모델 변경은 포함하지 않는다.
- 사용자 지정 컨텍스트 윈도우·자동 압축 한계는 유지한다. 이 작업은 개인 설정 파일을 수정하지 않는다.

## Instruction ownership

루트 `AGENTS.md`는 공통 개인정보·권한·아키텍처와 작업 시작점을 소유한다.
`apps/web`, `apps/api`, `workers/analyzer`의 추가 지침은 각 영역의 구현·검토 차이만 담는다.
루트에서 시작한 세션도 수정 대상의 하위 지침을 명시적으로 확인한다.
브랜치·커밋 상세는 `CONTRIBUTING.md`, 검사 명령은 `docs/design/test-strategy.md`가 기준이다.

마일스톤 진행은 [로드맵](plan/000-project-roadmap.md)에서 현재 메인·명세·WP로 연결한다.
장기 작업은 기존 WP와 workthrough에 결정·변경·실행 증거·남은 작업을 이어 적는다.
대화마다 새 계획이나 기록을 생성하지 않는다. 원문·개인정보·실제 경로는 기록하지 않는다.

## Skill selection

| 작업 | 사용할 역량 | 범위 |
|---|---|---|
| 마일스톤 구현 | `familycare-milestone-work` | 현재 이슈·의존성·PR 묶음과 실제 완료 기준 연결 |
| 변경 검증 | `familycare-verify` | 영향 범위에 따른 검사와 증거 구분 |
| 변경 기록 | 기존 `workthrough` 또는 저장소 기록 형식 | 작업/PR당 하나, 실제 결과만 기록 |
| OpenAI 모델·기능 확인 | 공식 문서 MCP와 기존 `openai-docs` | 검색한 공식 페이지를 실제 조회하고 근거 확인 |
| GitHub 상태 확인 | GitHub connector, 미지원 GET은 `gh api` | 요청 범위의 저장소만 조회; 쓰기 권한과 구분 |
| 일반 웹 검색 | 내장 검색, 필요한 경우 `web-search` | GitHub·공식 문서 전용 검색을 대체하지 않음 |
| 내부 UI 수정 | 기존 React·Vite 화면과 Web 지침 | 마케팅 랜딩 페이지 스킬 제외 |

저장소 스킬은 `.agents/skills/`에 짧은 `SKILL.md`로 공유한다. description 앞부분에
실제 용도를 쓰고 자주 혼동하는 작업만 제외한다. 필요 없는 스킬을 읽거나 같은 이름으로
복제하지 않는다. 개인/배포 플러그인의 캐시 파일은 저장소 설정처럼 수정하지 않는다.
스킬 validator 통과와 실제 자동 선택·실행 성공은 서로 다른 검증이다.

## Models and delegation

사용자가 선택한 모델·추론 수준을 우선한다. 변경이 필요할 때는 현재 클라이언트의
선택 가능 모델과 공식 문서를 확인하고 업무 결과로 비교한다. 모델 이름을 작업 지침의
강제 조건이나 제품 API 모델 설정으로 복사하지 않는다.

2026-09-07 공식 모델 역할에 따른 선택 예시:

- Astra: 계약 재설계·판정/계산·데이터 전환처럼 여러 경계의 판단이 필요한 작업.
- Sol/Terra: 범위가 명확한 구현과 일반 개발. 필요한 품질을 만족하는 낮은 추론 수준부터 비교.
- Luna: 좁은 자료 확인·정형 요약. 최종 보안·판정 판단을 요약만으로 대체하지 않음.

복잡한 작업의 독립 탐색·리뷰는 루트 지침에 따라 최대 2개 서브에이전트로 나눈다.
단순 변경은 단일 에이전트가 처리한다. 공유 파일 작성자와 무거운 검사 실행 주체를
명확히 하며 중복 탐색·검증으로 병렬 작업 이점을 없애지 않는다.

모델 선택·병렬화 효과는 첫 구현 PR부터 불필요한 확인 질문, 중복 검사, 작업 시간,
리뷰에서 발견한 회귀로 비교한다. 측정 전에 속도·비용 개선을 확정하지 않는다.
API의 비동기 도구 호출 같은 기능을 AGENTS 문구만으로 활성화할 수 있다고 가정하지 않는다.

## Official references

- [GPT-6 Astra prompting](https://developers.openai.com/api/docs/guides/latest-model): 지침 충돌, 승인 중단, 위임, 검증 범위를 실제 업무에 맞게 조정한다.
- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md): 루트부터 작업 디렉터리까지 지침을 합치며 같은 위치에서는 override가 우선한다.
- [Skills](https://learn.chatgpt.com/docs/build-skills): `.agents/skills` 배치, 점진적 로딩, 명확한 description과 실행 범위.
- [Models](https://learn.chatgpt.com/docs/models): 역할별 모델·추론 선택과 계정/클라이언트별 가용성.
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): 요청 또는 적용 지침에 따른 위임과 읽기 중심 병렬 작업.
- [Best practices](https://learn.chatgpt.com/guides/best-practices): 목표·맥락·제약·완료 기준과 간결한 재사용 지침.

공식 문서의 권고를 이 저장소의 필수 개인정보·CI 정책보다 넓은 권한으로 해석하지 않는다.

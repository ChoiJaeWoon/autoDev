# Agent Forge 3단계: 개인 프로젝트 운영 자동화 에이전트 설계 문서

- **작성일:** 2026-10-29
- **대상 범위:** 3단계 — GitHub 이슈 수집, 단계별 인간 승인(Human-in-the-loop), 체크포인트 재실행(Checkpoint Retry), PR 초안 자동 생성 및 게시

---

## 1. 배경 및 목적 (Intent & Why)

1단계(자율 코딩 하네스 코어)와 2단계(레거시 웹·JSP 브라우저 시각 회귀 검증)를 통해 단일 태스크에 대한 안전한 자율 수정·검증 루프가 완성되었다.

3단계의 목적은 이를 실제 **개인 프로젝트 운영 워크플로(DevOps / Issue-to-PR 파이프라인)**로 확장하는 것이다:
1. **GitHub 이슈 기반 작업 수집:** GitHub 저장소의 이슈(`#번호`, 제목, 본문)를 읽어와 에이전트 미션으로 변환한다.
2. **인간 승인 게이트(`WAITING_APPROVAL`):** 에이전트가 수립한 작업 분해 계획(`subtasks`)과 수정 파일 화이트리스트를 사람이 먼저 검토·승인한 뒤 테스트 작성 및 코드 수정을 진행하게 한다.
3. **체크포인트 기반 중단 지점 재실행(`retry_from_checkpoint`):** 실행 도중 중단(`HALT`)되더라도 처음(`PLAN`)부터 다시 토큰을 낭비하지 않고, 마지막으로 성공한 체크포인트(예: 테스트 스위트가 잠긴 `baseline_commit_sha`)부터 즉시 재실행한다.
4. **PR 초안 자동 생성 및 GitHub 게시:** 검증(`VERIFY`)을 통과해 `DONE`에 도달하면 하네스 검증 요약(수정 파일, 커밋 해시, 토큰·비용, 시각 회귀 결과)이 담긴 한국어 PR 초안 마크다운을 자동 생성하고, 원클릭으로 GitHub Draft PR을 생성한다.

---

## 2. 핵심 아키텍처 및 컴포넌트 설계

### 2.1 GitHub 연동 어댑터 (`agent_forge/integrations/github_ops.py`)
- **토큰 및 저장소 자동 탐색:**
  - `FORGE_GITHUB_TOKEN` / `GITHUB_TOKEN` / `GITHUB_PERSONAL_ACCESS_TOKEN` 환경변수, 로컬 `.env`, 또는 MCP 설정(`~/.gemini/config/mcp_config.json`)에서 토큰을 안전하게 탐색한다.
  - 로컬 Git `remote.origin.url`에서 기본 `owner/repo`를 자동 추출한다.
- **이슈 조회 (`fetch_github_issue`, `list_open_issues`):**
  - `GET /repos/{owner}/{repo}/issues/{issue_number}`를 호출해 이슈 제목, 본문, URL을 가져오고 에이전트용 한국어 태스크 설명으로 변환한다.
- **PR 초안 생성기 (`build_pr_draft_markdown`):**
  - `RunRecord`가 `DONE` 상태가 되면 다음 항목을 포함한 구조화된 마크다운을 생성한다:
    - 연결 이슈 (`Closes #N`)
    - 작업 요약 및 하위 계획 (`PlanSpec.subtasks`)
    - 하네스 가드레일 및 검증 요약 (브랜치명, 테스트 잠금 커밋, 최종 커밋, 시도 횟수, 총 토큰·비용, Playwright 시각 회귀 변경률 및 DOM 상태)
- **GitHub Draft PR 발행 (`publish_github_pr`):**
  - 작업 브랜치(`forge/run-xxxx`)를 원격 저장소에 푸시하고 `POST /repos/{owner}/{repo}/pulls`로 Draft PR을 생성한 뒤 생성된 `pr_url`을 반환한다.

### 2.2 상태 모델 및 체크포인트 재실행 (`state.py`, `store.py`, `engine.py`)
- **`RunRecord` 확장 필드 (SQLite 자동 마이그레이션 포함):**
  - `issue_number: int | None = None`
  - `issue_url: str = ""`
  - `pr_draft_markdown: str = ""`
  - `pr_url: str = ""`
- **`ForgeEngine.retry_from_checkpoint(run_id, max_extra_attempts=3)`:**
  - `HALT` 상태인 Run을 재실행할 때:
    - `baseline_commit_sha`와 `plan`이 이미 존재하면, `PLAN`과 `WRITE_TEST`를 건너뛰고 Git 워킹트리를 `baseline_commit_sha`로 원자적 복원한 뒤 `RunState.IMPLEMENT`부터 즉시 재개한다.
    - `plan`만 존재하면 `WRITE_TEST` 단계부터 재개한다.
- **`VERIFY` 통과 시 PR 초안 자동 기록:**
  - `verify_res.passed`가 참이면 `run.state = RunState.DONE` 전이와 동시에 `run.pr_draft_markdown = build_pr_draft_markdown(run)`을 자동 생성해 SQLite에 저장한다.

### 2.3 CLI 및 웹 대시보드 확장 (`cli.py`, `server.py`, `static/*`)
- **CLI 명령어:**
  - `forge issue-run <issue_number>`: GitHub 이슈를 가져와 미션 실행
  - `forge retry <run_id>`: 중단(`HALT`)된 Run을 마지막 체크포인트부터 재실행
  - `forge pr <run_id> [--publish]`: 생성된 PR 초안 마크다운 출력 및 GitHub Draft PR 발행
- **웹 인스펙터 대시보드 (`forge ui`):**
  - **GitHub 이슈 불러오기 바:** 이슈 번호(`#1`, `#2` 등)를 입력해 GitHub에서 제목·본문을 즉시 불러와 미션 입력창에 채우거나 바로 실행(`GET /api/github/issues/<number>`)
  - **체크포인트 재실행 배너 (`#halt-retry-banner`):** `HALT` 상태인 Run을 선택하면 상단에 **`Retry from Checkpoint`** 버튼 표시 (`POST /api/runs/<run_id>/retry`)
  - **PR 초안 뷰어 & GitHub 발행 패널 (`#pr-draft-panel`):** `DONE` 상태인 Run에서 자동 생성된 PR 마크다운 프리뷰, 클립보드 복사 버튼, **`Publish Draft PR to GitHub`** 버튼 제공 (`POST /api/runs/<run_id>/pr`)

# Agent Forge 3단계: 개인 프로젝트 운영 자동화 에이전트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitHub 이슈 수집, 인간 승인 게이트(`WAITING_APPROVAL`), 중단(`HALT`) 지점 체크포인트 재실행(`retry_from_checkpoint`), 한국어 PR 초안 자동 생성 및 GitHub Draft PR 발행 기능을 구축하고 웹 대시보드 및 CLI에 통합한다.

**Architecture:** `agent_forge/integrations/github_ops.py`에서 GitHub REST API 연동 및 한국어 PR 초안 마크다운 빌더를 제공하고, `RunRecord`와 `StateStore`에 이슈/PR 메타데이터 컬럼(기존 DB 자동 마이그레이션 포함)을 추가한다. `ForgeEngine.retry_from_checkpoint()`를 통해 `HALT` 상태인 실행을 마지막 체크포인트(`baseline_commit_sha` 또는 `plan`)에서 원자적으로 복원해 재개하며, 웹 인스펙터 대시보드와 CLI에서 이슈 불러오기, 체크포인트 재실행, PR 초안 미리보기 및 GitHub 발행을 제어한다.

**Tech Stack:** Python 3.11+, SQLite3, Git CLI, GitHub REST API (`urllib.request`), Typer, Vanilla HTML/CSS/JS (화이트톤·곡선·여백 중심 디자인, 이모지 0개).

---

### Task 1: 상태 모델 확장, GitHub 연동 어댑터, 체크포인트 재실행 엔진 구현

**Files:**
- Modify: `agent_forge/core/state.py`
- Modify: `agent_forge/core/store.py`
- Create: `agent_forge/integrations/__init__.py`
- Create: `agent_forge/integrations/github_ops.py`
- Modify: `agent_forge/core/engine.py`
- Create: `tests/test_stage3_ops.py`

- [ ] **Step 1: 실패하는 단위 테스트 작성 (`tests/test_stage3_ops.py`)**
  - `RunRecord` 및 `StateStore`의 3단계 필드(`issue_number`, `issue_url`, `pr_draft_markdown`, `pr_url`) 저장·조회 및 레거시 SQLite 테이블 자동 마이그레이션 검증
  - `github_ops.py`의 `format_issue_as_task`, `build_pr_draft_markdown`, `fetch_github_issue`(모킹), `publish_github_pr`(모킹) 검증
  - `ForgeEngine`에서 `VERIFY` 통과 시 `pr_draft_markdown` 자동 생성 검증
  - `ForgeEngine.retry_from_checkpoint()` 호출 시 `HALT`된 Run이 `PLAN`/`WRITE_TEST`를 다시 실행하지 않고 `baseline_commit_sha`로 롤백한 뒤 `IMPLEMENT`부터 재개하여 `DONE`에 도달하는지 검증
- [ ] **Step 2: 테스트를 실행하여 실패(RED) 확인**
  - Run: `.venv/bin/pytest tests/test_stage3_ops.py -v`
- [ ] **Step 3: `state.py`, `store.py`, `github_ops.py`, `engine.py` 구현**
  - `RunRecord`에 `issue_number`, `issue_url`, `pr_draft_markdown`, `pr_url` 추가
  - `StateStore._init_db()`에 기존 `runs` 테이블 컬럼 누락 시 `ALTER TABLE runs ADD COLUMN ...` 자동 마이그레이션 로직 추가
  - `agent_forge/integrations/github_ops.py` 작성
  - `ForgeEngine`에 완료 시 PR 초안 자동 생성 및 `retry_from_checkpoint` 메서드 추가
- [ ] **Step 4: 테스트 통과(GREEN) 확인 및 한국어 커밋**
  - Run: `.venv/bin/pytest tests/test_stage3_ops.py -v`

---

### Task 2: CLI 명령어 및 웹 인스펙터 대시보드 3단계 기능 통합

**Files:**
- Modify: `agent_forge/cli.py`
- Modify: `agent_forge/dashboard/server.py`
- Modify: `agent_forge/dashboard/static/index.html`
- Modify: `agent_forge/dashboard/static/styles.css`
- Modify: `agent_forge/dashboard/static/app.js`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: 대시보드 3단계 API 및 UI 요소 검증 테스트 추가 (`tests/test_dashboard.py`)**
  - `GET /api/github/issues/<number>` 엔드포인트 동작 검증
  - `POST /api/runs/<id>/retry` 체크포인트 재실행 엔드포인트 검증
  - `POST /api/runs/<id>/pr` PR 초안 생성 및 발행 엔드포인트 검증
  - 기존 `DONE` 상태 Run 조회 시 `pr_draft_markdown`이 비어 있으면 동적으로 백필(backfill)되어 반환되는지 검증
  - 대시보드 정적 파일 이모지 0개 규칙(`test_dashboard_files_contain_zero_emojis`) 유지 검증
- [ ] **Step 2: 테스트를 실행하여 실패(RED) 확인**
  - Run: `.venv/bin/pytest tests/test_dashboard.py -v`
- [ ] **Step 3: CLI(`cli.py`) 및 웹 대시보드(`server.py`, `index.html`, `styles.css`, `app.js`) 구현**
  - `cli.py`에 `issue-run`, `retry`, `pr` 명령어 추가
  - `server.py`에 `GET /api/github/issues/<number>`, `POST /api/runs/<id>/retry`, `POST /api/runs/<id>/pr` 추가 및 `POST /api/runs`에서 `issue_number`, `issue_url` 수신 지원
  - `index.html`, `styles.css`, `app.js`에 GitHub 이슈 번호 불러오기 바, `HALT` 상태 체크포인트 재실행 배너, `DONE` 상태 PR 초안 마크다운 프리뷰/복사/GitHub Draft PR 발행 패널 추가 (화이트톤·곡선·여백 디자인, 이모지 0개)
- [ ] **Step 4: 전체 테스트 스위트 실행 및 한국어 커밋**
  - Run: `.venv/bin/pytest -v`

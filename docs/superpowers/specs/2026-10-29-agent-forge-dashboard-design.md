# Agent Forge Step 1-2: Web Inspector Dashboard 설계 문서

- **작성일:** 2026-10-29
- **대상 범위:** 1단계 후반부(Step 1-2) — SQLite 체크포인트 및 Git 스냅샷 기반 경량 웹 인스펙터(`forge ui`)

---

## 1. 배경 및 목적 (Intent & Why)

Step 1-1에서 완성한 Agent Forge 코어 엔진은 자율 개발 루프의 모든 상태 전이(`RunState`), 파일 화이트리스트 계획(`PlanSpec`), 시도별 트리 해시와 커밋(`AttemptRecord`), 에러 지문(`error_fingerprint`), 반성 노트(`reflection_notes`), 토큰·비용 예산을 `.forge/state.db`와 Git 브랜치 스냅샷에 원자적으로 기록한다.

Step 1-2의 목적은 이 데이터를 브라우저에서 직관적으로 검증할 수 있는 **개발자용 경량 인스펙터 대시보드(`forge ui`)**를 구축하는 것이다. 단순 로그 뷰어를 넘어 **"하네스가 언제, 어떤 가드레일로 에이전트를 통제했는가(테스트 파일 Read-Only 잠금, 진동 감지, 반성 루프 전환, 실제 Git Diff)"**를 한눈에 파악할 수 있도록 설계한다.

---

## 2. 디자인 원칙 및 절대 금지 조건 (Design Principles & Anti-Goals)

### 디자인 톤앤매너 (Clean White, Soft Curves & Generous Whitespace)
- **레퍼런스:** Stripe Dashboard, Linear Light Mode, Apple HIG 스타일의 밝고 정돈된 워크스페이스
- **색상 체계 (Soft Alabaster & Pure White Surface):**
  - 배경: `#f8fafc`(쿨 화이트 캔버스), `#ffffff`(카드 표면), `#f1f5f9`(인셋/코드 배경)
  - 보더 & 그림자: `1px solid #e2e8f0`, 은은한 입체감(`0 4px 20px -2px rgba(15, 23, 42, 0.04)`)
  - 곡선 & 여백: 패널 모서리 `16px`, 입력창·버튼 `10px~12px`, 상태 배지 `9999px`(Pill), 섹션 간격 `20px~24px`, 넉넉한 내부 패딩(`22px~26px`)
  - 타이포그래피: 본문 `Pretendard` / `Inter`, 모든 식별자·경로·해시·토큰·비용·로그·Diff는 고정폭(`JetBrains Mono`, `SFMono-Regular`, `monospace`) + `font-variant-numeric: tabular-nums`
  - 상태 시맨틱 컬러:
    - `DONE` / `PASS`: `#15803d` (배경 `#f0fdf4`, 보더 `#bbf7d0`)
    - `HALT` / `FAIL`: `#b91c1c` (배경 `#fef2f2`, 보더 `#fecaca`)
    - `WAITING_APPROVAL` / `REFLECT`: `#b45309` (배경 `#fffbeb`, 보더 `#fde68a`)
    - 진행 중(`INIT`~`VERIFY`): `#2563eb` (배경 `#eff6ff`, 보더 `#bfdbfe`)

### Anti-Goals (절대 금지 조건)
1. **이모지(Emoji) 전면 금지:** HTML, CSS, JS, API 응답 어디에도 이모지(로봇, 체크마트, 불꽃, 로켓 등)를 단 1개도 사용하지 않는다. 모든 상태는 영문 대문자 텍스트 배지(`DONE`, `HALT`, `PASS`, `FAIL`, `LOCKED`)와 6px 단색 상태 점(dot)으로만 표현한다.
2. **AI 생성 템플릿 클리셰 배제:** 보라색·청록색 네온 그라데이션 배경, 글래스모피즘(`backdrop-filter`), 과도한 둥근 모서리(`border-radius: 16px+`), 의미 없는 원형 게이지/장식용 차트를 일절 사용하지 않는다.
3. **XSS 취약 렌더링 금지:** 태스크 설명, 에러 출력, Git Diff 등 외부/LLM 유래 문자열을 `innerHTML`에 직접 보간하지 않고 `textContent` 및 안전한 DOM 노드 생성 함수만 사용한다.

---

## 3. 레이아웃 및 컴포넌트 구조 (Layout & Components)

전체 화면(`100vh`)을 좌우 분할 인스펙터(Split-Pane Inspector)로 구성한다.

1. **상단 헤더 바 (`44px` 고정 높이)**
   - 좌측: `AGENT FORGE` 타이틀, `INSPECTOR` 태그, 현재 연결된 DB 경로 표시
   - 우측: 총 Run 수 요약, `AUTO-REFRESH (3S)` 토글 버튼, `REFRESH` 수동 갱신 버튼
2. **좌측 사이드바 (`280px` 고정 폭, 독립 스크롤)**
   - 검색/상태 필터(`ALL`, `DONE`, `HALT`, `ACTIVE`)
   - Run 리스트: 최신순 정렬, 각 항목에 `run_id`(모노스페이스), 상태 배지(`DONE`/`HALT`), 태스크 요약(1줄 말줄임), 시도 횟수(`Attempts`), 누적 비용(`$0.0016`) 표시
3. **우측 메인 인스펙터 (가변 폭, 독립 스크롤)**
   - **섹션 A — Run Header & Telemetry Strip:** 선택된 Run의 전체 태스크 설명, 상태/중단 사유 배지, `branch_name`, `baseline_commit_sha`, `Attempts`, `Total Tokens`, `Total Cost (USD)`를 1px 그리드 메트릭 스트립으로 표시
   - **섹션 B — FSM Pipeline Bar:** `INIT → PLAN → WAITING_APPROVAL → WRITE_TEST → IMPLEMENT → VERIFY → REFLECT → DONE / HALT` 상태 전이 파이프라인을 수평 스텝 바로 표시하고, 거쳐간 단계와 최종 도달 상태를 하이라이트
   - **섹션 C — Plan & File Guard Scope (2단 그리드):**
     - 좌측: `Subtasks` 번호 목록 (`PlanSpec.subtasks`)
     - 우측: 화이트리스트 파일 목록 — `target_source_files` 옆에는 `[READ-WRITE]` 배지, `target_test_files` 옆에는 `[LOCKED IN IMPLEMENT]` 배지를 붙여 파일 가드 정책을 명시
   - **섹션 D — Attempt History & Commit Diff Inspector:**
     - 시도(`AttemptRecord`) 테이블: `#`, `Phase`, `Result (PASS/FAIL)`, `Exit Code`, `Tree Hash (8자)`, `Commit SHA (7자)`, `Tokens`, `Cost`, `Patch Summary`, `Error Fingerprint`
     - 각 행(또는 상단 `Baseline Test Lock Commit` 버튼)을 클릭하면 하단 **Code Diff & Diagnostics 뷰어**에 해당 커밋의 실제 `git show` Unified Diff(`+` 초록 / `-` 빨강 라인 하이라이트)를 렌더링
   - **섹션 E — Reflection Notes & Last Error Output:**
     - `reflection_notes` 리스트(존재 시)와 `last_error_output` 터미널 로그 뷰어

---

## 4. 백엔드 API 및 CLI 설계 (Backend & CLI Architecture)

외부 웹 프레임워크 의존성을 추가하지 않고 Python 표준 라이브러리(`http.server.ThreadingHTTPServer`)와 기존 `StateStore`를 결합하여 경량성과 이식성을 유지한다.

- **모듈 구조:**
  - `agent_forge/dashboard/__init__.py`
  - `agent_forge/dashboard/server.py`
  - `agent_forge/dashboard/static/index.html`
  - `agent_forge/dashboard/static/styles.css`
  - `agent_forge/dashboard/static/app.js`
- **HTTP 엔드포인트:**
  - `GET /` 및 `GET /static/<filename>`: 정적 파일 서빙 (경로 탐색 공격 차단 포함)
  - `GET /api/runs`: 저장된 모든 `RunRecord` 요약 목록 및 현재 백그라운드 워커 실행 상태(`worker_busy`) 반환 (최신 Run이 먼저 오도록 역순 정렬)
  - `GET /api/runs/<run_id>`: 특정 `RunRecord` 전체 데이터 + Git 커밋 Diff(`baseline_diff`, `attempt_diffs`) 반환
    - Git Diff 조회 헬퍼(`_resolve_commit_diff(repo_root, commit_sha)`): 타깃 레포지토리(또는 상위 Git 루트)에서 `git show --format= --unified=3 <commit_sha>`를 안전하게 실행해 Unified Diff 문자열을 반환하며, 커밋이나 경로가 없을 때는 빈 문자열로 우아하게 폴백한다.
  - `POST /api/runs`: 웹 대시보드 내 프롬프트 입력창(`New Autonomous Mission`)에서 한국어 미션, 타깃 레포지토리, 승인 모드(`auto_approve`)를 전달받아 백그라운드 스레드에서 `ForgeEngine.start_run`을 비동기 실행(`202 Accepted`)한다.
  - `POST /api/runs/<run_id>/resume`: `WAITING_APPROVAL` 상태에 멈춰 있는 Run을 웹에서 승인(`approve: true`) 또는 반려(`approve: false`)하여 재개(`202 Accepted`)한다.
- **CLI 엔드포인트 (`agent_forge/cli.py`):**
  - `forge ui [--port 8765] [--host 127.0.0.1] [--db-path .forge/state.db]` 명령어를 추가하여 터미널에서 즉시 인스펙터 서버를 기동할 수 있게 한다.

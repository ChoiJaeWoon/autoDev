# Agent Forge 2단계: AI 웹사이트 유지보수 에이전트 구현 계획서

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task using TDD.

**Goal:** Extend Agent Forge with Stage 2 Legacy Web (Java/JSP/HTML/CSS) maintenance capabilities: HTML/CSS repo-map indexing, standalone `javac`/`java` JUnit test execution, JSP preview rendering, Playwright Chromium DOM/layout verification, before/after/diff screenshot generation, and a Visual Regression comparison panel in the `forge ui` dashboard.

**Architecture:** Plug `PlaywrightVisualVerifier` into the existing `Verifier` protocol without rewriting `ForgeEngine`, persist `AttemptRecord.artifacts` in SQLite, and expose screenshot artifacts in the Light-Mode Web Inspector Dashboard.

**Tech Stack:** Python 3.11+, Playwright (`playwright.sync_api` Chromium), Pillow (`PIL.Image`, `ImageChops`), JDK 17 (`javac`/`java`), SQLite3, Vanilla HTML/CSS/JS.

---

### Task 1: HTML/CSS Repo Map & Checkpoint Artifact Persistence (`repo_map.py`, `state.py`, `store.py`, `engine.py`)

**Files:**
- Modify: `agent_forge/harness/repo_map.py`
- Modify: `agent_forge/core/state.py`
- Modify: `agent_forge/core/store.py`
- Modify: `agent_forge/core/engine.py`
- Modify: `tests/test_repo_map_and_file_guard.py`
- Modify: `tests/test_state_and_store.py`

- [ ] **Step 1: Add failing tests for HTML/CSS repo map indexing and `AttemptRecord.artifacts` SQLite persistence + schema migration**
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_repo_map_and_file_guard.py tests/test_state_and_store.py -v` and confirm RED**
- [ ] **Step 3: Implement HTML/CSS extraction in `repo_map.py`, `artifacts` field in `AttemptRecord`, `artifacts_json` persistence/migration in `store.py`, and `capture_baseline` + `artifacts` wiring in `engine.py`**
- [ ] **Step 4: Run `.venv/bin/pytest tests/test_repo_map_and_file_guard.py tests/test_state_and_store.py -v` and confirm GREEN**

---

### Task 2: Legacy Java/JSP Verifier & Playwright Visual Regression Engine (`verifiers/legacy_web.py`, `verifiers/playwright_stub.py`, `pyproject.toml`)

**Files:**
- Create: `agent_forge/verifiers/legacy_web.py`
- Modify: `agent_forge/verifiers/playwright_stub.py`
- Modify: `pyproject.toml`
- Create: `tests/test_stage2_legacy_web.py`

- [ ] **Step 1: Write failing tests in `tests/test_stage2_legacy_web.py` for Java standalone compilation/test execution, JSP preview HTML rendering, Playwright DOM/overflow/table-column checks, and Before/After/Diff screenshot generation**
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_stage2_legacy_web.py -v` and confirm RED**
- [ ] **Step 3: Implement `agent_forge/verifiers/legacy_web.py` (`run_java_tests`, `render_jsp_preview_html`, `compute_visual_diff`, `PlaywrightVisualVerifier`) and update `playwright_stub.py` and `pyproject.toml`**
- [ ] **Step 4: Run `.venv/bin/pytest tests/test_stage2_legacy_web.py -v` and confirm GREEN**

---

### Task 3: Dashboard Visual Regression Viewer & Auto-Verifier Selection (`dashboard/server.py`, `cli.py`, `dashboard/static/*`)

**Files:**
- Modify: `agent_forge/dashboard/server.py`
- Modify: `agent_forge/cli.py`
- Modify: `agent_forge/dashboard/static/index.html`
- Modify: `agent_forge/dashboard/static/styles.css`
- Modify: `agent_forge/dashboard/static/app.js`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add failing tests in `tests/test_dashboard.py` for `/api/runs/<run_id>/artifacts/<attempt>/<kind>` PNG serving, `#visual-regression-panel` UI structure, and zero-emoji compliance**
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_dashboard.py -v` and confirm RED**
- [ ] **Step 3: Implement artifact image endpoint and auto-verifier selection in `server.py` and `cli.py`, and add the Visual Regression Before/After/Diff comparison panel to `index.html`, `styles.css`, and `app.js`**
- [ ] **Step 4: Run full test suite `.venv/bin/pytest -v` and confirm GREEN**

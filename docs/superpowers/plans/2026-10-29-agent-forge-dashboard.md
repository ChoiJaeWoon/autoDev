# Agent Forge Step 1-2: Web Inspector Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task using TDD.

**Goal:** Build the lightweight, developer-grade Web Inspector Dashboard (`forge ui`) for Agent Forge that visualizes SQLite checkpoints (`RunRecord`, `PlanSpec`, `AttemptRecord`, file locks, error fingerprints, reflection notes) and real Git commit unified diffs without a single emoji or AI-template cliché.

**Architecture:** A zero-dependency Python standard-library HTTP server (`ThreadingHTTPServer` in `agent_forge/dashboard/server.py`) reading from `StateStore` and resolving commit diffs via `git show`, paired with a high-contrast dark-slate split-pane static frontend (`agent_forge/dashboard/static/`) and exposed via `forge ui` in `agent_forge/cli.py`.

**Tech Stack:** Python 3.11+ (`http.server`, `urllib.parse`, `json`, `subprocess`), Vanilla HTML5 / CSS3 / ES6 JavaScript (zero emojis, tabular-nums monospace telemetry), Typer CLI, Pytest.

---

### Task 1: Dashboard HTTP Server & JSON API (`agent_forge/dashboard/server.py`)

**Files:**
- Create: `agent_forge/dashboard/__init__.py`
- Create: `agent_forge/dashboard/server.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests for `/api/runs`, `/api/runs/<run_id>` (including git commit diff resolution), and 404 handling**
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_dashboard.py -v` and confirm RED**
- [ ] **Step 3: Implement `agent_forge/dashboard/server.py` with `create_dashboard_server`, `/api/runs`, `/api/runs/<run_id>`, and `_resolve_commit_diff`**
- [ ] **Step 4: Run `.venv/bin/pytest tests/test_dashboard.py -v` and confirm GREEN**

---

### Task 2: Static Inspector UI (`index.html`, `styles.css`, `app.js`) & Zero-Emoji Guard

**Files:**
- Create: `agent_forge/dashboard/static/index.html`
- Create: `agent_forge/dashboard/static/styles.css`
- Create: `agent_forge/dashboard/static/app.js`
- Modify: `pyproject.toml`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add failing tests in `tests/test_dashboard.py` for static asset serving, UI structural IDs, path-traversal prevention, and automated zero-emoji enforcement across all dashboard files**
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_dashboard.py -v` and confirm RED**
- [ ] **Step 3: Implement `index.html`, `styles.css` (GitHub Actions / Linear dark slate `#0d1117` / `#161b22` / `#30363d`), and `app.js` (XSS-safe DOM rendering, FSM pipeline bar, file lock badges, interactive commit diff viewer)**
- [ ] **Step 4: Run `.venv/bin/pytest tests/test_dashboard.py -v` and confirm GREEN**

---

### Task 3: CLI `forge ui` Integration & Live Run Verification

**Files:**
- Modify: `agent_forge/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI tests in `tests/test_cli.py` for `forge ui`**
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_cli.py -v` and confirm RED**
- [ ] **Step 3: Add `ui` command to `agent_forge/cli.py`**
- [ ] **Step 4: Run full test suite `.venv/bin/pytest -v` and verify live `/api/runs/run-af7fa626` output**

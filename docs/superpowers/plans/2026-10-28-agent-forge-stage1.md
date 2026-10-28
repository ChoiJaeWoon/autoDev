# Agent Forge Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Stage 1 Agent Forge autonomous coding harness in Python with an explicit finite-state machine, SQLite checkpointing, anti-cheating file guards, fuzzy `SEARCH/REPLACE` patching, loop/budget brakes, and benchmark target repositories (`python-mini-calc` and `legacy-jsp-app`).

**Architecture:** A pure-Python finite-state machine (`ForgeEngine`) coordinates transitions across `INIT -> PLAN -> WAITING_APPROVAL -> WRITE_TEST -> IMPLEMENT -> VERIFY -> REFLECT / DONE / HALT`, synchronizing Git snapshots and SQLite checkpoints at every step. Pluggable `Verifier` and `LLMClient` interfaces decouple the core loop from test runners (Stage 1 `SubprocessTestVerifier` vs. Stage 2 `PlaywrightVisualVerifier`) and model providers (DeepSeek via OpenAI-compatible SDK).

**Tech Stack:** Python 3.11+, `pydantic>=2.0`, `openai>=1.0`, `typer>=0.12`, `rich>=13.0`, `pytest>=8.0`, `sqlite3` (stdlib), `git` CLI via `subprocess`.

**Spec:** `docs/superpowers/specs/2026-10-28-agent-forge-harness-design.md`

## Global Constraints

- Python 3.11+ with `pydantic` v2 models for all state, configuration, and verification payloads.
- Default safety limits: `max_attempts = 5`, `max_files_changed = 5`, `max_diff_lines = 200`, `max_total_tokens = 100_000`, `max_cost_usd = 0.50`, `test_timeout_seconds = 60`, `consecutive_error_reflect_threshold = 2`.
- Default LLM provider: DeepSeek API (`base_url = "https://api.deepseek.com"`, `model = "deepseek-chat"`) via an OpenAI-compatible interface swappable by config/env vars.
- Zero proprietary/company code in the repository; all target repositories under `targets/` are clean, self-contained sample projects.
- Test files must be physically locked from modification as soon as the run transitions into `IMPLEMENT`.

## Review Focus

- **Untracked rogue file creation bypassing `git diff`:** If a patch or command creates an untracked file not tracked by git, `get_diff_stats` must include untracked files (via `git status --porcelain`) and `discard_working_changes` must remove untracked files (`git clean -fd`) so unwhitelisted new files never slip through. (Pinned in Task 2 & Task 3)
- **Path traversal (`../../etc/passwd` or symlink escape) in `FILE:` headers:** `FileGuard.is_path_allowed` must resolve paths against `repo_root.resolve()` and reject any path that escapes the repository root. (Pinned in Task 3)
- **Ambiguous `SEARCH` block matching multiple occurrences in a file:** When a `SEARCH` block matches 2 or more locations in a target file, `apply_patch_blocks` must reject the patch with an ambiguity error and leave all files unmodified. (Pinned in Task 4)
- **Volatile tokens (timestamps, hex memory addresses, temp paths) in test logs:** `extract_error_fingerprint` must strip volatile substrings (`0x[0-9a-fA-F]+`, execution durations like `0.12s`, `/tmp/...` paths, ISO timestamps) so two identical test failures produce the exact same fingerprint. (Pinned in Task 5)
- **Infinite loop / hang inside the target test process:** `SubprocessTestVerifier` must enforce `timeout_seconds` and return a structured timeout `VerificationResult(passed=False, exit_code=124, error_fingerprint="TIMEOUT")` rather than raising an unhandled `TimeoutExpired`. (Pinned in Task 6)

---

### Task 1: Project Scaffolding, Config, State Models, and SQLite Store

**Files:**
- Create: `pyproject.toml`
- Create: `agent_forge/__init__.py`
- Create: `agent_forge/config.py`
- Create: `agent_forge/core/__init__.py`
- Create: `agent_forge/core/state.py`
- Create: `agent_forge/core/store.py`
- Test: `tests/test_state_and_store.py`

**Interfaces:**
- Produces:
  - `agent_forge.config.ForgeConfig(BaseModel)` with defaults: `max_attempts: int = 5`, `max_files_changed: int = 5`, `max_diff_lines: int = 200`, `max_total_tokens: int = 100_000`, `max_cost_usd: float = 0.50`, `test_timeout_seconds: int = 60`, `consecutive_error_reflect_threshold: int = 2`, `llm_base_url: str = "https://api.deepseek.com"`, `llm_model: str = "deepseek-chat"`, `prompt_price_per_1m: float = 0.27`, `completion_price_per_1m: float = 1.10`
  - `agent_forge.core.state.RunState(str, Enum)`: `INIT`, `PLAN`, `WAITING_APPROVAL`, `WRITE_TEST`, `IMPLEMENT`, `VERIFY`, `REFLECT`, `DONE`, `HALT`
  - `agent_forge.core.state.HaltReason(str, Enum)`: `NONE`, `BUDGET_EXCEEDED`, `MAX_ATTEMPTS`, `OSCILLATION_DETECTED`, `POLICY_VIOLATION`, `PATCH_FAILED`
  - `agent_forge.core.state.PlanSpec(BaseModel)`: `subtasks: list[str]`, `target_source_files: list[str]`, `target_test_files: list[str]`
  - `agent_forge.core.state.AttemptRecord(BaseModel)`: `attempt_number: int`, `phase: RunState`, `tree_hash: str`, `commit_sha: str | None`, `patch_summary: str`, `test_passed: bool | None`, `exit_code: int | None`, `error_fingerprint: str | None`, `prompt_tokens: int`, `completion_tokens: int`, `cost_usd: float`
  - `agent_forge.core.state.RunRecord(BaseModel)`: `run_id: str`, `task_description: str`, `repo_root: str`, `branch_name: str`, `state: RunState`, `halt_reason: HaltReason = HaltReason.NONE`, `plan: PlanSpec | None = None`, `total_tokens: int = 0`, `total_cost_usd: float = 0.0`, `baseline_commit_sha: str | None = None`, `attempts: list[AttemptRecord] = []`
  - `agent_forge.core.store.StateStore`:
    - `__init__(self, db_path: Path)`
    - `save_run(self, run: RunRecord) -> None`
    - `add_attempt(self, run_id: str, attempt: AttemptRecord) -> None`
    - `get_run(self, run_id: str) -> RunRecord | None`
    - `list_runs(self) -> list[RunRecord]`

- [ ] **Step 1: Create `pyproject.toml` and package `__init__.py` files, then write failing tests in `tests/test_state_and_store.py`**

```python
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.state import (
    AttemptRecord,
    HaltReason,
    PlanSpec,
    RunRecord,
    RunState,
)
from agent_forge.core.store import StateStore


def test_default_config_values():
    cfg = ForgeConfig()
    assert cfg.max_attempts == 5
    assert cfg.max_files_changed == 5
    assert cfg.max_diff_lines == 200
    assert cfg.max_total_tokens == 100_000
    assert cfg.max_cost_usd == 0.50
    assert cfg.test_timeout_seconds == 60
    assert cfg.consecutive_error_reflect_threshold == 2
    assert cfg.llm_base_url == "https://api.deepseek.com"
    assert cfg.llm_model == "deepseek-chat"


def test_state_store_roundtrip_run_and_attempts(tmp_path: Path):
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)

    plan = PlanSpec(
        subtasks=["공백 검색어 처리 추가"],
        target_source_files=["src/service.py"],
        target_test_files=["tests/test_service.py"],
    )
    run = RunRecord(
        run_id="run-001",
        task_description="검색어 공백 처리",
        repo_root=str(tmp_path),
        branch_name="forge/run-001",
        state=RunState.IMPLEMENT,
        plan=plan,
        total_tokens=1500,
        total_cost_usd=0.002,
        baseline_commit_sha="abc1234",
    )
    store.save_run(run)

    attempt = AttemptRecord(
        attempt_number=1,
        phase=RunState.IMPLEMENT,
        tree_hash="tree-hash-1",
        commit_sha="commit-1",
        patch_summary="Modified src/service.py",
        test_passed=False,
        exit_code=1,
        error_fingerprint="fp-001",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_usd=0.002,
    )
    store.add_attempt("run-001", attempt)

    loaded = store.get_run("run-001")
    assert loaded is not None
    assert loaded.state == RunState.IMPLEMENT
    assert loaded.plan == plan
    assert len(loaded.attempts) == 1
    assert loaded.attempts[0].error_fingerprint == "fp-001"
    assert len(store.list_runs()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_and_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge'`

- [ ] **Step 3: Implement `ForgeConfig` in `agent_forge/config.py`, state models in `agent_forge/core/state.py`, and `StateStore` in `agent_forge/core/store.py`**

Use `sqlite3` with JSON serialization (`model_dump_json` / `model_validate_json`) for `PlanSpec` and a separate `attempts` table keyed by `(run_id, attempt_number)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_state_and_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git init
git add pyproject.toml agent_forge/ tests/test_state_and_store.py docs/
git commit -m "feat(core): add config, FSM state models, and SQLite checkpoint store"
```

---

### Task 2: Git Isolation, Working-Tree Hashing, Diff Stats, and Atomic Rollback

**Files:**
- Create: `agent_forge/core/git_ops.py`
- Test: `tests/test_git_ops.py`

**Interfaces:**
- Produces:
  - `agent_forge.core.git_ops.DiffStats(BaseModel)`: `changed_files: list[str]`, `added_lines: int`, `deleted_lines: int`, `total_changed_lines: int`
  - `agent_forge.core.git_ops.GitOps`:
    - `__init__(self, repo_root: Path)`
    - `create_task_branch(self, branch_name: str) -> str` (creates/checks out branch and returns current HEAD commit SHA)
    - `get_head_commit(self) -> str`
    - `get_working_tree_hash(self) -> str` (uses a temporary `GIT_INDEX_FILE` to run `git add -A` and `git write-tree` so working tree + untracked files yield an exact git tree SHA without mutating the real index or creating a commit)
    - `create_snapshot(self, message: str) -> str` (stages all changes and creates a snapshot commit, returning the commit SHA; if working tree is clean, returns current HEAD SHA)
    - `get_diff_stats(self, base_ref: str = "HEAD") -> DiffStats` (includes both tracked diff via `git diff --numstat <base_ref>` and untracked files via `git status --porcelain` with line counts)
    - `discard_working_changes(self) -> None` (`git reset --hard HEAD` and `git clean -fd`)
    - `rollback_to_snapshot(self, commit_sha: str) -> None` (`git reset --hard <commit_sha>` and `git clean -fd`)

- [ ] **Step 1: Write the failing tests in `tests/test_git_ops.py`**

```python
import subprocess
from pathlib import Path
from agent_forge.core.git_ops import GitOps


def _init_sample_repo(path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_working_tree_hash_detects_oscillation_and_preserves_index(tmp_path: Path):
    repo = _init_sample_repo(tmp_path)
    git_ops = GitOps(repo)
    base_sha = git_ops.create_task_branch("forge/test-1")
    assert len(base_sha) == 40

    hash_a = git_ops.get_working_tree_hash()
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    hash_b = git_ops.get_working_tree_hash()
    assert hash_a != hash_b

    # Revert back to A -> tree hash must match hash_a (oscillation A -> B -> A)
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    hash_a_again = git_ops.get_working_tree_hash()
    assert hash_a == hash_a_again


def test_diff_stats_includes_untracked_files_and_rollback_cleans_them(tmp_path: Path):
    repo = _init_sample_repo(tmp_path)
    git_ops = GitOps(repo)
    base_sha = git_ops.get_head_commit()

    (repo / "app.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    (repo / "rogue.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    stats = git_ops.get_diff_stats()
    assert set(stats.changed_files) == {"app.py", "rogue.py"}
    assert stats.total_changed_lines >= 4

    git_ops.rollback_to_snapshot(base_sha)
    assert not (repo / "rogue.py").exists()
    assert "return a + b\n" in (repo / "app.py").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_ops.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.core.git_ops'`

- [ ] **Step 3: Implement `DiffStats` and `GitOps` in `agent_forge/core/git_ops.py`**

In `get_working_tree_hash`, copy `.git/index` (if present) to a `tempfile.TemporaryDirectory`, set `env["GIT_INDEX_FILE"]` to that temp file, run `git add -A` and `git write-tree`, and return the stripped stdout.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_git_ops.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_forge/core/git_ops.py tests/test_git_ops.py
git commit -m "feat(git): implement branch isolation, tree hashing, diff stats, and rollback"
```

---

### Task 3: Repo Map Generator and File / Anti-Cheating Guard

**Files:**
- Create: `agent_forge/harness/__init__.py`
- Create: `agent_forge/harness/repo_map.py`
- Create: `agent_forge/harness/file_guard.py`
- Test: `tests/test_repo_map_and_file_guard.py`

**Interfaces:**
- Consumes:
  - `agent_forge.config.ForgeConfig`
  - `agent_forge.core.state.PlanSpec`, `RunState`
  - `agent_forge.core.git_ops.DiffStats`
- Produces:
  - `agent_forge.harness.repo_map.build_repo_map(repo_root: Path, max_files: int = 100) -> str`
  - `agent_forge.harness.file_guard.GuardViolation(Exception)`
  - `agent_forge.harness.file_guard.FileGuard`:
    - `__init__(self, repo_root: Path, config: ForgeConfig, plan: PlanSpec)`
    - `is_test_file(self, rel_path: str) -> bool`
    - `validate_edit_target(self, rel_path: str, phase: RunState) -> Path` (raises `GuardViolation` on path traversal outside `repo_root`, non-whitelisted file, or attempting to edit test files during `IMPLEMENT`/`REFLECT`, or editing source files during `WRITE_TEST`)
    - `validate_test_patch_not_cheating(self, search_text: str, replace_text: str) -> None` (raises `GuardViolation` if `WRITE_TEST` patch reduces the count of `assert` / `assertEquals` / `assertThat` / `assertTrue` statements or adds `@Disabled` / `@pytest.mark.skip`)
    - `validate_diff_stats(self, diff_stats: DiffStats, phase: RunState) -> None` (raises `GuardViolation` if `changed_files` exceeds `config.max_files_changed`, `total_changed_lines` exceeds `config.max_diff_lines`, or any changed file violates `validate_edit_target`)

- [ ] **Step 1: Write the failing tests in `tests/test_repo_map_and_file_guard.py`**

```python
import pytest
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.git_ops import DiffStats
from agent_forge.core.state import PlanSpec, RunState
from agent_forge.harness.file_guard import FileGuard, GuardViolation
from agent_forge.harness.repo_map import build_repo_map


def test_repo_map_extracts_python_and_java_jsp_symbols(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "class Calculator:\n    def add(self, a: int, b: int) -> int:\n        return a + b\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "BoardService.java").write_text(
        "public class BoardService {\n    public List<Post> findPosts(String keyword) { return null; }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "boardList.jsp").write_text(
        '<%@ page contentType="text/html;charset=UTF-8" %>\n<form action="/board/search"></form>\n',
        encoding="utf-8",
    )
    repo_map = build_repo_map(tmp_path)
    assert "src/calc.py" in repo_map
    assert "class Calculator" in repo_map
    assert "def add" in repo_map
    assert "class BoardService" in repo_map
    assert "findPosts" in repo_map
    assert "src/boardList.jsp" in repo_map


def test_file_guard_blocks_path_traversal_and_locks_tests_in_implement(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["기능 구현"],
        target_source_files=["src/calc.py"],
        target_test_files=["tests/test_calc.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)

    # Path traversal blocked
    with pytest.raises(GuardViolation, match="outside repository"):
        guard.validate_edit_target("../../etc/passwd", RunState.IMPLEMENT)

    # WRITE_TEST allows test file, blocks source file
    assert guard.validate_edit_target("tests/test_calc.py", RunState.WRITE_TEST) == (
        tmp_path / "tests/test_calc.py"
    ).resolve()
    with pytest.raises(GuardViolation, match="not allowed in WRITE_TEST"):
        guard.validate_edit_target("src/calc.py", RunState.WRITE_TEST)

    # IMPLEMENT locks test files and allows whitelisted source file
    with pytest.raises(GuardViolation, match="locked"):
        guard.validate_edit_target("tests/test_calc.py", RunState.IMPLEMENT)
    assert guard.validate_edit_target("src/calc.py", RunState.IMPLEMENT) == (
        tmp_path / "src/calc.py"
    ).resolve()


def test_file_guard_blocks_test_cheating_assertion_removal(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["테스트 추가"],
        target_source_files=["src/calc.py"],
        target_test_files=["tests/test_calc.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)

    with pytest.raises(GuardViolation, match="assertion"):
        guard.validate_test_patch_not_cheating(
            search_text="def test_add():\n    assert add(1, 2) == 3\n",
            replace_text="def test_add():\n    pass\n",
        )


def test_file_guard_validates_diff_stats_limits(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["수정"],
        target_source_files=["src/calc.py"],
        target_test_files=["tests/test_calc.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(max_diff_lines=200), plan)

    with pytest.raises(GuardViolation, match="max_diff_lines"):
        guard.validate_diff_stats(
            DiffStats(
                changed_files=["src/calc.py"],
                added_lines=150,
                deleted_lines=60,
                total_changed_lines=210,
            ),
            RunState.IMPLEMENT,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repo_map_and_file_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.harness'`

- [ ] **Step 3: Implement `build_repo_map` in `agent_forge/harness/repo_map.py` and `FileGuard` in `agent_forge/harness/file_guard.py`**

Use Python's `ast` module for `.py` files and regex patterns for `.java` (`(public|protected|private)\s+[\w<>[\],\s]+\s+\w+\s*\([^)]*\)` and `class\s+\w+`) and `.jsp` (`<form[^>]*action=["'][^"']+["']`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_repo_map_and_file_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_forge/harness/ tests/test_repo_map_and_file_guard.py
git commit -m "feat(harness): add repo map generator and anti-cheating file guard"
```

---

### Task 4: Fuzzy `SEARCH / REPLACE` Patcher

**Files:**
- Create: `agent_forge/harness/patcher.py`
- Test: `tests/test_patcher.py`

**Interfaces:**
- Consumes:
  - `agent_forge.core.state.RunState`
  - `agent_forge.harness.file_guard.FileGuard`, `GuardViolation`
- Produces:
  - `agent_forge.harness.patcher.PatchBlock(BaseModel)`: `file_path: str`, `search_block: str`, `replace_block: str`
  - `agent_forge.harness.patcher.PatchError(Exception)`
  - `agent_forge.harness.patcher.PatchResult(BaseModel)`: `applied_files: list[str]`, `blocks_applied: int`
  - `agent_forge.harness.patcher.parse_patch_blocks(llm_output: str) -> list[PatchBlock]`
  - `agent_forge.harness.patcher.apply_patch_blocks(blocks: list[PatchBlock], file_guard: FileGuard, phase: RunState) -> PatchResult`

- [ ] **Step 1: Write the failing tests in `tests/test_patcher.py`**

```python
import pytest
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.state import PlanSpec, RunState
from agent_forge.harness.file_guard import FileGuard
from agent_forge.harness.patcher import (
    PatchError,
    apply_patch_blocks,
    parse_patch_blocks,
)


def test_parse_and_apply_exact_and_fuzzy_indentation_patch(tmp_path: Path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "class Service:\n"
        "    def run(self, x):\n"
        "        if x is None:\n"
        "            return 0\n"
        "        return x + 1\n",
        encoding="utf-8",
    )

    plan = PlanSpec(
        subtasks=["update"],
        target_source_files=["src/service.py"],
        target_test_files=[],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)

    # Notice the SEARCH block has 0 leading indent on `def run` while the file has 4 spaces
    llm_text = (
        "Here is the fix:\n"
        "FILE: src/service.py\n"
        "<<<<<<< SEARCH\n"
        "def run(self, x):\n"
        "    if x is None:\n"
        "        return 0\n"
        "    return x + 1\n"
        "=======\n"
        "def run(self, x):\n"
        "    if x is None:\n"
        "        return -1\n"
        "    return x + 2\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse_patch_blocks(llm_text)
    assert len(blocks) == 1

    res = apply_patch_blocks(blocks, guard, RunState.IMPLEMENT)
    assert res.applied_files == ["src/service.py"]
    updated = target.read_text(encoding="utf-8")
    assert "        return -1\n" in updated
    assert "    return x + 2\n" in updated


def test_ambiguous_search_block_rejected_without_modifying_file(tmp_path: Path):
    target = tmp_path / "src" / "dup.py"
    target.parent.mkdir(parents=True)
    original = "def a():\n    return 1\n\ndef b():\n    return 1\n"
    target.write_text(original, encoding="utf-8")

    plan = PlanSpec(
        subtasks=["dup"],
        target_source_files=["src/dup.py"],
        target_test_files=[],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)
    blocks = parse_patch_blocks(
        "FILE: src/dup.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n"
    )

    with pytest.raises(PatchError, match="ambiguous|multiple"):
        apply_patch_blocks(blocks, guard, RunState.IMPLEMENT)

    assert target.read_text(encoding="utf-8") == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_patcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.harness.patcher'`

- [ ] **Step 3: Implement `parse_patch_blocks` and `apply_patch_blocks` in `agent_forge/harness/patcher.py`**

Implement the 3-tier matcher:
1. Exact substring match (must occur exactly once; if `> 1`, raise `PatchError("Ambiguous match: found multiple occurrences")`).
2. Line-by-line right-stripped (`rstrip()`) match (must match a unique contiguous slice of lines).
3. Relative indentation match: strip common leading whitespace (`textwrap.dedent`) from both candidate window and `search_block`, verify unique match, and re-indent `replace_block` with the matched window's base indentation.
Stage all file writes in memory first and only write to disk after every block in `blocks` succeeds validation and matching.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_patcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_forge/harness/patcher.py tests/test_patcher.py
git commit -m "feat(harness): add 3-tier fuzzy SEARCH/REPLACE patcher"
```

---

### Task 5: Loop Guard (Error Fingerprint & Oscillation) and Budget Guard

**Files:**
- Create: `agent_forge/harness/loop_guard.py`
- Create: `agent_forge/harness/budget_guard.py`
- Test: `tests/test_loop_and_budget_guard.py`

**Interfaces:**
- Consumes:
  - `agent_forge.config.ForgeConfig`
  - `agent_forge.core.state.AttemptRecord`, `HaltReason`, `RunRecord`
- Produces:
  - `agent_forge.harness.loop_guard.LoopAction(str, Enum)`: `CONTINUE`, `REFLECT`, `HALT_OSCILLATION`
  - `agent_forge.harness.loop_guard.extract_error_fingerprint(raw_output: str) -> str`
  - `agent_forge.harness.loop_guard.evaluate_loop_guard(current_fingerprint: str | None, current_tree_hash: str, history: list[AttemptRecord], threshold: int = 2) -> LoopAction`
  - `agent_forge.harness.budget_guard.BudgetGuard`:
    - `__init__(self, config: ForgeConfig)`
    - `check_can_proceed(self, run: RunRecord) -> HaltReason` (returns `HaltReason.NONE`, `HaltReason.BUDGET_EXCEEDED`, or `HaltReason.MAX_ATTEMPTS`)

- [ ] **Step 1: Write the failing tests in `tests/test_loop_and_budget_guard.py`**

```python
from agent_forge.config import ForgeConfig
from agent_forge.core.state import AttemptRecord, HaltReason, RunRecord, RunState
from agent_forge.harness.budget_guard import BudgetGuard
from agent_forge.harness.loop_guard import (
    LoopAction,
    evaluate_loop_guard,
    extract_error_fingerprint,
)


def test_error_fingerprint_ignores_volatile_timestamps_and_addresses():
    log1 = (
        "FAILED tests/test_calc.py::test_div - ZeroDivisionError: division by zero "
        "at <Calculator object at 0x104a8b200> in 0.14s (2026-10-28T22:10:01) /tmp/pytest-of-user/pytest-12/test0"
    )
    log2 = (
        "FAILED tests/test_calc.py::test_div - ZeroDivisionError: division by zero "
        "at <Calculator object at 0x7ff9011a0> in 1.89s (2026-10-28T23:59:59) /tmp/pytest-of-user/pytest-99/test0"
    )
    fp1 = extract_error_fingerprint(log1)
    fp2 = extract_error_fingerprint(log2)
    assert fp1 == fp2


def test_loop_guard_detects_oscillation_and_consecutive_identical_errors():
    history = [
        AttemptRecord(
            attempt_number=1,
            phase=RunState.VERIFY,
            tree_hash="tree-A",
            commit_sha="c1",
            patch_summary="p1",
            test_passed=False,
            exit_code=1,
            error_fingerprint="fp-same",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
        )
    ]

    # Same tree hash as attempt 1 -> HALT_OSCILLATION
    assert (
        evaluate_loop_guard("fp-other", "tree-A", history, threshold=2)
        == LoopAction.HALT_OSCILLATION
    )

    # 2nd consecutive identical error fingerprint -> REFLECT
    assert (
        evaluate_loop_guard("fp-same", "tree-B", history, threshold=2)
        == LoopAction.REFLECT
    )

    # Different fingerprint and new tree hash -> CONTINUE
    assert (
        evaluate_loop_guard("fp-different", "tree-B", history, threshold=2)
        == LoopAction.CONTINUE
    )


def test_budget_guard_enforces_token_cost_and_attempt_caps():
    guard = BudgetGuard(
        ForgeConfig(max_attempts=3, max_total_tokens=1000, max_cost_usd=0.10)
    )
    run = RunRecord(
        run_id="r1",
        task_description="t",
        repo_root=".",
        branch_name="forge/r1",
        state=RunState.IMPLEMENT,
        total_tokens=500,
        total_cost_usd=0.05,
    )
    assert guard.check_can_proceed(run) == HaltReason.NONE

    run.total_tokens = 1001
    assert guard.check_can_proceed(run) == HaltReason.BUDGET_EXCEEDED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_loop_and_budget_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.harness.loop_guard'`

- [ ] **Step 3: Implement `extract_error_fingerprint` and `evaluate_loop_guard` in `agent_forge/harness/loop_guard.py`, and `BudgetGuard` in `agent_forge/harness/budget_guard.py`**

In `extract_error_fingerprint`: normalize input with regexes removing `0x[0-9a-fA-F]+`, `\b\d+\.\d+s\b`, ISO timestamps `\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?`, and `/tmp/[^\s:]+`, filter relevant error/failure lines (`FAILED`, `ERROR`, `Error`, `Exception`, `assert`), and hash with `hashlib.sha256` (first 16 hex chars).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_loop_and_budget_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_forge/harness/loop_guard.py agent_forge/harness/budget_guard.py tests/test_loop_and_budget_guard.py
git commit -m "feat(harness): add error fingerprint reflection, oscillation detector, and budget guard"
```

---

### Task 6: Pluggable Verifier Interface and Subprocess / Docker Test Runner

**Files:**
- Create: `agent_forge/verifiers/__init__.py`
- Create: `agent_forge/verifiers/base.py`
- Create: `agent_forge/verifiers/test_runner.py`
- Create: `agent_forge/verifiers/playwright_stub.py`
- Test: `tests/test_verifiers.py`

**Interfaces:**
- Consumes:
  - `agent_forge.harness.loop_guard.extract_error_fingerprint`
- Produces:
  - `agent_forge.verifiers.base.VerificationResult(BaseModel)`:
    `passed: bool`, `exit_code: int`, `summary: str`, `raw_output: str`, `error_fingerprint: str | None = None`, `artifacts: dict[str, str] = {}`
  - `agent_forge.verifiers.base.Verifier(Protocol)`:
    `def verify(self, repo_root: Path) -> VerificationResult: ...`
  - `agent_forge.verifiers.test_runner.SubprocessTestVerifier`:
    `__init__(self, command: list[str], timeout_seconds: int = 60, docker_image: str | None = None)`
    `verify(self, repo_root: Path) -> VerificationResult`
  - `agent_forge.verifiers.playwright_stub.PlaywrightVisualVerifierStub`:
    Stage 2 plug-in stub implementing `Verifier`

- [ ] **Step 1: Write the failing tests in `tests/test_verifiers.py`**

```python
import sys
import pytest
from pathlib import Path
from agent_forge.verifiers.test_runner import SubprocessTestVerifier


def test_subprocess_verifier_rejects_unwhitelisted_command():
    with pytest.raises(ValueError, match="not in allowed test commands"):
        SubprocessTestVerifier(["rm", "-rf", "/"])


def test_subprocess_verifier_runs_pytest_and_handles_timeout(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"], timeout_seconds=10)
    res = verifier.verify(tmp_path)
    assert res.passed is True
    assert res.exit_code == 0
    assert res.error_fingerprint is None

    # Hang test -> timeout handled gracefully with exit_code 124 and TIMEOUT fingerprint
    (tmp_path / "test_sample.py").write_text(
        "import time\ndef test_hang():\n    time.sleep(5)\n", encoding="utf-8"
    )
    fast_timeout_verifier = SubprocessTestVerifier(
        [sys.executable, "-m", "pytest", "-q"], timeout_seconds=1
    )
    timeout_res = fast_timeout_verifier.verify(tmp_path)
    assert timeout_res.passed is False
    assert timeout_res.exit_code == 124
    assert timeout_res.error_fingerprint == "TIMEOUT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_verifiers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.verifiers'`

- [ ] **Step 3: Implement `VerificationResult`, `Verifier`, `SubprocessTestVerifier`, and `PlaywrightVisualVerifierStub`**

Allow command prefixes matching `pytest`, `<python> -m pytest`, `./gradlew`, `gradle`, `mvn`, `./mvnw`. Catch `subprocess.TimeoutExpired` and return `VerificationResult(passed=False, exit_code=124, summary="Test execution timed out", raw_output=..., error_fingerprint="TIMEOUT")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_verifiers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_forge/verifiers/ tests/test_verifiers.py
git commit -m "feat(verifiers): add pluggable verifier protocol and timeout-safe test runner"
```

---

### Task 7: DeepSeek / OpenAI LLM Client, Stage Prompts, and Core FSM Engine

**Files:**
- Create: `agent_forge/llm/__init__.py`
- Create: `agent_forge/llm/client.py`
- Create: `agent_forge/llm/prompts.py`
- Create: `agent_forge/core/engine.py`
- Test: `tests/test_engine_fsm.py`

**Interfaces:**
- Consumes:
  - `ForgeConfig`, `RunState`, `HaltReason`, `PlanSpec`, `AttemptRecord`, `RunRecord`, `StateStore`
  - `GitOps`, `build_repo_map`, `FileGuard`, `GuardViolation`, `parse_patch_blocks`, `apply_patch_blocks`, `PatchError`, `LoopAction`, `evaluate_loop_guard`, `BudgetGuard`, `Verifier`
- Produces:
  - `agent_forge.llm.client.LLMResponse(BaseModel)`: `content: str`, `prompt_tokens: int`, `completion_tokens: int`, `cost_usd: float`
  - `agent_forge.llm.client.LLMClient(Protocol)`: `def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse: ...`
  - `agent_forge.llm.client.OpenAICompatibleClient`: implements `LLMClient` using `openai.OpenAI(api_key=..., base_url=config.llm_base_url)`
  - `agent_forge.llm.prompts`: `build_plan_prompt`, `build_write_test_prompt`, `build_implement_prompt`, `build_reflect_prompt`
  - `agent_forge.core.engine.ForgeEngine`:
    - `__init__(self, config: ForgeConfig, store: StateStore, llm: LLMClient, verifier: Verifier)`
    - `start_run(self, task_description: str, repo_root: Path, auto_approve: bool = True) -> RunRecord`
    - `resume_run(self, run_id: str, approve: bool = True) -> RunRecord`

- [ ] **Step 1: Write the failing end-to-end FSM tests in `tests/test_engine_fsm.py`**

```python
import sys
import subprocess
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.engine import ForgeEngine
from agent_forge.core.state import HaltReason, RunState
from agent_forge.core.store import StateStore
from agent_forge.llm.client import LLMResponse
from agent_forge.verifiers.test_runner import SubprocessTestVerifier


class ScriptedLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        self.calls.append((system_prompt, user_prompt))
        content = self.responses.pop(0)
        return LLMResponse(
            content=content, prompt_tokens=200, completion_tokens=100, cost_usd=0.001
        )


def _setup_calc_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_engine_reflects_on_repeated_error_then_succeeds(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)
    store = StateStore(tmp_path / "state.db")
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"])

    plan_json = (
        '{"subtasks": ["subtract 함수 추가"], '
        '"target_source_files": ["src/calc.py"], '
        '"target_test_files": ["tests/test_calc.py"]}'
    )
    write_test_patch = (
        "FILE: tests/test_calc.py\n"
        "<<<<<<< SEARCH\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "=======\n"
        "from src.calc import subtract\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n\n"
        "def test_subtract():\n"
        "    assert subtract(5, 3) == 2\n"
        ">>>>>>> REPLACE\n"
    )
    # Attempt 1: wrong formula (a + b)
    bad_impl_1 = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    # Attempt 2: different code (adds comment) so tree_hash differs, but produces SAME AssertionError fingerprint!
    bad_impl_2 = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    # still wrong\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    # Reflection output
    reflection_note = "a + b 대신 a - b를 반환해야 합니다."
    # Attempt 3 (after rollback to post-WRITE_TEST baseline): correct implementation
    good_impl = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a - b\n"
        ">>>>>>> REPLACE\n"
    )

    llm = ScriptedLLM([plan_json, write_test_patch, bad_impl_1, bad_impl_2, reflection_note, good_impl])
    engine = ForgeEngine(ForgeConfig(), store, llm, verifier)
    run = engine.start_run("subtract 함수를 추가해줘", repo, auto_approve=True)

    assert run.state == RunState.DONE
    assert len(run.attempts) == 3
    assert run.attempts[-1].test_passed is True


def test_engine_pauses_at_waiting_approval_and_resumes(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)
    store = StateStore(tmp_path / "state.db")
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"])

    plan_json = (
        '{"subtasks": ["noop"], '
        '"target_source_files": ["src/calc.py"], '
        '"target_test_files": ["tests/test_calc.py"]}'
    )
    write_test_patch = "NO_CHANGES"
    good_impl = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return int(a + b)\n"
        ">>>>>>> REPLACE\n"
    )
    llm = ScriptedLLM([plan_json, write_test_patch, good_impl])
    engine = ForgeEngine(ForgeConfig(), store, llm, verifier)

    paused_run = engine.start_run("add 반환값 int 보장", repo, auto_approve=False)
    assert paused_run.state == RunState.WAITING_APPROVAL

    resumed_run = engine.resume_run(paused_run.run_id, approve=True)
    assert resumed_run.state == RunState.DONE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine_fsm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.core.engine'`

- [ ] **Step 3: Implement `agent_forge/llm/client.py`, `agent_forge/llm/prompts.py`, and `agent_forge/core/engine.py`**

Key FSM transition rules in `ForgeEngine`:
- `INIT -> PLAN`: Generate `repo_map`, request JSON `PlanSpec`, validate `len(target_source_files) <= config.max_files_changed`.
- `PLAN -> WAITING_APPROVAL`: Save run state to SQLite. If `auto_approve=False`, return immediately so Stage 3 / CLI can inspect and call `resume_run`.
- `WAITING_APPROVAL -> WRITE_TEST`: Apply optional test patches (or `"NO_CHANGES"`), validate via `FileGuard` (`RunState.WRITE_TEST`), and create a Git snapshot commit stored in `run.baseline_commit_sha`. From this point on, test files are locked!
- `IMPLEMENT -> VERIFY`: Apply source patches, check `FileGuard.validate_diff_stats`, compute `tree_hash = git_ops.get_working_tree_hash()`. Check `evaluate_loop_guard` for `HALT_OSCILLATION` before even running tests if `tree_hash` was already seen! Otherwise run `verifier.verify(repo_root)`, record `AttemptRecord`, and transition to `DONE`, `REFLECT` (which calls `git_ops.rollback_to_snapshot(run.baseline_commit_sha)` and prompts LLM for reflection strategy), `IMPLEMENT` (retry with test failure log), or `HALT`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine_fsm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_forge/llm/ agent_forge/core/engine.py tests/test_engine_fsm.py
git commit -m "feat(engine): implement FSM core loop, LLM client, and checkpoint resume"
```

---

### Task 8: CLI Entrypoint (`cli.py`) and Target Repositories (`python-mini-calc` & `legacy-jsp-app`)

**Files:**
- Create: `agent_forge/cli.py`
- Create: `targets/python-mini-calc/src/calculator.py`
- Create: `targets/python-mini-calc/tests/test_calculator.py`
- Create: `targets/legacy-jsp-app/pom.xml`
- Create: `targets/legacy-jsp-app/src/main/java/com/example/legacy/BoardService.java`
- Create: `targets/legacy-jsp-app/src/main/java/com/example/legacy/Post.java`
- Create: `targets/legacy-jsp-app/src/main/webapp/WEB-INF/jsp/boardList.jsp`
- Create: `targets/legacy-jsp-app/src/test/java/com/example/legacy/BoardServiceTest.java`
- Create: `targets/legacy-jsp-app/MISSIONS.md`
- Test: `tests/test_cli_and_targets.py`

**Interfaces:**
- Consumes:
  - `ForgeConfig`, `StateStore`, `ForgeEngine`, `OpenAICompatibleClient`, `SubprocessTestVerifier`
- Produces:
  - `agent_forge.cli.app` (`typer.Typer()` with commands: `run`, `status`, `list`, `resume`)

- [ ] **Step 1: Write the failing tests in `tests/test_cli_and_targets.py`**

```python
from pathlib import Path
from typer.testing import CliRunner
from agent_forge.cli import app
from agent_forge.harness.repo_map import build_repo_map

runner = CliRunner()


def test_cli_list_runs_on_empty_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    result = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert result.exit_code == 0


def test_target_repositories_exist_and_parse_in_repo_map():
    root = Path(__file__).resolve().parents[1]
    py_target = root / "targets" / "python-mini-calc"
    jsp_target = root / "targets" / "legacy-jsp-app"

    assert (py_target / "src" / "calculator.py").exists()
    assert (jsp_target / "src" / "main" / "java" / "com" / "example" / "legacy" / "BoardService.java").exists()
    assert (jsp_target / "src" / "main" / "webapp" / "WEB-INF" / "jsp" / "boardList.jsp").exists()
    assert (jsp_target / "MISSIONS.md").exists()

    jsp_map = build_repo_map(jsp_target)
    assert "BoardService" in jsp_map
    assert "boardList.jsp" in jsp_map
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_and_targets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_forge.cli'`

- [ ] **Step 3: Implement `agent_forge/cli.py` and create `targets/python-mini-calc` and `targets/legacy-jsp-app`**

In `agent_forge/cli.py`, provide `run`, `list`, `status`, and `resume` commands using `typer` and `rich.table.Table`. Populate `targets/python-mini-calc` and `targets/legacy-jsp-app` (including `MISSIONS.md` documenting Stage 1 JUnit missions and Stage 2 JSP/Playwright visual maintenance missions).

- [ ] **Step 4: Run full test suite to verify everything passes**

Run: `pytest -v`
Expected: PASS (all test files green)

- [ ] **Step 5: Commit**

```bash
git add agent_forge/cli.py targets/ tests/test_cli_and_targets.py
git commit -m "feat(cli,targets): add Typer CLI and python/legacy-jsp benchmark targets"
```

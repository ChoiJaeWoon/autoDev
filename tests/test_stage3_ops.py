"""Tests for Stage 3: GitHub Issue Intake, Checkpoint Retry, and PR Draft/Publish."""

import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from agent_forge.config import ForgeConfig
from agent_forge.core.engine import ForgeEngine
from agent_forge.core.state import AttemptRecord, HaltReason, PlanSpec, RunRecord, RunState
from agent_forge.core.store import StateStore
from agent_forge.integrations.github_ops import (
    build_pr_draft_markdown,
    detect_github_repo,
    fetch_github_issue,
    format_issue_as_task,
    publish_github_pr,
)
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
            content=content, prompt_tokens=150, completion_tokens=80, cost_usd=0.0005
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
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/ChoiJaeWoon/autoDev.git"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_state_and_store_stage3_fields_and_migration(tmp_path: Path):
    db_path = tmp_path / "legacy_state.db"
    # Create legacy Stage 1/2 runs table without Stage 3 columns
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            task_description TEXT NOT NULL,
            repo_root TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            state TEXT NOT NULL,
            halt_reason TEXT NOT NULL,
            plan_json TEXT,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            total_cost_usd REAL NOT NULL DEFAULT 0.0,
            baseline_commit_sha TEXT,
            reflection_notes_json TEXT NOT NULL DEFAULT '[]',
            last_error_output TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    run = RunRecord(
        run_id="run-stage3",
        task_description="[GitHub Issue #42] 나누기 함수 추가",
        repo_root="/tmp/repo",
        branch_name="forge/run-stage3",
        state=RunState.DONE,
        issue_number=42,
        issue_url="https://github.com/ChoiJaeWoon/autoDev/issues/42",
        pr_draft_markdown="## 작업 요약\n- Closes #42",
        pr_url="https://github.com/ChoiJaeWoon/autoDev/pull/43",
    )
    store.save_run(run)

    loaded = store.get_run("run-stage3")
    assert loaded is not None
    assert loaded.issue_number == 42
    assert loaded.issue_url == "https://github.com/ChoiJaeWoon/autoDev/issues/42"
    assert "Closes #42" in loaded.pr_draft_markdown
    assert loaded.pr_url == "https://github.com/ChoiJaeWoon/autoDev/pull/43"


def test_github_ops_issue_formatting_and_pr_draft_builder(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)
    assert detect_github_repo(repo) == "ChoiJaeWoon/autoDev"

    issue_data = {
        "number": 15,
        "title": "게시판 조회수 컬럼 및 검색어 공백 제거",
        "body": "boardList.jsp에 조회수 컬럼을 추가하고 검색어를 trim 처리해주세요.",
        "html_url": "https://github.com/ChoiJaeWoon/autoDev/issues/15",
        "state": "open",
    }
    task_text = format_issue_as_task(issue_data)
    assert "[GitHub Issue #15]" in task_text
    assert "게시판 조회수 컬럼 및 검색어 공백 제거" in task_text
    assert "boardList.jsp에 조회수 컬럼을 추가하고" in task_text

    run = RunRecord(
        run_id="run-prtest01",
        task_description=task_text,
        repo_root=str(repo),
        branch_name="forge/run-prtest01",
        state=RunState.DONE,
        plan=PlanSpec(
            subtasks=["BoardService trim 추가", "boardList.jsp 조회수 헤더 추가"],
            target_source_files=["src/main/webapp/WEB-INF/views/boardList.jsp"],
            target_test_files=["src/test/java/com/legacy/board/BoardServiceTest.java"],
        ),
        total_tokens=1850,
        total_cost_usd=0.0024,
        baseline_commit_sha="111122223333",
        issue_number=15,
        issue_url="https://github.com/ChoiJaeWoon/autoDev/issues/15",
        attempts=[
            AttemptRecord(
                attempt_number=1,
                phase=RunState.VERIFY,
                tree_hash="abcd1234",
                commit_sha="999988887777",
                patch_summary="Applied 1 block(s)",
                test_passed=True,
                exit_code=0,
                artifacts={
                    "baseline_png": "/tmp/baseline.png",
                    "after_png": "/tmp/attempt-1-after.png",
                    "diff_png": "/tmp/attempt-1-diff.png",
                    "diff_ratio": "0.0142",
                },
            )
        ],
    )
    md = build_pr_draft_markdown(run)
    assert "Closes #15" in md
    assert "run-prtest01" in md
    assert "BoardService trim 추가" in md
    assert "11112222" in md
    assert "99998888" in md
    assert "1.42%" in md


def test_github_ops_fetch_issue_and_publish_pr_mocked(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)

    class DummyResponse(io.BytesIO):
        def __init__(self, payload: dict):
            super().__init__(json.dumps(payload).encode("utf-8"))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = DummyResponse(
            {
                "number": 9,
                "title": "modulo 연산 추가",
                "body": "a % b 연산을 지원하고 0으로 나누면 ValueError 발생",
                "html_url": "https://github.com/ChoiJaeWoon/autoDev/issues/9",
                "state": "open",
            }
        )
        issue = fetch_github_issue("ChoiJaeWoon/autoDev", 9, token="ghp_test")
        assert issue["number"] == 9
        assert issue["title"] == "modulo 연산 추가"

    run = RunRecord(
        run_id="run-pub01",
        task_description="[GitHub Issue #9] modulo 연산 추가",
        repo_root=str(repo),
        branch_name="forge/run-pub01",
        state=RunState.DONE,
        issue_number=9,
        issue_url="https://github.com/ChoiJaeWoon/autoDev/issues/9",
        pr_draft_markdown="## 작업 요약\nCloses #9",
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = DummyResponse(
            {
                "number": 10,
                "html_url": "https://github.com/ChoiJaeWoon/autoDev/pull/10",
            }
        )
        pr_url = publish_github_pr(
            run,
            repo_slug="ChoiJaeWoon/autoDev",
            token="ghp_test",
            base_branch="main",
            draft=True,
            git_push=False,
        )
        assert pr_url == "https://github.com/ChoiJaeWoon/autoDev/pull/10"


def test_engine_retry_from_checkpoint_skips_plan_and_write_test(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)
    store = StateStore(tmp_path / "state.db")
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"])

    plan_json = (
        '{"subtasks": ["multiply 함수 추가"], '
        '"target_source_files": ["src/calc.py"], '
        '"target_test_files": ["tests/test_calc.py"]}'
    )
    write_test_patch = (
        "FILE: tests/test_calc.py\n"
        "<<<<<<< SEARCH\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "=======\n"
        "from src.calc import multiply\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n\n"
        "def test_multiply():\n"
        "    assert multiply(3, 4) == 12\n"
        ">>>>>>> REPLACE\n"
    )
    bad_impl = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )

    # First run with max_attempts=1 halts after attempt 1 fails
    llm = ScriptedLLM([plan_json, write_test_patch, bad_impl])
    cfg = ForgeConfig(max_attempts=1)
    engine = ForgeEngine(cfg, store, llm, verifier)
    halted_run = engine.start_run(
        "[GitHub Issue #21] multiply 함수를 추가해줘",
        repo,
        auto_approve=True,
        issue_number=21,
        issue_url="https://github.com/ChoiJaeWoon/autoDev/issues/21",
    )

    assert halted_run.state == RunState.HALT
    assert halted_run.halt_reason == HaltReason.MAX_ATTEMPTS
    assert halted_run.baseline_commit_sha is not None
    assert len(halted_run.attempts) == 1
    assert len(llm.calls) == 3  # PLAN, WRITE_TEST, IMPLEMENT #1

    # Now provide ONLY the fixed IMPLEMENT patch (no PLAN, no WRITE_TEST!)
    good_impl = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n"
        ">>>>>>> REPLACE\n"
    )
    llm.responses.append(good_impl)

    retried_run = engine.retry_from_checkpoint(halted_run.run_id, extra_attempts=2)
    assert retried_run.state == RunState.DONE
    assert retried_run.halt_reason == HaltReason.NONE
    assert len(retried_run.attempts) == 2
    assert retried_run.attempts[-1].test_passed is True
    assert len(llm.calls) == 4  # Only 1 additional LLM call (IMPLEMENT #2)!
    assert "Closes #21" in retried_run.pr_draft_markdown

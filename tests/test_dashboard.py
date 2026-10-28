"""Tests for the Agent Forge Web Inspector Dashboard server and static UI."""

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
import pytest

from agent_forge.core.state import (
    AttemptRecord,
    HaltReason,
    PlanSpec,
    RunRecord,
    RunState,
)
from agent_forge.core.store import StateStore
from agent_forge.dashboard.server import create_dashboard_server


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


@pytest.fixture
def sample_dashboard_env(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    calc_py = repo / "calc.py"
    test_py = repo / "test_calc.py"
    calc_py.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    test_py.write_text("from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    # Baseline test lock commit
    test_py.write_text(
        "from calc import add, sub\ndef test_add():\n    assert add(1, 2) == 3\ndef test_sub():\n    assert sub(5, 3) == 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_calc.py")
    _git(repo, "commit", "-m", "lock test suite")
    baseline_sha = _git(repo, "rev-parse", "HEAD")

    # Attempt 1 commit
    calc_py.write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    _git(repo, "add", "calc.py")
    _git(repo, "commit", "-m", "implement sub")
    attempt_sha = _git(repo, "rev-parse", "HEAD")

    db_path = tmp_path / "state.db"
    store = StateStore(db_path)

    run1 = RunRecord(
        run_id="run-001",
        task_description="첫 번째 테스트 미션",
        repo_root=str(repo),
        branch_name="forge/run-001",
        state=RunState.HALT,
        halt_reason=HaltReason.OSCILLATION_DETECTED,
        total_tokens=1200,
        total_cost_usd=0.0009,
        reflection_notes=["동일 에러 2회 발생으로 전략 수정"],
        last_error_output="AssertionError: expected 2 got 0",
    )
    store.save_run(run1)

    run2 = RunRecord(
        run_id="run-002",
        task_description="sub(a, b) 함수 추가 및 검증",
        repo_root=str(repo),
        branch_name="forge/run-002",
        state=RunState.DONE,
        halt_reason=HaltReason.NONE,
        plan=PlanSpec(
            subtasks=["sub 함수 구현", "단위 테스트 검증"],
            target_source_files=["calc.py"],
            target_test_files=["test_calc.py"],
        ),
        total_tokens=850,
        total_cost_usd=0.0006,
        baseline_commit_sha=baseline_sha,
        attempts=[
            AttemptRecord(
                attempt_number=1,
                phase=RunState.VERIFY,
                tree_hash="abcdef1234567890",
                commit_sha=attempt_sha,
                patch_summary="Applied 1 block(s) to ['calc.py']",
                test_passed=True,
                exit_code=0,
                prompt_tokens=450,
                completion_tokens=400,
                cost_usd=0.0006,
            )
        ],
    )
    store.save_run(run2)

    server = create_dashboard_server(db_path=db_path, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{port}", db_path
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_api_list_and_get_run_with_git_diffs(sample_dashboard_env):
    base_url, _ = sample_dashboard_env

    # 1. List runs (newest first)
    with urllib.request.urlopen(f"{base_url}/api/runs") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
    assert len(data["runs"]) == 2
    assert data["runs"][0]["run_id"] == "run-002"
    assert data["runs"][0]["attempt_count"] == 1
    assert data["runs"][1]["run_id"] == "run-001"
    assert data["runs"][1]["state"] == "HALT"

    # 2. Get specific run with git diffs
    with urllib.request.urlopen(f"{base_url}/api/runs/run-002") as resp:
        assert resp.status == 200
        detail = json.loads(resp.read().decode("utf-8"))
    assert detail["run_id"] == "run-002"
    assert "+def test_sub():" in detail["baseline_diff"]
    assert "+def sub(a, b):" in detail["attempt_diffs"]["1"]

    # 3. 404 on unknown run_id
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/runs/run-missing")
    assert exc_info.value.code == 404

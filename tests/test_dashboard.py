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

    # Create dummy screenshot files for run-002 attempt 1
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(exist_ok=True)
    for name in ("baseline.png", "after.png", "diff.png"):
        (art_dir / name).write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

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
                artifacts={
                    "baseline_screenshot": str(art_dir / "baseline.png"),
                    "after_screenshot": str(art_dir / "after.png"),
                    "diff_screenshot": str(art_dir / "diff.png"),
                    "diff_ratio": "0.0315",
                    "dom_status": "PASS",
                },
            )
        ],
    )
    store.save_run(run2)

    server = create_dashboard_server(
        db_path=db_path,
        host="127.0.0.1",
        port=0,
        workspace_root=tmp_path,
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{port}", db_path, repo
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_api_list_and_get_run_with_git_diffs(sample_dashboard_env):
    base_url, _, _ = sample_dashboard_env

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

    # 4. Serve visual regression screenshot artifacts (baseline / after / diff)
    for kind in ("baseline", "after", "diff"):
        with urllib.request.urlopen(
            f"{base_url}/api/runs/run-002/artifacts/1/{kind}"
        ) as resp:
            assert resp.status == 200
            assert "image/png" in resp.headers.get("Content-Type", "")
            assert resp.read().startswith(b"\x89PNG")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/runs/run-002/artifacts/1/unknown")
    assert exc_info.value.code == 404


def test_dashboard_post_dispatch_run_and_resume_approval(tmp_path: Path):
    import time

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    db_path = tmp_path / "state.db"
    store = StateStore(db_path)

    class FakeEngine:
        def __init__(self, store_ref: StateStore):
            self.store = store_ref

        def start_run(
            self,
            task_description: str,
            repo_root: Path,
            auto_approve: bool = True,
            run_id: str | None = None,
        ) -> RunRecord:
            rec = RunRecord(
                run_id=run_id or "run-fake",
                task_description=task_description,
                repo_root=str(repo_root),
                branch_name=f"forge/{run_id}",
                state=RunState.DONE if auto_approve else RunState.WAITING_APPROVAL,
                plan=PlanSpec(
                    subtasks=["계획 1"],
                    target_source_files=["calc.py"],
                    target_test_files=[],
                ),
            )
            self.store.save_run(rec)
            return rec

        def resume_run(self, run_id: str, approve: bool = True) -> RunRecord:
            rec = self.store.get_run(run_id)
            assert rec is not None
            rec.state = RunState.DONE if approve else RunState.HALT
            if not approve:
                rec.halt_reason = HaltReason.POLICY_VIOLATION
            self.store.save_run(rec)
            return rec

    server = create_dashboard_server(
        db_path=db_path,
        host="127.0.0.1",
        port=0,
        workspace_root=tmp_path,
        engine_factory=lambda _db, _cmd: FakeEngine(store),
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. Reject empty task
        req_empty = urllib.request.Request(
            f"{base_url}/api/runs",
            data=json.dumps({"task": "   ", "repo_root": "repo"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_empty)
        assert exc_info.value.code == 400

        # 2. Reject path traversal outside workspace_root
        req_traversal = urllib.request.Request(
            f"{base_url}/api/runs",
            data=json.dumps({"task": "테스트 미션", "repo_root": "../outside"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_traversal)
        assert exc_info.value.code == 400

        # 3. Dispatch run with auto_approve=False -> pauses at WAITING_APPROVAL
        req_run = urllib.request.Request(
            f"{base_url}/api/runs",
            data=json.dumps(
                {
                    "task": "multiply 메서드 추가해 줘",
                    "repo_root": "repo",
                    "auto_approve": False,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_run) as resp:
            assert resp.status == 202
            created = json.loads(resp.read().decode("utf-8"))
        run_id = created["run_id"]

        # Wait briefly for background thread
        for _ in range(20):
            r = store.get_run(run_id)
            if r and r.state == RunState.WAITING_APPROVAL:
                break
            time.sleep(0.02)

        paused = store.get_run(run_id)
        assert paused is not None
        assert paused.state == RunState.WAITING_APPROVAL

        # 4. Resume run via POST /api/runs/<run_id>/resume
        req_resume = urllib.request.Request(
            f"{base_url}/api/runs/{run_id}/resume",
            data=json.dumps({"approve": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_resume) as resp:
            assert resp.status == 202

        for _ in range(20):
            r = store.get_run(run_id)
            if r and r.state == RunState.DONE:
                break
            time.sleep(0.02)

        finished = store.get_run(run_id)
        assert finished is not None
        assert finished.state == RunState.DONE
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_static_assets_and_path_traversal_guard(sample_dashboard_env):
    base_url, _, _ = sample_dashboard_env

    # Root HTML
    with urllib.request.urlopen(f"{base_url}/") as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
    for required_id in (
        'id="mission-form"',
        'id="mission-input"',
        'id="run-list"',
        'id="fsm-pipeline"',
        'id="plan-scope"',
        'id="attempts-table"',
        'id="diff-viewer"',
        'id="approval-banner"',
        'id="visual-regression-panel"',
        'id="vis-img-baseline"',
        'id="vis-img-after"',
        'id="vis-img-diff"',
    ):
        assert required_id in html

    # CSS: verify light-tone palette, soft curves, and tabular-nums
    with urllib.request.urlopen(f"{base_url}/static/styles.css") as resp:
        assert resp.status == 200
        css = resp.read().decode("utf-8")
    assert "#f8fafc" in css or "#f6f8fa" in css
    assert "#ffffff" in css
    assert "16px" in css
    assert "tabular-nums" in css

    # JS
    with urllib.request.urlopen(f"{base_url}/static/app.js") as resp:
        assert resp.status == 200
        js = resp.read().decode("utf-8")
    assert "LOCKED IN IMPLEMENT" in js
    assert "textContent" in js

    # Path traversal blocked
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/static/../../pyproject.toml")
    assert exc_info.value.code == 404


def test_dashboard_files_contain_zero_emojis():
    import re

    emoji_pattern = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
    dashboard_dir = Path(__file__).resolve().parent.parent / "agent_forge" / "dashboard"
    files_to_check = [
        dashboard_dir / "server.py",
        dashboard_dir / "static" / "index.html",
        dashboard_dir / "static" / "styles.css",
        dashboard_dir / "static" / "app.js",
    ]
    for file_path in files_to_check:
        assert file_path.is_file(), f"Missing file: {file_path}"
        content = file_path.read_text(encoding="utf-8")
        matches = emoji_pattern.findall(content)
        assert not matches, f"Emoji found in {file_path.name}: {matches}"


def test_cli_ui_command_invokes_dashboard_server(monkeypatch, tmp_path: Path):
    from typer.testing import CliRunner
    import agent_forge.cli as cli_module

    called = {}

    def fake_start_dashboard_server(db_path: Path, host: str, port: int) -> None:
        called["db_path"] = db_path
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli_module, "start_dashboard_server", fake_start_dashboard_server, raising=False)
    runner = CliRunner()
    db_file = tmp_path / "custom.db"
    result = runner.invoke(
        cli_module.app,
        ["ui", "--port", "9123", "--host", "127.0.0.1", "--db-path", str(db_file)],
    )
    assert result.exit_code == 0, result.output
    assert called["port"] == 9123
    assert called["host"] == "127.0.0.1"
    assert called["db_path"] == db_file
    assert "http://127.0.0.1:9123" in result.output



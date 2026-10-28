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
        artifacts={
            "baseline_screenshot": "/tmp/baseline.png",
            "after_screenshot": "/tmp/after.png",
            "diff_ratio": "0.042",
        },
    )
    store.add_attempt("run-001", attempt)

    loaded = store.get_run("run-001")
    assert loaded is not None
    assert loaded.state == RunState.IMPLEMENT
    assert loaded.plan == plan
    assert len(loaded.attempts) == 1
    assert loaded.attempts[0].error_fingerprint == "fp-001"
    assert loaded.attempts[0].artifacts["diff_ratio"] == "0.042"
    assert len(store.list_runs()) == 1

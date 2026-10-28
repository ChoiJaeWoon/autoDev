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

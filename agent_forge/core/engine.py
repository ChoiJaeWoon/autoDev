"""Core Finite-State Machine (FSM) execution engine for Agent Forge."""

import json
import re
import uuid
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.git_ops import GitOps
from agent_forge.core.state import (
    AttemptRecord,
    HaltReason,
    PlanSpec,
    RunRecord,
    RunState,
)
from agent_forge.core.store import StateStore
from agent_forge.harness.budget_guard import BudgetGuard
from agent_forge.harness.file_guard import FileGuard, GuardViolation
from agent_forge.harness.loop_guard import LoopAction, evaluate_loop_guard
from agent_forge.harness.patcher import PatchError, apply_patch_blocks, parse_patch_blocks
from agent_forge.harness.repo_map import build_repo_map
from agent_forge.llm.client import LLMClient, LLMResponse
from agent_forge.llm.prompts import (
    build_implement_prompt,
    build_plan_prompt,
    build_reflect_prompt,
    build_write_test_prompt,
)
from agent_forge.verifiers.base import Verifier

JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_plan_json(raw_text: str) -> PlanSpec:
    match = JSON_BLOCK_RE.search(raw_text)
    if not match:
        raise ValueError(f"Could not find JSON object in PLAN response: {raw_text}")
    data = json.loads(match.group(0))
    return PlanSpec.model_validate(data)


class ForgeEngine:
    """Coordinates the autonomous PLAN -> WRITE_TEST -> IMPLEMENT -> VERIFY -> REFLECT loop."""

    def __init__(
        self,
        config: ForgeConfig,
        store: StateStore,
        llm: LLMClient,
        verifier: Verifier,
    ):
        self.config = config
        self.store = store
        self.llm = llm
        self.verifier = verifier
        self.budget_guard = BudgetGuard(config)

    def _record_llm_usage(self, run: RunRecord, resp: LLMResponse) -> None:
        run.total_tokens += resp.prompt_tokens + resp.completion_tokens
        run.total_cost_usd = round(run.total_cost_usd + resp.cost_usd, 6)

    def _halt(self, run: RunRecord, reason: HaltReason, message: str = "") -> RunRecord:
        run.state = RunState.HALT
        run.halt_reason = reason
        if message:
            run.last_error_output = message
        self.store.save_run(run)
        return run

    def start_run(
        self,
        task_description: str,
        repo_root: Path,
        auto_approve: bool = True,
        run_id: str | None = None,
    ) -> RunRecord:
        resolved_root = Path(repo_root).resolve()
        rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
        branch_name = f"forge/{rid}"

        git_ops = GitOps(resolved_root)
        base_sha = git_ops.create_task_branch(branch_name)

        run = RunRecord(
            run_id=rid,
            task_description=task_description,
            repo_root=str(resolved_root),
            branch_name=branch_name,
            state=RunState.INIT,
            baseline_commit_sha=base_sha,
        )
        self.store.save_run(run)

        # Transition INIT -> PLAN
        run.state = RunState.PLAN
        self.store.save_run(run)

        repo_map = build_repo_map(resolved_root)
        sys_p, usr_p = build_plan_prompt(
            task_description, repo_map, self.config.max_files_changed
        )
        plan_resp = self.llm.complete(sys_p, usr_p)
        self._record_llm_usage(run, plan_resp)

        try:
            plan = _parse_plan_json(plan_resp.content)
        except Exception as exc:
            return self._halt(run, HaltReason.POLICY_VIOLATION, f"Invalid plan JSON: {exc}")

        if len(plan.target_source_files) > self.config.max_files_changed:
            return self._halt(
                run,
                HaltReason.POLICY_VIOLATION,
                f"Plan requested {len(plan.target_source_files)} source files, exceeding max_files_changed={self.config.max_files_changed}",
            )

        run.plan = plan
        run.state = RunState.WAITING_APPROVAL
        self.store.save_run(run)

        if not auto_approve:
            return run

        return self._continue_from_approval(run)

    def resume_run(self, run_id: str, approve: bool = True) -> RunRecord:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' not found in StateStore")

        if run.state == RunState.WAITING_APPROVAL:
            if not approve:
                return self._halt(run, HaltReason.POLICY_VIOLATION, "User rejected plan")
            return self._continue_from_approval(run)

        if run.state in {RunState.IMPLEMENT, RunState.REFLECT}:
            return self._execute_implement_verify_loop(run)

        return run

    def _continue_from_approval(self, run: RunRecord) -> RunRecord:
        assert run.plan is not None
        repo_root = Path(run.repo_root)
        git_ops = GitOps(repo_root)
        file_guard = FileGuard(repo_root, self.config, run.plan)

        # Transition WAITING_APPROVAL -> WRITE_TEST
        run.state = RunState.WRITE_TEST
        self.store.save_run(run)

        if run.plan.target_test_files:
            budget_check = self.budget_guard.check_can_proceed(run)
            if budget_check != HaltReason.NONE:
                return self._halt(run, budget_check)

            sys_p, usr_p = build_write_test_prompt(
                run.task_description, run.plan, repo_root
            )
            test_resp = self.llm.complete(sys_p, usr_p)
            self._record_llm_usage(run, test_resp)

            if "NO_CHANGES" not in test_resp.content:
                blocks = parse_patch_blocks(test_resp.content)
                if blocks:
                    try:
                        apply_patch_blocks(blocks, file_guard, RunState.WRITE_TEST)
                        diff_stats = git_ops.get_diff_stats()
                        file_guard.validate_diff_stats(diff_stats, RunState.WRITE_TEST)
                    except (GuardViolation, PatchError) as exc:
                        git_ops.discard_working_changes()
                        return self._halt(run, HaltReason.POLICY_VIOLATION, str(exc))

        # Snapshot baseline commit after WRITE_TEST so test files are committed & locked
        run.baseline_commit_sha = git_ops.create_snapshot(
            f"chore(forge): lock test suite for {run.run_id}"
        )
        run.state = RunState.IMPLEMENT
        self.store.save_run(run)

        return self._execute_implement_verify_loop(run)

    def _execute_implement_verify_loop(self, run: RunRecord) -> RunRecord:
        assert run.plan is not None
        repo_root = Path(run.repo_root)
        git_ops = GitOps(repo_root)
        file_guard = FileGuard(repo_root, self.config, run.plan)

        while run.state in {RunState.IMPLEMENT, RunState.REFLECT}:
            budget_check = self.budget_guard.check_can_proceed(run)
            if budget_check != HaltReason.NONE:
                return self._halt(run, budget_check)

            if run.state == RunState.REFLECT:
                if run.baseline_commit_sha:
                    git_ops.rollback_to_snapshot(run.baseline_commit_sha)
                sys_p, usr_p = build_reflect_prompt(
                    run.task_description, run.plan, run.last_error_output
                )
                reflect_resp = self.llm.complete(sys_p, usr_p)
                self._record_llm_usage(run, reflect_resp)
                run.reflection_notes.append(reflect_resp.content.strip())
                run.state = RunState.IMPLEMENT
                self.store.save_run(run)

                budget_check = self.budget_guard.check_can_proceed(run)
                if budget_check != HaltReason.NONE:
                    return self._halt(run, budget_check)

            # IMPLEMENT step
            sys_p, usr_p = build_implement_prompt(
                run.task_description,
                run.plan,
                repo_root,
                last_error_output=run.last_error_output,
                reflection_notes=run.reflection_notes,
            )
            impl_resp = self.llm.complete(sys_p, usr_p)
            self._record_llm_usage(run, impl_resp)

            blocks = parse_patch_blocks(impl_resp.content)
            if not blocks:
                run.last_error_output = "No valid FILE / SEARCH / REPLACE patch blocks found in response."
                attempt = AttemptRecord(
                    attempt_number=len(run.attempts) + 1,
                    phase=RunState.IMPLEMENT,
                    tree_hash="",
                    commit_sha=git_ops.get_head_commit(),
                    patch_summary="No patch blocks parsed",
                    test_passed=False,
                    exit_code=-1,
                    error_fingerprint="PATCH_PARSE_ERROR",
                    prompt_tokens=impl_resp.prompt_tokens,
                    completion_tokens=impl_resp.completion_tokens,
                    cost_usd=impl_resp.cost_usd,
                )
                run.attempts.append(attempt)
                self.store.add_attempt(run.run_id, attempt)
                self.store.save_run(run)
                continue

            try:
                patch_res = apply_patch_blocks(blocks, file_guard, RunState.IMPLEMENT)
                diff_stats = git_ops.get_diff_stats(
                    base_ref=run.baseline_commit_sha or "HEAD"
                )
                file_guard.validate_diff_stats(diff_stats, RunState.IMPLEMENT)
            except GuardViolation as exc:
                git_ops.discard_working_changes()
                return self._halt(run, HaltReason.POLICY_VIOLATION, str(exc))
            except PatchError as exc:
                git_ops.discard_working_changes()
                run.last_error_output = f"Patch application error: {exc}"
                attempt = AttemptRecord(
                    attempt_number=len(run.attempts) + 1,
                    phase=RunState.IMPLEMENT,
                    tree_hash="",
                    commit_sha=git_ops.get_head_commit(),
                    patch_summary=f"PatchError: {exc}",
                    test_passed=False,
                    exit_code=-1,
                    error_fingerprint="PATCH_APPLY_ERROR",
                    prompt_tokens=impl_resp.prompt_tokens,
                    completion_tokens=impl_resp.completion_tokens,
                    cost_usd=impl_resp.cost_usd,
                )
                run.attempts.append(attempt)
                self.store.add_attempt(run.run_id, attempt)
                self.store.save_run(run)
                continue

            tree_hash = git_ops.get_working_tree_hash()

            # Check oscillation BEFORE running tests if this exact working tree was already tried
            pre_loop_check = evaluate_loop_guard(
                None,
                tree_hash,
                run.attempts,
                threshold=self.config.consecutive_error_reflect_threshold,
            )
            if pre_loop_check == LoopAction.HALT_OSCILLATION:
                attempt = AttemptRecord(
                    attempt_number=len(run.attempts) + 1,
                    phase=RunState.IMPLEMENT,
                    tree_hash=tree_hash,
                    commit_sha=git_ops.get_head_commit(),
                    patch_summary=f"Oscillation detected on files {patch_res.applied_files}",
                    test_passed=False,
                    exit_code=-1,
                    error_fingerprint="OSCILLATION",
                    prompt_tokens=impl_resp.prompt_tokens,
                    completion_tokens=impl_resp.completion_tokens,
                    cost_usd=impl_resp.cost_usd,
                )
                run.attempts.append(attempt)
                self.store.add_attempt(run.run_id, attempt)
                return self._halt(
                    run,
                    HaltReason.OSCILLATION_DETECTED,
                    f"Working tree hash {tree_hash} matches a previous failed attempt",
                )

            # Transition IMPLEMENT -> VERIFY
            run.state = RunState.VERIFY
            self.store.save_run(run)

            verify_res = self.verifier.verify(repo_root)
            snap_sha = git_ops.create_snapshot(
                f"chore(forge): attempt {len(run.attempts) + 1} ({'PASS' if verify_res.passed else 'FAIL'})"
            )

            attempt = AttemptRecord(
                attempt_number=len(run.attempts) + 1,
                phase=RunState.VERIFY,
                tree_hash=tree_hash,
                commit_sha=snap_sha,
                patch_summary=f"Applied {patch_res.blocks_applied} block(s) to {patch_res.applied_files}",
                test_passed=verify_res.passed,
                exit_code=verify_res.exit_code,
                error_fingerprint=verify_res.error_fingerprint,
                prompt_tokens=impl_resp.prompt_tokens,
                completion_tokens=impl_resp.completion_tokens,
                cost_usd=impl_resp.cost_usd,
            )
            prior_attempts = list(run.attempts)
            run.attempts.append(attempt)
            self.store.add_attempt(run.run_id, attempt)

            if verify_res.passed:
                run.state = RunState.DONE
                run.last_error_output = ""
                self.store.save_run(run)
                return run

            run.last_error_output = verify_res.raw_output
            loop_decision = evaluate_loop_guard(
                verify_res.error_fingerprint,
                tree_hash,
                prior_attempts,
                threshold=self.config.consecutive_error_reflect_threshold,
            )
            if loop_decision == LoopAction.HALT_OSCILLATION:
                return self._halt(run, HaltReason.OSCILLATION_DETECTED)
            elif loop_decision == LoopAction.REFLECT:
                run.state = RunState.REFLECT
            else:
                run.state = RunState.IMPLEMENT

            self.store.save_run(run)

        return run

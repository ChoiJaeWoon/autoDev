"""Finite-state machine states and data records for Agent Forge."""

from enum import Enum
from pydantic import BaseModel, Field


class RunState(str, Enum):
    """Explicit lifecycle states of a single Agent Forge run."""

    INIT = "INIT"
    PLAN = "PLAN"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WRITE_TEST = "WRITE_TEST"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    REFLECT = "REFLECT"
    DONE = "DONE"
    HALT = "HALT"


class HaltReason(str, Enum):
    """Reasons why a run halted before passing verification."""

    NONE = "NONE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MAX_ATTEMPTS = "MAX_ATTEMPTS"
    OSCILLATION_DETECTED = "OSCILLATION_DETECTED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    PATCH_FAILED = "PATCH_FAILED"


class PlanSpec(BaseModel):
    """Structured task decomposition and file whitelist produced in PLAN phase."""

    subtasks: list[str]
    target_source_files: list[str]
    target_test_files: list[str] = Field(default_factory=list)


class AttemptRecord(BaseModel):
    """Record of a single modification/verification attempt within a run."""

    attempt_number: int
    phase: RunState
    tree_hash: str
    commit_sha: str | None = None
    patch_summary: str
    test_passed: bool | None = None
    exit_code: int | None = None
    error_fingerprint: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class RunRecord(BaseModel):
    """Complete checkpointed state of an Agent Forge run."""

    run_id: str
    task_description: str
    repo_root: str
    branch_name: str
    state: RunState
    halt_reason: HaltReason = HaltReason.NONE
    plan: PlanSpec | None = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    baseline_commit_sha: str | None = None
    reflection_notes: list[str] = Field(default_factory=list)
    last_error_output: str = ""
    attempts: list[AttemptRecord] = Field(default_factory=list)

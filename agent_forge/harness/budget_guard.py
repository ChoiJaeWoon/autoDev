"""Token, cost, and attempt budget enforcement."""

from agent_forge.config import ForgeConfig
from agent_forge.core.state import HaltReason, RunRecord


class BudgetGuard:
    """Enforces hard caps on attempts, total tokens, and USD cost per run."""

    def __init__(self, config: ForgeConfig):
        self.config = config

    def check_can_proceed(self, run: RunRecord) -> HaltReason:
        """Return HaltReason.NONE if within limits, or the specific HaltReason if a cap is reached."""
        if (
            run.total_tokens > self.config.max_total_tokens
            or run.total_cost_usd > self.config.max_cost_usd
        ):
            return HaltReason.BUDGET_EXCEEDED
        if len(run.attempts) >= self.config.max_attempts:
            return HaltReason.MAX_ATTEMPTS
        return HaltReason.NONE

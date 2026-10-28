"""File access control and anti-cheating test guardrails."""

import re
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.git_ops import DiffStats
from agent_forge.core.state import PlanSpec, RunState

ASSERT_PATTERN = re.compile(
    r"\b(assert\b|assertEquals|assertThat|assertTrue|assertFalse|assertNotNull|assertThrows)"
)
SKIP_PATTERN = re.compile(
    r"(@Disabled|@Ignore|@pytest\.mark\.skip|pytest\.skip\s*\(|unittest\.skip)"
)


class GuardViolation(Exception):
    """Raised when an agent attempts a prohibited file access or test-cheating edit."""


class FileGuard:
    """Enforces path boundaries, whitelists, and phase-based test file locks."""

    def __init__(self, repo_root: Path, config: ForgeConfig, plan: PlanSpec):
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.plan = plan
        self._source_whitelist = {self._normalize_rel(p) for p in plan.target_source_files}
        self._test_whitelist = {self._normalize_rel(p) for p in plan.target_test_files}

    def _normalize_rel(self, rel_path: str) -> str:
        return Path(rel_path).as_posix().lstrip("./")

    def is_test_file(self, rel_path: str) -> bool:
        norm = self._normalize_rel(rel_path)
        if norm in self._test_whitelist:
            return True
        parts = Path(norm).parts
        name = Path(norm).name
        if any(p in {"test", "tests"} for p in parts):
            return True
        if "src/test/" in norm:
            return True
        if name.startswith("test_") or name.endswith("_test.py") or name.endswith("Test.java"):
            return True
        return False

    def validate_edit_target(self, rel_path: str, phase: RunState) -> Path:
        """Resolve path within repo_root and verify phase-specific file permissions."""
        candidate = (self.repo_root / rel_path).resolve()
        try:
            rel_resolved = candidate.relative_to(self.repo_root).as_posix()
        except ValueError as exc:
            raise GuardViolation(
                f"Path '{rel_path}' resolves outside repository root"
            ) from exc

        if phase == RunState.WRITE_TEST:
            if rel_resolved not in self._test_whitelist:
                raise GuardViolation(
                    f"File '{rel_resolved}' is not allowed in WRITE_TEST phase (allowed test files: {sorted(self._test_whitelist)})"
                )
            return candidate

        if phase in {RunState.IMPLEMENT, RunState.REFLECT}:
            if self.is_test_file(rel_resolved):
                raise GuardViolation(
                    f"Test file '{rel_resolved}' is locked (Read-Only) during {phase.value} phase"
                )
            if rel_resolved not in self._source_whitelist:
                raise GuardViolation(
                    f"File '{rel_resolved}' is not in target_source_files whitelist ({sorted(self._source_whitelist)})"
                )
            return candidate

        raise GuardViolation(f"File editing is not permitted in phase {phase.value}")

    def validate_test_patch_not_cheating(
        self, search_text: str, replace_text: str
    ) -> None:
        """Prevent weakening tests during WRITE_TEST by blocking assertion removal or skip annotations."""
        before_asserts = len(ASSERT_PATTERN.findall(search_text))
        after_asserts = len(ASSERT_PATTERN.findall(replace_text))
        if after_asserts < before_asserts:
            raise GuardViolation(
                f"Test cheating detected: patch reduces assertion count from {before_asserts} to {after_asserts}"
            )
        if SKIP_PATTERN.search(replace_text) and not SKIP_PATTERN.search(search_text):
            raise GuardViolation(
                "Test cheating detected: patch introduces test skip/disable annotation"
            )

    def validate_diff_stats(self, diff_stats: DiffStats, phase: RunState) -> None:
        """Validate post-patch git diff against file count, line count, and whitelist rules."""
        if len(diff_stats.changed_files) > self.config.max_files_changed:
            raise GuardViolation(
                f"Changed files count ({len(diff_stats.changed_files)}) exceeds max_files_changed ({self.config.max_files_changed})"
            )
        if diff_stats.total_changed_lines > self.config.max_diff_lines:
            raise GuardViolation(
                f"Changed lines ({diff_stats.total_changed_lines}) exceeds max_diff_lines ({self.config.max_diff_lines})"
            )
        for changed_file in diff_stats.changed_files:
            self.validate_edit_target(changed_file, phase)

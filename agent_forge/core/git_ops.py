"""Git branch isolation, working-tree hashing, diff statistics, and atomic rollback."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from pydantic import BaseModel, Field


class DiffStats(BaseModel):
    """Summary of modified and untracked files in the working tree."""

    changed_files: list[str] = Field(default_factory=list)
    added_lines: int = 0
    deleted_lines: int = 0
    total_changed_lines: int = 0


class GitOps:
    """Manages git snapshots, deterministic working-tree hashes, and atomic rollbacks."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()
        self._ensure_internal_excludes()

    def _ensure_internal_excludes(self) -> None:
        """Ensure .forge/ and harness directories are excluded from git tracking in target repos."""
        info_exclude = self.repo_root / ".git" / "info" / "exclude"
        if info_exclude.parent.is_dir():
            existing = (
                info_exclude.read_text(encoding="utf-8", errors="ignore")
                if info_exclude.exists()
                else ""
            )
            needed = [".forge/", ".superpowers/", ".worktrees/"]
            missing = [item for item in needed if item not in existing]
            if missing:
                with info_exclude.open("a", encoding="utf-8") as fh:
                    if existing and not existing.endswith("\n"):
                        fh.write("\n")
                    for item in missing:
                        fh.write(f"{item}\n")

    def _purge_transient_caches(self) -> None:
        """Remove transient test/bytecode cache directories so they never pollute diffs or tree hashes."""
        for cache_dir_name in (".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"):
            for p in self.repo_root.rglob(cache_dir_name):
                if ".git" not in p.parts and p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
        for pyc in self.repo_root.rglob("*.py[cod]"):
            if ".git" not in pyc.parts and pyc.is_file():
                pyc.unlink(missing_ok=True)

    def _run_git(
        self, args: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    def get_head_commit(self) -> str:
        return self._run_git(["rev-parse", "HEAD"]).stdout.strip()

    def create_task_branch(self, branch_name: str) -> str:
        self._run_git(["checkout", "-B", branch_name])
        return self.get_head_commit()

    def get_working_tree_hash(self) -> str:
        """Compute deterministic git tree SHA of current working tree + untracked files without mutating index."""
        self._purge_transient_caches()
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_index = Path(tmpdir) / "index"
            git_index = self.repo_root / ".git" / "index"
            if git_index.exists():
                shutil.copy2(git_index, temp_index)
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(temp_index)
            self._run_git(["add", "-A"], env=env)
            return self._run_git(["write-tree"], env=env).stdout.strip()

    def create_snapshot(self, message: str) -> str:
        """Stage all changes and create a snapshot commit, returning the commit SHA."""
        self._purge_transient_caches()
        status = self._run_git(["status", "--porcelain"]).stdout.strip()
        if not status:
            return self.get_head_commit()
        self._run_git(["add", "-A"])
        self._run_git(["commit", "-m", message])
        return self.get_head_commit()

    def get_diff_stats(self, base_ref: str = "HEAD") -> DiffStats:
        """Compute changed files and line counts against base_ref, including untracked files."""
        self._purge_transient_caches()
        numstat_out = self._run_git(["diff", "--numstat", base_ref]).stdout.strip()
        changed_files: list[str] = []
        added_lines = 0
        deleted_lines = 0

        if numstat_out:
            for line in numstat_out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    add_str, del_str, file_path = parts[0], parts[1], parts[2]
                    add_val = int(add_str) if add_str.isdigit() else 0
                    del_val = int(del_str) if del_str.isdigit() else 0
                    added_lines += add_val
                    deleted_lines += del_val
                    if file_path not in changed_files:
                        changed_files.append(file_path)

        # Include untracked files so rogue file creation is never missed
        untracked_out = self._run_git(
            ["ls-files", "--others", "--exclude-standard"]
        ).stdout.strip()
        if untracked_out:
            for rel_path in untracked_out.splitlines():
                rel_path = rel_path.strip()
                if not rel_path:
                    continue
                if rel_path not in changed_files:
                    changed_files.append(rel_path)
                full_path = self.repo_root / rel_path
                if full_path.is_file():
                    try:
                        line_count = len(
                            full_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                        )
                    except OSError:
                        line_count = 0
                    added_lines += line_count

        return DiffStats(
            changed_files=changed_files,
            added_lines=added_lines,
            deleted_lines=deleted_lines,
            total_changed_lines=added_lines + deleted_lines,
        )

    def discard_working_changes(self) -> None:
        """Reset tracked files to HEAD and remove untracked files/directories while preserving .forge/."""
        self._run_git(["reset", "--hard", "HEAD"])
        self._run_git(["clean", "-fd", "-e", ".forge", "-e", ".superpowers", "-e", ".worktrees"])

    def rollback_to_snapshot(self, commit_sha: str) -> None:
        """Atomically reset working tree and branch HEAD to commit_sha and clean untracked files."""
        self._run_git(["reset", "--hard", commit_sha])
        self._run_git(["clean", "-fd", "-e", ".forge", "-e", ".superpowers", "-e", ".worktrees"])

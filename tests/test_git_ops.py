import subprocess
from pathlib import Path
from agent_forge.core.git_ops import GitOps


def _init_sample_repo(path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_working_tree_hash_detects_oscillation_and_preserves_index(tmp_path: Path):
    repo = _init_sample_repo(tmp_path)
    git_ops = GitOps(repo)
    base_sha = git_ops.create_task_branch("forge/test-1")
    assert len(base_sha) == 40

    hash_a = git_ops.get_working_tree_hash()
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    hash_b = git_ops.get_working_tree_hash()
    assert hash_a != hash_b

    # Revert back to A -> tree hash must match hash_a (oscillation A -> B -> A)
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    hash_a_again = git_ops.get_working_tree_hash()
    assert hash_a == hash_a_again


def test_diff_stats_includes_untracked_files_and_rollback_cleans_them(tmp_path: Path):
    repo = _init_sample_repo(tmp_path)
    git_ops = GitOps(repo)
    base_sha = git_ops.get_head_commit()

    (repo / "app.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    (repo / "rogue.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    stats = git_ops.get_diff_stats()
    assert set(stats.changed_files) == {"app.py", "rogue.py"}
    assert stats.total_changed_lines >= 4

    git_ops.rollback_to_snapshot(base_sha)
    assert not (repo / "rogue.py").exists()
    assert "return a + b\n" in (repo / "app.py").read_text(encoding="utf-8")


def test_git_ops_ignores_and_preserves_dot_forge_state_dir(tmp_path: Path):
    repo = _init_sample_repo(tmp_path)
    git_ops = GitOps(repo)
    forge_db = repo / ".forge" / "state.db"
    forge_db.parent.mkdir(parents=True)
    forge_db.write_text("sqlite-bytes", encoding="utf-8")

    # .forge/state.db must not appear as a changed file in diff_stats
    stats = git_ops.get_diff_stats()
    assert stats.changed_files == []

    # discard_working_changes must NOT delete .forge/state.db
    git_ops.discard_working_changes()
    assert forge_db.exists()


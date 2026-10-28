import pytest
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.git_ops import DiffStats
from agent_forge.core.state import PlanSpec, RunState
from agent_forge.harness.file_guard import FileGuard, GuardViolation
from agent_forge.harness.repo_map import build_repo_map


def test_repo_map_extracts_python_and_java_jsp_symbols(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "class Calculator:\n    def add(self, a: int, b: int) -> int:\n        return a + b\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "BoardService.java").write_text(
        "public class BoardService {\n    public List<Post> findPosts(String keyword) { return null; }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "boardList.jsp").write_text(
        '<%@ page contentType="text/html;charset=UTF-8" %>\n'
        '<form action="/board/search"></form>\n'
        '<table class="board-table"><thead><tr><th>번호</th><th>제목</th></tr></thead></table>\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "board.css").write_text(
        ".board-container { width: 960px; }\n.board-table { border-collapse: collapse; }\n",
        encoding="utf-8",
    )
    repo_map = build_repo_map(tmp_path)
    assert "src/calc.py" in repo_map
    assert "class Calculator" in repo_map
    assert "def add" in repo_map
    assert "class BoardService" in repo_map
    assert "findPosts" in repo_map
    assert "src/boardList.jsp" in repo_map
    assert "<th>: 번호, 제목" in repo_map
    assert "src/board.css" in repo_map
    assert ".board-container" in repo_map


def test_file_guard_blocks_path_traversal_and_locks_tests_in_implement(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["기능 구현"],
        target_source_files=["src/calc.py"],
        target_test_files=["tests/test_calc.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)

    # Path traversal blocked
    with pytest.raises(GuardViolation, match="outside repository"):
        guard.validate_edit_target("../../etc/passwd", RunState.IMPLEMENT)

    # WRITE_TEST allows test file, blocks source file
    assert guard.validate_edit_target("tests/test_calc.py", RunState.WRITE_TEST) == (
        tmp_path / "tests/test_calc.py"
    ).resolve()
    with pytest.raises(GuardViolation, match="not allowed in WRITE_TEST"):
        guard.validate_edit_target("src/calc.py", RunState.WRITE_TEST)

    # IMPLEMENT locks test files and allows whitelisted source file
    with pytest.raises(GuardViolation, match="locked"):
        guard.validate_edit_target("tests/test_calc.py", RunState.IMPLEMENT)
    assert guard.validate_edit_target("src/calc.py", RunState.IMPLEMENT) == (
        tmp_path / "src/calc.py"
    ).resolve()


def test_file_guard_blocks_test_cheating_assertion_removal(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["테스트 추가"],
        target_source_files=["src/calc.py"],
        target_test_files=["tests/test_calc.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)

    with pytest.raises(GuardViolation, match="assertion"):
        guard.validate_test_patch_not_cheating(
            search_text="def test_add():\n    assert add(1, 2) == 3\n",
            replace_text="def test_add():\n    pass\n",
        )

    # Commenting out an assertion must also be caught as cheating
    with pytest.raises(GuardViolation, match="assertion"):
        guard.validate_test_patch_not_cheating(
            search_text="def test_add():\n    assert add(1, 2) == 3\n",
            replace_text="def test_add():\n    # assert add(1, 2) == 3\n    pass\n",
        )


def test_file_guard_preserves_dotfiles_and_validates_plan_roles(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["환경 설정"],
        target_source_files=[".env.example"],
        target_test_files=["tests/test_env.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)
    assert guard.validate_edit_target(".env.example", RunState.IMPLEMENT) == (
        tmp_path / ".env.example"
    ).resolve()

    # Source file disguised in target_test_files must be rejected by validate_plan
    bad_plan = PlanSpec(
        subtasks=["위장"],
        target_source_files=[],
        target_test_files=["src/calc.py"],
    )
    bad_guard = FileGuard(tmp_path, ForgeConfig(), bad_plan)
    with pytest.raises(GuardViolation, match="not a valid test file"):
        bad_guard.validate_plan()



def test_file_guard_validates_diff_stats_limits(tmp_path: Path):
    plan = PlanSpec(
        subtasks=["수정"],
        target_source_files=["src/calc.py"],
        target_test_files=["tests/test_calc.py"],
    )
    guard = FileGuard(tmp_path, ForgeConfig(max_diff_lines=200), plan)

    with pytest.raises(GuardViolation, match="max_diff_lines"):
        guard.validate_diff_stats(
            DiffStats(
                changed_files=["src/calc.py"],
                added_lines=150,
                deleted_lines=60,
                total_changed_lines=210,
            ),
            RunState.IMPLEMENT,
        )

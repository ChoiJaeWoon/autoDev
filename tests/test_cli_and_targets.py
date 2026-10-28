from pathlib import Path
from typer.testing import CliRunner
from agent_forge.cli import app
from agent_forge.harness.repo_map import build_repo_map

runner = CliRunner()


def test_cli_list_runs_on_empty_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    result = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert result.exit_code == 0


def test_target_repositories_exist_and_parse_in_repo_map():
    root = Path(__file__).resolve().parents[1]
    py_target = root / "targets" / "python-mini-calc"
    jsp_target = root / "targets" / "legacy-jsp-app"

    assert (py_target / "src" / "calculator.py").exists()
    assert (jsp_target / "src" / "main" / "java" / "com" / "example" / "legacy" / "BoardService.java").exists()
    assert (jsp_target / "src" / "main" / "webapp" / "WEB-INF" / "jsp" / "boardList.jsp").exists()
    assert (jsp_target / "MISSIONS.md").exists()

    jsp_map = build_repo_map(jsp_target)
    assert "BoardService" in jsp_map
    assert "boardList.jsp" in jsp_map

    pom_content = (jsp_target / "pom.xml").read_text(encoding="utf-8")
    test_java_content = (
        jsp_target / "src" / "test" / "java" / "com" / "example" / "legacy" / "BoardServiceTest.java"
    ).read_text(encoding="utf-8")
    assert "junit-jupiter" in pom_content
    assert "@Test" in test_java_content
    assert "assertEquals" in test_java_content


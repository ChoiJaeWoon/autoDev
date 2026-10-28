import sys
import subprocess
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.engine import ForgeEngine
from agent_forge.core.state import HaltReason, RunState
from agent_forge.core.store import StateStore
from agent_forge.llm.client import LLMResponse
from agent_forge.verifiers.test_runner import SubprocessTestVerifier


class ScriptedLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        self.calls.append((system_prompt, user_prompt))
        content = self.responses.pop(0)
        return LLMResponse(
            content=content, prompt_tokens=200, completion_tokens=100, cost_usd=0.001
        )


def _setup_calc_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_engine_reflects_on_repeated_error_then_succeeds(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)
    store = StateStore(tmp_path / "state.db")
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"])

    plan_json = (
        '{"subtasks": ["subtract 함수 추가"], '
        '"target_source_files": ["src/calc.py"], '
        '"target_test_files": ["tests/test_calc.py"]}'
    )
    write_test_patch = (
        "FILE: tests/test_calc.py\n"
        "<<<<<<< SEARCH\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "=======\n"
        "from src.calc import subtract\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n\n"
        "def test_subtract():\n"
        "    assert subtract(5, 3) == 2\n"
        ">>>>>>> REPLACE\n"
    )
    # Attempt 1: wrong formula (a + b)
    bad_impl_1 = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    # Attempt 2: different code (adds comment) so tree_hash differs, but produces SAME AssertionError fingerprint!
    bad_impl_2 = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    # still wrong\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    # Reflection output
    reflection_note = "a + b 대신 a - b를 반환해야 합니다."
    # Attempt 3 (after rollback to post-WRITE_TEST baseline): correct implementation
    good_impl = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a - b\n"
        ">>>>>>> REPLACE\n"
    )

    llm = ScriptedLLM([plan_json, write_test_patch, bad_impl_1, bad_impl_2, reflection_note, good_impl])
    engine = ForgeEngine(ForgeConfig(), store, llm, verifier)
    run = engine.start_run("subtract 함수를 추가해줘", repo, auto_approve=True)

    assert run.state == RunState.DONE
    assert len(run.attempts) == 3
    assert run.attempts[-1].test_passed is True


def test_engine_pauses_at_waiting_approval_and_resumes(tmp_path: Path):
    repo = _setup_calc_repo(tmp_path)
    store = StateStore(tmp_path / "state.db")
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"])

    plan_json = (
        '{"subtasks": ["noop"], '
        '"target_source_files": ["src/calc.py"], '
        '"target_test_files": ["tests/test_calc.py"]}'
    )
    write_test_patch = "NO_CHANGES"
    good_impl = (
        "FILE: src/calc.py\n"
        "<<<<<<< SEARCH\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    return int(a + b)\n"
        ">>>>>>> REPLACE\n"
    )
    llm = ScriptedLLM([plan_json, write_test_patch, good_impl])
    engine = ForgeEngine(ForgeConfig(), store, llm, verifier)

    paused_run = engine.start_run("add 반환값 int 보장", repo, auto_approve=False)
    assert paused_run.state == RunState.WAITING_APPROVAL

    resumed_run = engine.resume_run(paused_run.run_id, approve=True)
    assert resumed_run.state == RunState.DONE

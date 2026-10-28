import pytest
from pathlib import Path
from agent_forge.config import ForgeConfig
from agent_forge.core.state import PlanSpec, RunState
from agent_forge.harness.file_guard import FileGuard
from agent_forge.harness.patcher import (
    PatchError,
    apply_patch_blocks,
    parse_patch_blocks,
)


def test_parse_and_apply_exact_and_fuzzy_indentation_patch(tmp_path: Path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "class Service:\n"
        "    def run(self, x):\n"
        "        if x is None:\n"
        "            return 0\n"
        "        return x + 1\n",
        encoding="utf-8",
    )

    plan = PlanSpec(
        subtasks=["update"],
        target_source_files=["src/service.py"],
        target_test_files=[],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)

    # Notice the SEARCH block has 0 leading indent on `def run` while the file has 4 spaces
    llm_text = (
        "Here is the fix:\n"
        "FILE: src/service.py\n"
        "<<<<<<< SEARCH\n"
        "def run(self, x):\n"
        "    if x is None:\n"
        "        return 0\n"
        "    return x + 1\n"
        "=======\n"
        "def run(self, x):\n"
        "    if x is None:\n"
        "        return -1\n"
        "    return x + 2\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse_patch_blocks(llm_text)
    assert len(blocks) == 1

    res = apply_patch_blocks(blocks, guard, RunState.IMPLEMENT)
    assert res.applied_files == ["src/service.py"]
    updated = target.read_text(encoding="utf-8")
    assert "        return -1\n" in updated
    assert "    return x + 2\n" in updated


def test_ambiguous_search_block_rejected_without_modifying_file(tmp_path: Path):
    target = tmp_path / "src" / "dup.py"
    target.parent.mkdir(parents=True)
    original = "def a():\n    return 1\n\ndef b():\n    return 1\n"
    target.write_text(original, encoding="utf-8")

    plan = PlanSpec(
        subtasks=["dup"],
        target_source_files=["src/dup.py"],
        target_test_files=[],
    )
    guard = FileGuard(tmp_path, ForgeConfig(), plan)
    blocks = parse_patch_blocks(
        "FILE: src/dup.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n"
    )

    with pytest.raises(PatchError, match="ambiguous|multiple"):
        apply_patch_blocks(blocks, guard, RunState.IMPLEMENT)

    assert target.read_text(encoding="utf-8") == original

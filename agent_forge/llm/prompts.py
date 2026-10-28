"""Prompt builders for PLAN, WRITE_TEST, IMPLEMENT, and REFLECT phases."""

from pathlib import Path
from agent_forge.core.state import PlanSpec


def _read_files_context(repo_root: Path, rel_paths: list[str]) -> str:
    resolved_root = Path(repo_root).resolve()
    sections: list[str] = []
    for rel_path in rel_paths:
        full_path = (resolved_root / rel_path).resolve()
        if not full_path.is_relative_to(resolved_root):
            continue
        if full_path.is_file():
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            sections.append(f"=== FILE: {rel_path} ===\n{content}\n")
        else:
            sections.append(f"=== FILE: {rel_path} (NEW FILE) ===\n")
    return "\n".join(sections)


def build_plan_prompt(
    task_description: str, repo_map: str, max_files: int
) -> tuple[str, str]:
    system_prompt = (
        "You are Agent Forge's Planning Engine. "
        "Analyze the Korean user request and repository map, then output ONLY a valid JSON object "
        "with keys: 'subtasks' (list of strings), 'target_source_files' (list of relative paths, "
        f"max {max_files}), and 'target_test_files' (list of relative test file paths)."
    )
    user_prompt = (
        f"## 작업 요청 (Task Description)\n{task_description}\n\n"
        f"## 저장소 구조 및 심볼 요약 (Repo Map)\n{repo_map}\n"
    )
    return system_prompt, user_prompt


def build_write_test_prompt(
    task_description: str, plan: PlanSpec, repo_root: Path
) -> tuple[str, str]:
    system_prompt = (
        "You are Agent Forge's Test Author. "
        "You may ONLY edit files in target_test_files using FILE / <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks. "
        "Never delete existing assertions or add skip annotations. If existing tests are sufficient, output NO_CHANGES."
    )
    files_ctx = _read_files_context(
        repo_root, plan.target_source_files + plan.target_test_files
    )
    user_prompt = (
        f"## 작업 요청\n{task_description}\n\n"
        f"## 하위 작업 계획\n{plan.subtasks}\n\n"
        f"## 수정 허용 테스트 파일\n{plan.target_test_files}\n\n"
        f"## 관련 파일 내용\n{files_ctx}\n"
    )
    return system_prompt, user_prompt


def build_implement_prompt(
    task_description: str,
    plan: PlanSpec,
    repo_root: Path,
    last_error_output: str = "",
    reflection_notes: list[str] | None = None,
) -> tuple[str, str]:
    system_prompt = (
        "You are Agent Forge's Implementation Engine. "
        "Test files are now LOCKED (Read-Only). You may ONLY edit files in target_source_files "
        "using exact FILE / <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks."
    )
    files_ctx = _read_files_context(
        repo_root, plan.target_source_files + plan.target_test_files
    )
    parts = [
        f"## 작업 요청\n{task_description}",
        f"## 하위 작업 계획\n{plan.subtasks}",
        f"## 수정 허용 구현 파일 (테스트 파일 수정 금지)\n{plan.target_source_files}",
        f"## 현재 파일 내용\n{files_ctx}",
    ]
    if reflection_notes:
        parts.append("## 이전 실패 반성 및 우회 전략 (Reflection Notes)\n" + "\n".join(reflection_notes))
    if last_error_output:
        parts.append(f"## 직전 실패 로그 (Last Failure Output)\n{last_error_output}")

    return system_prompt, "\n\n".join(parts)


def build_reflect_prompt(
    task_description: str, plan: PlanSpec, repeated_error_output: str
) -> tuple[str, str]:
    system_prompt = (
        "You are Agent Forge's Reflection Engine. "
        "The previous two attempts failed with the exact same error fingerprint, and the working tree "
        "has been rolled back to the clean baseline. Explain the root cause briefly and specify a different strategy."
    )
    user_prompt = (
        f"## 작업 요청\n{task_description}\n\n"
        f"## 계획\n{plan.subtasks}\n\n"
        f"## 2회 연속 반복된 동일 에러 로그\n{repeated_error_output}\n"
    )
    return system_prompt, user_prompt

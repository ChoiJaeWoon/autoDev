"""3-tier fuzzy SEARCH / REPLACE patch parser and applicator."""

import re
import textwrap
from pathlib import Path
from pydantic import BaseModel, Field
from agent_forge.core.state import RunState
from agent_forge.harness.file_guard import FileGuard


class PatchBlock(BaseModel):
    """A single SEARCH / REPLACE block targeting a relative file path."""

    file_path: str
    search_block: str
    replace_block: str


class PatchError(Exception):
    """Raised when a SEARCH / REPLACE block cannot be uniquely matched or applied."""


class PatchResult(BaseModel):
    """Summary of applied patch blocks."""

    applied_files: list[str] = Field(default_factory=list)
    blocks_applied: int = 0


PATCH_BLOCK_RE = re.compile(
    r"FILE:\s*(?P<file_path>[^\r\n]+)\r?\n"
    r"<<<<<<< SEARCH\r?\n"
    r"(?P<search>.*?)"
    r"=======\r?\n"
    r"(?P<replace>.*?)"
    r">>>>>>> REPLACE",
    re.DOTALL,
)


def parse_patch_blocks(llm_output: str) -> list[PatchBlock]:
    """Extract all FILE / SEARCH / REPLACE blocks from an LLM completion."""
    blocks: list[PatchBlock] = []
    for match in PATCH_BLOCK_RE.finditer(llm_output):
        raw_path = match.group("file_path").strip()
        raw_path = raw_path.replace("(NEW FILE)", "").strip()
        file_path = raw_path.strip("= `#*'\"\t")
        search_raw = match.group("search")
        replace_raw = match.group("replace")
        if search_raw.endswith("\r\n"):
            search_raw = search_raw[:-2]
        elif search_raw.endswith("\n"):
            search_raw = search_raw[:-1]
        if replace_raw.endswith("\r\n"):
            replace_raw = replace_raw[:-2]
        elif replace_raw.endswith("\n"):
            replace_raw = replace_raw[:-1]
        blocks.append(
            PatchBlock(
                file_path=file_path,
                search_block=search_raw,
                replace_block=replace_raw,
            )
        )
    return blocks


def _leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent_block(dedented_replace: str, base_indent: str) -> str:
    if not dedented_replace:
        return ""
    out_lines: list[str] = []
    for line in dedented_replace.splitlines():
        if line.strip():
            out_lines.append(base_indent + line)
        else:
            out_lines.append("")
    return "\n".join(out_lines)


def _apply_single_block_to_content(
    content: str, search_block: str, replace_block: str, file_path: str
) -> str:
    if search_block == "":
        # Empty SEARCH block creates or appends if file was empty
        if content.strip() == "":
            return replace_block + ("\n" if not replace_block.endswith("\n") else "")
        raise PatchError(
            f"Empty SEARCH block is only allowed for new/empty files (target: {file_path})"
        )

    # Tier 1: Exact substring match
    exact_count = content.count(search_block)
    if exact_count > 1:
        raise PatchError(
            f"Ambiguous SEARCH block in '{file_path}': found {exact_count} multiple exact occurrences"
        )
    if exact_count == 1:
        return content.replace(search_block, replace_block, 1)

    content_lines = content.splitlines()
    search_lines = search_block.splitlines()
    n_search = len(search_lines)
    if n_search == 0 or n_search > len(content_lines):
        raise PatchError(f"SEARCH block not found in '{file_path}'")

    # Tier 2: Right-stripped line-by-line match
    search_rstripped = [line.rstrip() for line in search_lines]
    tier2_matches: list[int] = []
    for idx in range(len(content_lines) - n_search + 1):
        window = [line.rstrip() for line in content_lines[idx : idx + n_search]]
        if window == search_rstripped:
            tier2_matches.append(idx)

    if len(tier2_matches) > 1:
        raise PatchError(
            f"Ambiguous SEARCH block in '{file_path}': found {len(tier2_matches)} multiple whitespace-insensitive occurrences"
        )
    if len(tier2_matches) == 1:
        start = tier2_matches[0]
        new_lines = (
            content_lines[:start]
            + replace_block.splitlines()
            + content_lines[start + n_search :]
        )
        trailing_nl = "\n" if content.endswith("\n") else ""
        return "\n".join(new_lines) + trailing_nl

    # Tier 3: Relative indentation (dedent) match
    dedented_search_lines = [
        line.rstrip() for line in textwrap.dedent(search_block).splitlines()
    ]
    tier3_matches: list[tuple[int, str]] = []
    for idx in range(len(content_lines) - n_search + 1):
        raw_window = "\n".join(content_lines[idx : idx + n_search])
        dedented_window = [
            line.rstrip() for line in textwrap.dedent(raw_window).splitlines()
        ]
        if dedented_window == dedented_search_lines:
            first_non_empty = next(
                (line for line in content_lines[idx : idx + n_search] if line.strip()),
                "",
            )
            base_indent = _leading_indent(first_non_empty)
            tier3_matches.append((idx, base_indent))

    if len(tier3_matches) > 1:
        raise PatchError(
            f"Ambiguous SEARCH block in '{file_path}': found {len(tier3_matches)} multiple indentation-insensitive occurrences"
        )
    if len(tier3_matches) == 1:
        start, base_indent = tier3_matches[0]
        dedented_replace = textwrap.dedent(replace_block)
        reindented_replace = _reindent_block(dedented_replace, base_indent)
        new_lines = (
            content_lines[:start]
            + reindented_replace.splitlines()
            + content_lines[start + n_search :]
        )
        trailing_nl = "\n" if content.endswith("\n") else ""
        return "\n".join(new_lines) + trailing_nl

    raise PatchError(f"SEARCH block not found in '{file_path}'")


def apply_patch_blocks(
    blocks: list[PatchBlock], file_guard: FileGuard, phase: RunState
) -> PatchResult:
    """Validate and atomically apply all patch blocks in memory before writing to disk."""
    staged_contents: dict[Path, str] = {}
    applied_rel_paths: list[str] = []

    for block in blocks:
        resolved_path = file_guard.validate_edit_target(block.file_path, phase)
        if phase == RunState.WRITE_TEST:
            file_guard.validate_test_patch_not_cheating(
                block.search_block, block.replace_block
            )

        if resolved_path not in staged_contents:
            if resolved_path.exists():
                staged_contents[resolved_path] = resolved_path.read_text(encoding="utf-8")
            else:
                staged_contents[resolved_path] = ""

        updated = _apply_single_block_to_content(
            staged_contents[resolved_path],
            block.search_block,
            block.replace_block,
            block.file_path,
        )
        staged_contents[resolved_path] = updated
        norm_rel = resolved_path.relative_to(file_guard.repo_root).as_posix()
        if norm_rel not in applied_rel_paths:
            applied_rel_paths.append(norm_rel)

    # Atomic write: only persist after all blocks succeeded
    for resolved_path, new_content in staged_contents.items():
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_text(new_content, encoding="utf-8")

    return PatchResult(applied_files=applied_rel_paths, blocks_applied=len(blocks))

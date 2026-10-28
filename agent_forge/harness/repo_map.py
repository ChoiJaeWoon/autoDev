"""Lightweight repository structure and symbol signature extractor."""

import ast
import re
from pathlib import Path

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "target",
    "build",
    "dist",
    ".superpowers",
    ".worktrees",
}

JAVA_CLASS_RE = re.compile(
    r"^\s*(?:public\s+|protected\s+|private\s+)?(?:abstract\s+|final\s+)?(class|interface|enum)\s+(\w+)"
)
JAVA_METHOD_RE = re.compile(
    r"^\s*(?:public|protected|private)\s+(?:static\s+|final\s+|synchronized\s+)*[\w<>[\], ?]+\s+(\w+)\s*\(([^)]*)\)"
)
JSP_FORM_RE = re.compile(r"<form[^>]*action=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _summarize_python_file(path: Path) -> list[str]:
    signatures: list[str] = []
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return signatures

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            signatures.append(f"  class {node.name}:")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in item.args.args]
                    signatures.append(f"    def {item.name}({', '.join(args)})")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            signatures.append(f"  def {node.name}({', '.join(args)})")
    return signatures


def _summarize_java_file(path: Path) -> list[str]:
    signatures: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return signatures

    for line in lines:
        cls_match = JAVA_CLASS_RE.match(line)
        if cls_match:
            kind, name = cls_match.groups()
            signatures.append(f"  {kind} {name}")
            continue
        method_match = JAVA_METHOD_RE.match(line)
        if method_match:
            method_name, params = method_match.groups()
            signatures.append(f"    {method_name}({params.strip()})")
    return signatures


def _summarize_jsp_file(path: Path) -> list[str]:
    signatures: list[str] = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return signatures

    for match in JSP_FORM_RE.finditer(content):
        signatures.append(f"  <form action=\"{match.group(1)}\">")
    return signatures


def build_repo_map(repo_root: Path, max_files: int = 100) -> str:
    """Build a compact text summary of repository files and top-level symbols."""
    root = Path(repo_root).resolve()
    output_lines: list[str] = []
    file_count = 0

    all_paths = sorted(root.rglob("*"))
    for path in all_paths:
        if file_count >= max_files:
            break
        rel_parts = path.relative_to(root).parts
        if any(part in IGNORED_DIRS or part.startswith(".") for part in rel_parts):
            continue
        if not path.is_file():
            continue

        rel_str = path.relative_to(root).as_posix()
        output_lines.append(rel_str)
        file_count += 1

        suffix = path.suffix.lower()
        if suffix == ".py":
            output_lines.extend(_summarize_python_file(path))
        elif suffix == ".java":
            output_lines.extend(_summarize_java_file(path))
        elif suffix in {".jsp", ".html"}:
            output_lines.extend(_summarize_jsp_file(path))

    return "\n".join(output_lines)

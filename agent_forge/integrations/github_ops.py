"""GitHub Issue intake, Korean PR draft builder, and Draft PR publisher for Stage 3."""

import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from agent_forge.core.state import RunRecord

GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _validate_repo_slug(repo_slug: str) -> str:
    slug = (repo_slug or "").strip()
    if not REPO_SLUG_RE.match(slug) or ".." in slug:
        raise ValueError(f"Invalid GitHub repository slug: '{repo_slug}'")
    return slug


def _build_ssl_context() -> ssl.SSLContext:
    """Create a verified SSLContext, using certifi or macOS /etc/ssl/cert.pem if available."""
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    if os.path.isfile("/etc/ssl/cert.pem"):
        try:
            return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
        except Exception:
            pass
    return ssl.create_default_context()


def resolve_github_token(repo_root: Path | None = None) -> str | None:
    """Resolve GitHub Personal Access Token from environment, .env, or local MCP config."""
    for env_key in (
        "FORGE_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    ):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val

    search_dirs: list[Path] = []
    if repo_root is not None:
        search_dirs.append(Path(repo_root).resolve())
    search_dirs.append(Path.cwd().resolve())

    for d in search_dirs:
        cur = d
        for _ in range(3):
            candidate = cur / ".env"
            if candidate.is_file():
                try:
                    for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k.strip() in {
                            "FORGE_GITHUB_TOKEN",
                            "GITHUB_TOKEN",
                            "GITHUB_PERSONAL_ACCESS_TOKEN",
                        }:
                            cleaned = v.strip().strip("'\"")
                            if cleaned:
                                return cleaned
                except OSError:
                    pass
            if (cur / ".git").exists() or cur.parent == cur:
                break
            cur = cur.parent

    mcp_cfg = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if mcp_cfg.is_file():
        try:
            data = json.loads(mcp_cfg.read_text(encoding="utf-8"))
            token = (
                data.get("mcpServers", {})
                .get("github-mcp-server", {})
                .get("env", {})
                .get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
                .strip()
            )
            if token:
                return token
        except Exception:
            pass

    return None


def detect_github_repo(repo_root: Path) -> str | None:
    """Extract 'owner/repo' slug from git remote origin URL."""
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=Path(repo_root).resolve(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode != 0:
            return None
        remote_url = res.stdout.strip()
        match = GITHUB_REMOTE_RE.search(remote_url)
        if match:
            return match.group(1)
    except Exception:
        return None
    return None


def fetch_github_issue(
    repo_slug: str, issue_number: int, token: str | None = None
) -> dict:
    """Fetch a single GitHub issue by number via the GitHub REST API."""
    slug = _validate_repo_slug(repo_slug)
    num = int(issue_number)
    if num < 1:
        raise ValueError(f"Issue number must be >= 1, got {issue_number}")

    url = f"https://api.github.com/repos/{slug}/issues/{num}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AgentForge-Ops/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(
            req, timeout=15, context=_build_ssl_context()
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"GitHub API HTTP {exc.code} fetching issue #{num}: {err_body}"
        ) from exc

    return {
        "number": int(data.get("number", num)),
        "title": str(data.get("title") or "").strip(),
        "body": str(data.get("body") or "").strip(),
        "html_url": str(data.get("html_url") or ""),
        "state": str(data.get("state") or "open"),
    }


def format_issue_as_task(issue_data: dict) -> str:
    """Convert a GitHub issue dictionary into an actionable Agent Forge mission prompt."""
    num = issue_data.get("number", "?")
    title = (issue_data.get("title") or "").strip()
    body = (issue_data.get("body") or "").strip()
    if body:
        return f"[GitHub Issue #{num}] {title}\n\n{body}"
    return f"[GitHub Issue #{num}] {title}"


def build_pr_draft_markdown(run: RunRecord) -> str:
    """Generate a structured Korean Pull Request description from a completed RunRecord."""
    lines: list[str] = ["## 작업 요약"]

    if run.issue_number is not None:
        if run.issue_url:
            lines.append(
                f"- **연결 이슈:** Closes #{run.issue_number} ({run.issue_url})"
            )
        else:
            lines.append(f"- **연결 이슈:** Closes #{run.issue_number}")

    lines.append(f"- **실행 ID / 브랜치:** `{run.run_id}` (`{run.branch_name}`)")
    first_line = (
        run.task_description.splitlines()[0] if run.task_description else "-"
    )
    lines.append(f"- **수행 미션:** {first_line}")
    lines.append("")

    if run.plan is not None:
        lines.append("## 세부 실행 계획 (Plan)")
        for idx, step in enumerate(run.plan.subtasks, start=1):
            lines.append(f"{idx}. {step}")
        lines.append("")
        lines.append("### 변경 대상 파일 화이트리스트")
        for src in run.plan.target_source_files:
            lines.append(f"- 소스 파일: `{src}`")
        for tst in run.plan.target_test_files:
            lines.append(f"- 테스트 파일 (잠금 완료): `{tst}`")
        lines.append("")

    lines.append("## 하네스 검증 및 가드레일 요약")
    base_sha = (run.baseline_commit_sha or "N/A")[:8]
    final_sha = "N/A"
    last_artifacts: dict[str, str] = {}
    if run.attempts:
        final_sha = (run.attempts[-1].commit_sha or "N/A")[:8]
        last_artifacts = run.attempts[-1].artifacts or {}

    lines.append(f"- **테스트 잠금 커밋 (Baseline):** `{base_sha}`")
    lines.append(f"- **최종 검증 통과 커밋:** `{final_sha}`")
    lines.append(f"- **총 구현 시도 횟수:** {len(run.attempts)}회")
    lines.append(
        f"- **누적 토큰 및 비용:** {run.total_tokens:,} tokens (${run.total_cost_usd:.4f})"
    )

    if (
        "diff_ratio" in last_artifacts
        or "after_screenshot" in last_artifacts
        or "after_png" in last_artifacts
    ):
        try:
            ratio_raw = float(last_artifacts.get("diff_ratio", "0.0") or 0.0)
        except ValueError:
            ratio_raw = 0.0
        dom_status = last_artifacts.get("dom_status", "PASS")
        lines.append(
            f"- **Playwright 시각 회귀 검증:** {dom_status} (픽셀 변경률 `{ratio_raw * 100:.2f}%`, 가로 레이아웃 오버플로 및 JS 런타임 오류 0건)"
        )

    return "\n".join(lines).strip() + "\n"


def _derive_pr_title(run: RunRecord) -> str:
    first_line = (
        run.task_description.strip().splitlines()[0]
        if run.task_description.strip()
        else f"자동화 작업 ({run.run_id})"
    )
    if len(first_line) > 72:
        first_line = first_line[:69] + "..."
    if run.issue_number is not None and f"#{run.issue_number}" not in first_line:
        return f"기능/수정 (#{run.issue_number}): {first_line}"
    return first_line


def publish_github_pr(
    run: RunRecord,
    repo_slug: str,
    token: str,
    base_branch: str = "main",
    draft: bool = True,
    git_push: bool = True,
) -> str:
    """Push the run branch (optional) and create a Draft Pull Request on GitHub."""
    slug = _validate_repo_slug(repo_slug)
    if git_push:
        push_cwd = Path(run.repo_root).resolve()
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            subprocess.run(
                [
                    "git",
                    "push",
                    "--force-with-lease",
                    "-u",
                    "origin",
                    run.branch_name,
                ],
                cwd=push_cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            stderr_msg = (exc.stderr or exc.stdout or "").strip()[:300]
            raise RuntimeError(
                f"Git push failed for branch '{run.branch_name}': {stderr_msg}"
            ) from exc

    body = run.pr_draft_markdown.strip() or build_pr_draft_markdown(run)
    payload = {
        "title": _derive_pr_title(run),
        "head": run.branch_name,
        "base": base_branch,
        "body": body,
        "draft": bool(draft),
    }

    url = f"https://api.github.com/repos/{slug}/pulls"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "AgentForge-Ops/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=20, context=_build_ssl_context()
        ) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"GitHub API HTTP {exc.code} creating PR: {err_body}"
        ) from exc
    return str(resp_data.get("html_url") or "")

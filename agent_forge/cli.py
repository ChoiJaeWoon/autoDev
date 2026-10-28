"""Typer + Rich CLI entrypoint for Agent Forge."""

import shlex
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from agent_forge.config import ForgeConfig
from agent_forge.core.engine import ForgeEngine
from agent_forge.core.state import RunState
from agent_forge.core.store import StateStore
from agent_forge.dashboard.server import start_dashboard_server
from agent_forge.integrations.github_ops import (
    build_pr_draft_markdown,
    detect_github_repo,
    fetch_github_issue,
    format_issue_as_task,
    publish_github_pr,
    resolve_github_token,
)
from agent_forge.llm.client import OpenAICompatibleClient
from agent_forge.verifiers.legacy_web import PlaywrightVisualVerifier
from agent_forge.verifiers.test_runner import SubprocessTestVerifier

app = typer.Typer(help="Agent Forge: Autonomous Coding Harness with Guardrails")
console = Console()


@app.command("run")
def run_command(
    task: str = typer.Argument(..., help="한국어 기능/수정 요청 설명"),
    repo: Path = typer.Option(Path("."), "--repo", help="타깃 프로젝트 경로"),
    test_cmd: str = typer.Option(
        f"{sys.executable} -m pytest -q", "--test-cmd", help="실행할 테스트 게이트 명령어"
    ),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
    auto_approve: bool = typer.Option(
        True, "--auto-approve/--require-approval", help="계획 단계 자동 승인 여부"
    ),
    max_attempts: int = typer.Option(5, "--max-attempts", help="최대 재시도 횟수"),
) -> None:
    """새로운 자율 개발 루프(Run)를 시작합니다."""
    config = ForgeConfig(max_attempts=max_attempts)
    store = StateStore(db_path)
    llm = OpenAICompatibleClient(config)
    if (repo / "pom.xml").is_file() and not (repo / "pyproject.toml").is_file():
        verifier = PlaywrightVisualVerifier()
    else:
        verifier = SubprocessTestVerifier(
            shlex.split(test_cmd), timeout_seconds=config.test_timeout_seconds
        )
    engine = ForgeEngine(config, store, llm, verifier)

    record = engine.start_run(task, repo_root=repo, auto_approve=auto_approve)
    console.print(
        Panel(
            f"[bold]Run ID:[/bold] {record.run_id}\n"
            f"[bold]State:[/bold] {record.state.value}\n"
            f"[bold]Halt Reason:[/bold] {record.halt_reason.value}\n"
            f"[bold]Attempts:[/bold] {len(record.attempts)}\n"
            f"[bold]Tokens / Cost:[/bold] {record.total_tokens} (${record.total_cost_usd:.4f})",
            title="Agent Forge Run Result",
        )
    )


@app.command("list")
def list_command(
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
) -> None:
    """저장된 모든 Run 목록과 상태를 출력합니다."""
    store = StateStore(db_path)
    runs = store.list_runs()
    table = Table(title="Agent Forge Runs")
    table.add_column("Run ID", style="cyan")
    table.add_column("State", style="magenta")
    table.add_column("Halt Reason")
    table.add_column("Attempts", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Task")

    for r in runs:
        table.add_row(
            r.run_id,
            r.state.value,
            r.halt_reason.value,
            str(len(r.attempts)),
            str(r.total_tokens),
            r.task_description[:40],
        )
    console.print(table)


@app.command("status")
def status_command(
    run_id: str = typer.Argument(..., help="조회할 Run ID"),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
) -> None:
    """특정 Run의 상세 상태와 시도(Attempt) 히스토리를 출력합니다."""
    store = StateStore(db_path)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]Run '{run_id}'를 찾을 수 없습니다.[/red]")
        raise typer.Exit(code=1)

    console.print(
        Panel(
            f"[bold]Run ID:[/bold] {run.run_id}\n"
            f"[bold]Task:[/bold] {run.task_description}\n"
            f"[bold]Branch:[/bold] {run.branch_name}\n"
            f"[bold]State:[/bold] {run.state.value} ({run.halt_reason.value})\n"
            f"[bold]Tokens / Cost:[/bold] {run.total_tokens} (${run.total_cost_usd:.4f})",
            title="Run Status",
        )
    )


@app.command("resume")
def resume_command(
    run_id: str = typer.Argument(..., help="재개할 Run ID"),
    approve: bool = typer.Option(True, "--approve/--reject", help="승인 대기 상태 승인 여부"),
    test_cmd: str = typer.Option(
        f"{sys.executable} -m pytest -q", "--test-cmd", help="실행할 테스트 게이트 명령어"
    ),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
) -> None:
    """일시 중지(WAITING_APPROVAL)되었거나 중단된 Run을 체크포인트부터 이어서 실행합니다."""
    config = ForgeConfig()
    store = StateStore(db_path)
    llm = OpenAICompatibleClient(config)
    existing = store.get_run(run_id)
    if (
        existing is not None
        and (Path(existing.repo_root) / "pom.xml").is_file()
        and not (Path(existing.repo_root) / "pyproject.toml").is_file()
    ):
        verifier: object = PlaywrightVisualVerifier()
    else:
        verifier = SubprocessTestVerifier(
            shlex.split(test_cmd), timeout_seconds=config.test_timeout_seconds
        )
    engine = ForgeEngine(config, store, llm, verifier)  # type: ignore[arg-type]
    record = engine.resume_run(run_id, approve=approve)
    console.print(
        f"[green]Run '{record.run_id}' resumed -> {record.state.value} ({record.halt_reason.value})[/green]"
    )


@app.command("issue-run")
def issue_run_command(
    issue_number: int = typer.Argument(..., help="가져올 GitHub 이슈 번호"),
    repo: Path = typer.Option(Path("."), "--repo", help="타깃 프로젝트 경로"),
    repo_slug: str = typer.Option("", "--repo-slug", help="GitHub owner/repo (미지정 시 origin에서 자동 추출)"),
    test_cmd: str = typer.Option(
        f"{sys.executable} -m pytest -q", "--test-cmd", help="실행할 테스트 게이트 명령어"
    ),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
    auto_approve: bool = typer.Option(
        True, "--auto-approve/--require-approval", help="계획 단계 자동 승인 여부"
    ),
) -> None:
    """GitHub 이슈를 불러와 자율 개발 루프(Run)를 실행합니다."""
    slug = repo_slug.strip() or detect_github_repo(repo) or detect_github_repo(Path.cwd())
    if not slug:
        console.print("[red]GitHub 저장소(owner/repo)를 찾을 수 없습니다.[/red]")
        raise typer.Exit(code=1)

    token = resolve_github_token(repo)
    try:
        issue_data = fetch_github_issue(slug, issue_number, token=token)
    except Exception as exc:
        console.print(f"[red]GitHub 이슈 조회 실패: {exc}[/red]")
        raise typer.Exit(code=1)
    task = format_issue_as_task(issue_data)

    config = ForgeConfig()
    store = StateStore(db_path)
    llm = OpenAICompatibleClient(config)
    if (repo / "pom.xml").is_file() and not (repo / "pyproject.toml").is_file():
        verifier: object = PlaywrightVisualVerifier()
    else:
        verifier = SubprocessTestVerifier(
            shlex.split(test_cmd), timeout_seconds=config.test_timeout_seconds
        )
    engine = ForgeEngine(config, store, llm, verifier)  # type: ignore[arg-type]
    record = engine.start_run(
        task,
        repo_root=repo,
        auto_approve=auto_approve,
        issue_number=int(issue_data["number"]),
        issue_url=str(issue_data.get("html_url") or ""),
    )
    console.print(
        Panel(
            f"[bold]Run ID:[/bold] {record.run_id}\n"
            f"[bold]Issue:[/bold] #{record.issue_number} ({record.issue_url})\n"
            f"[bold]State:[/bold] {record.state.value}\n"
            f"[bold]Attempts:[/bold] {len(record.attempts)}",
            title="GitHub Issue Run Result",
        )
    )


@app.command("retry")
def retry_command(
    run_id: str = typer.Argument(..., help="체크포인트부터 재실행할 HALT 상태 Run ID"),
    extra_attempts: int = typer.Option(3, "--extra-attempts", help="추가 부여할 시도 횟수"),
    test_cmd: str = typer.Option(
        f"{sys.executable} -m pytest -q", "--test-cmd", help="실행할 테스트 게이트 명령어"
    ),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
) -> None:
    """중단(HALT)된 Run을 마지막 체크포인트(테스트 잠금 커밋 등)부터 즉시 재실행합니다."""
    config = ForgeConfig()
    store = StateStore(db_path)
    llm = OpenAICompatibleClient(config)
    existing = store.get_run(run_id)
    if (
        existing is not None
        and (Path(existing.repo_root) / "pom.xml").is_file()
        and not (Path(existing.repo_root) / "pyproject.toml").is_file()
    ):
        verifier: object = PlaywrightVisualVerifier()
    else:
        verifier = SubprocessTestVerifier(
            shlex.split(test_cmd), timeout_seconds=config.test_timeout_seconds
        )
    engine = ForgeEngine(config, store, llm, verifier)  # type: ignore[arg-type]
    try:
        record = engine.retry_from_checkpoint(run_id, extra_attempts=extra_attempts)
    except Exception as exc:
        console.print(f"[red]체크포인트 재실행 실패: {exc}[/red]")
        raise typer.Exit(code=1)
    console.print(
        f"[green]Run '{record.run_id}' retried from checkpoint -> {record.state.value} ({record.halt_reason.value})[/green]"
    )


@app.command("pr")
def pr_command(
    run_id: str = typer.Argument(..., help="PR 초안을 생성/게시할 완료된 Run ID"),
    publish: bool = typer.Option(False, "--publish", help="GitHub에 Draft PR 실제 게시 여부"),
    base_branch: str = typer.Option("main", "--base", help="PR 타깃 베이스 브랜치"),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
) -> None:
    """완료(DONE)된 Run의 한국어 PR 초안 마크다운을 출력하고 선택적으로 GitHub Draft PR을 생성합니다."""
    store = StateStore(db_path)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]Run '{run_id}'를 찾을 수 없습니다.[/red]")
        raise typer.Exit(code=1)
    if run.state != RunState.DONE:
        console.print(f"[red]Run '{run_id}' 상태가 DONE이 아닙니다 ({run.state.value}).[/red]")
        raise typer.Exit(code=1)

    if not run.pr_draft_markdown.strip():
        run.pr_draft_markdown = build_pr_draft_markdown(run)
        store.save_run(run)

    console.print(Panel(run.pr_draft_markdown, title=f"PR Draft ({run.run_id})"))

    if publish:
        repo_path = Path(run.repo_root)
        slug = detect_github_repo(repo_path) or detect_github_repo(Path.cwd())
        token = resolve_github_token(repo_path)
        if not slug or not token:
            console.print("[red]GitHub 저장소 또는 인증 토큰을 찾을 수 없습니다.[/red]")
            raise typer.Exit(code=1)
        try:
            pr_url = publish_github_pr(
                run, repo_slug=slug, token=token, base_branch=base_branch, draft=True
            )
        except Exception as exc:
            console.print(f"[red]Draft PR 게시 실패: {exc}[/red]")
            raise typer.Exit(code=1)
        run.pr_url = pr_url
        store.save_run(run)
        console.print(f"[green]Draft PR 게시 완료: {pr_url}[/green]")


@app.command("ui")
def ui_command(
    port: int = typer.Option(8765, "--port", help="인스펙터 웹 서버 포트"),
    host: str = typer.Option("127.0.0.1", "--host", help="바인딩 호스트 주소"),
    db_path: Path = typer.Option(
        Path(".forge/state.db"), "--db-path", help="SQLite 체크포인트 저장 경로"
    ),
) -> None:
    """SQLite 체크포인트 및 Git 커밋 패치를 조회하는 웹 인스펙터를 실행합니다."""
    console.print(
        Panel(
            f"[bold]URL:[/bold] http://{host}:{port}\n"
            f"[bold]DB Path:[/bold] {db_path}\n"
            f"종료하려면 Ctrl+C를 누르세요.",
            title="Agent Forge Web Inspector",
        )
    )
    start_dashboard_server(db_path=db_path, host=host, port=port)


if __name__ == "__main__":
    app()

"""Standard-library HTTP server and JSON API for the Agent Forge Inspector Dashboard."""

import json
import mimetypes
import re
import shlex
import socketserver
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_forge.config import ForgeConfig
from agent_forge.core.engine import ForgeEngine
from agent_forge.core.state import HaltReason, RunRecord, RunState
from agent_forge.core.store import StateStore
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

STATIC_DIR = Path(__file__).resolve().parent / "static"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_ARTIFACT_PATH_RE = re.compile(
    r"^/api/runs/(?P<run_id>[^/]+)/artifacts/(?P<attempt>\d+)/(?P<kind>baseline|after|diff)$"
)


def _resolve_commit_diff(repo_root: str, commit_sha: str | None) -> str:
    """Safely extract unified diff for a commit SHA from repo_root (or its enclosing git repo)."""
    if not commit_sha or not _SHA_RE.match(commit_sha):
        return ""
    repo_path = Path(repo_root)
    if not repo_path.exists():
        return ""
    try:
        res = subprocess.run(
            ["git", "show", "--format=", "--unified=3", commit_sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        return ""
    return ""


def _default_test_cmd_for_repo(repo_path: Path) -> str:
    """Infer the default test command or verifier mode for a target repository."""
    if (repo_path / "pom.xml").is_file() and not (repo_path / "pyproject.toml").is_file():
        return "legacy-web"
    return f"{sys.executable} -m pytest -q"


def _default_engine_factory(db_path: Path, test_cmd: str | None = None) -> ForgeEngine:
    config = ForgeConfig()
    store = StateStore(db_path)
    llm = OpenAICompatibleClient(config)
    effective_cmd = test_cmd or f"{sys.executable} -m pytest -q"
    if effective_cmd == "legacy-web":
        verifier: Any = PlaywrightVisualVerifier()
    else:
        verifier = SubprocessTestVerifier(
            shlex.split(effective_cmd), timeout_seconds=config.test_timeout_seconds
        )
    return ForgeEngine(config, store, llm, verifier)


class DashboardHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer holding reference to the SQLite checkpoint store and worker lock."""

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: Any,
        db_path: Path,
        workspace_root: Path | None = None,
        engine_factory: Callable[[Path, str | None], Any] | None = None,
    ):
        self.db_path = Path(db_path)
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.engine_factory = engine_factory or _default_engine_factory
        self.worker_lock = threading.Lock()
        super().__init__(server_address, RequestHandlerClass)

    def server_bind(self) -> None:
        # Avoid HTTPServer.server_bind()'s socket.getfqdn() reverse-DNS stall on macOS
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Handles JSON API requests (GET/POST) and static inspector frontend files."""

    server: DashboardHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy per-request stderr logging during tests and normal polling
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length == 0:
            return {}
        if length < 0 or length > 262144:
            return None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _serve_static_file(self, rel_path: str) -> None:
        static_root = STATIC_DIR.resolve()
        candidate = (static_root / rel_path).resolve()
        if not candidate.is_relative_to(static_root) or not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Static asset not found"})
            return

        content_type, _ = mimetypes.guess_type(str(candidate))
        if content_type is None:
            content_type = "application/octet-stream"
        elif content_type.startswith("text/") or content_type == "application/javascript":
            content_type = f"{content_type}; charset=utf-8"

        data = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        raw_path = unquote(parsed.path)

        if raw_path == "/api/runs":
            store = StateStore(self.server.db_path)
            runs = list(reversed(store.list_runs()))
            summaries = [
                {
                    "run_id": r.run_id,
                    "task_description": r.task_description,
                    "repo_root": r.repo_root,
                    "branch_name": r.branch_name,
                    "state": r.state.value,
                    "halt_reason": r.halt_reason.value,
                    "total_tokens": r.total_tokens,
                    "total_cost_usd": r.total_cost_usd,
                    "attempt_count": len(r.attempts),
                    "issue_number": r.issue_number,
                    "issue_url": r.issue_url,
                    "pr_url": r.pr_url,
                }
                for r in runs
            ]
            self._send_json(
                HTTPStatus.OK,
                {
                    "db_path": str(self.server.db_path),
                    "worker_busy": self.server.worker_lock.locked(),
                    "runs": summaries,
                },
            )
            return

        if raw_path.startswith("/api/github/issues/"):
            issue_str = raw_path.removeprefix("/api/github/issues/").strip("/")
            if not issue_str.isdigit():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid issue number"})
                return
            self._handle_get_github_issue(int(issue_str))
            return

        art_match = _ARTIFACT_PATH_RE.match(raw_path)
        if art_match:
            self._handle_get_artifact(
                art_match.group("run_id"),
                int(art_match.group("attempt")),
                art_match.group("kind"),
            )
            return

        if raw_path.startswith("/api/runs/"):
            run_id = raw_path.removeprefix("/api/runs/").strip("/")
            if not run_id or "/" in run_id:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invalid run path"})
                return
            store = StateStore(self.server.db_path)
            run = store.get_run(run_id)
            if run is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"Run '{run_id}' not found"},
                )
                return

            if run.state == RunState.DONE and not run.pr_draft_markdown.strip():
                run.pr_draft_markdown = build_pr_draft_markdown(run)
                store.save_run(run)

            payload = run.model_dump(mode="json")
            payload["baseline_diff"] = _resolve_commit_diff(
                run.repo_root, run.baseline_commit_sha
            )
            payload["attempt_diffs"] = {
                str(att.attempt_number): _resolve_commit_diff(
                    run.repo_root, att.commit_sha
                )
                for att in run.attempts
            }
            self._send_json(HTTPStatus.OK, payload)
            return

        if raw_path in ("/", "/index.html"):
            self._serve_static_file("index.html")
            return

        if raw_path.startswith("/static/"):
            rel = raw_path.removeprefix("/static/")
            self._serve_static_file(rel)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

    def _handle_get_artifact(self, run_id: str, attempt_num: int, kind: str) -> None:
        store = StateStore(self.server.db_path)
        run = store.get_run(run_id)
        if run is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Run not found"})
            return

        target_att = next(
            (a for a in run.attempts if a.attempt_number == attempt_num), None
        )
        if target_att is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Attempt not found"})
            return

        key = f"{kind}_screenshot"
        file_path_str = (target_att.artifacts or {}).get(key)
        if not file_path_str:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Artifact '{key}' not recorded"})
            return

        img_path = Path(file_path_str).resolve()
        ws_root = self.server.workspace_root.resolve()
        default_art_root = Path(".forge/artifacts").resolve()
        if not (
            img_path.is_relative_to(ws_root)
            or img_path.is_relative_to(default_art_root)
        ):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Artifact path outside workspace"})
            return

        if not img_path.is_file() or img_path.suffix.lower() != ".png":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Artifact image file not found"})
            return

        data = img_path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invalid PNG signature"})
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        raw_path = unquote(parsed.path)
        body = self._read_json_body()
        if body is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON request body"})
            return

        if raw_path == "/api/runs":
            self._handle_post_start_run(body)
            return

        if raw_path.startswith("/api/runs/") and raw_path.endswith("/resume"):
            middle = raw_path.removeprefix("/api/runs/").removesuffix("/resume").strip("/")
            if not middle or "/" in middle:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invalid run resume path"})
                return
            self._handle_post_resume_run(middle, body)
            return

        if raw_path.startswith("/api/runs/") and raw_path.endswith("/retry"):
            middle = raw_path.removeprefix("/api/runs/").removesuffix("/retry").strip("/")
            if not middle or "/" in middle:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invalid run retry path"})
                return
            self._handle_post_retry_run(middle, body)
            return

        if raw_path.startswith("/api/runs/") and raw_path.endswith("/pr"):
            middle = raw_path.removeprefix("/api/runs/").removesuffix("/pr").strip("/")
            if not middle or "/" in middle:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invalid run PR path"})
                return
            self._handle_post_pr(middle, body)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

    def _handle_get_github_issue(self, issue_number: int) -> None:
        slug = detect_github_repo(self.server.workspace_root) or "ChoiJaeWoon/autoDev"
        token = resolve_github_token(self.server.workspace_root)
        try:
            issue = fetch_github_issue(slug, issue_number, token=token)
        except Exception as exc:
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {"error": f"Failed to fetch GitHub issue #{issue_number} from {slug}: {exc}"},
            )
            return

        issue["repo_slug"] = slug
        issue["task_prompt"] = format_issue_as_task(issue)
        self._send_json(HTTPStatus.OK, issue)

    def _handle_post_start_run(self, body: dict[str, Any]) -> None:
        task = str(body.get("task") or "").strip()
        if not task:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Task description must not be empty."},
            )
            return

        raw_repo = str(body.get("repo_root") or "targets/python-mini-calc").strip()
        if raw_repo == "targets/python-mini-calc" and any(
            kw in task.lower() for kw in (".jsp", ".java", "boardservice", "게시판")
        ):
            legacy_candidate = (self.server.workspace_root / "targets/legacy-jsp-app").resolve()
            if legacy_candidate.is_dir():
                raw_repo = "targets/legacy-jsp-app"

        candidate_repo = (
            Path(raw_repo).resolve()
            if Path(raw_repo).is_absolute()
            else (self.server.workspace_root / raw_repo).resolve()
        )
        if (
            not candidate_repo.is_relative_to(self.server.workspace_root)
            or not candidate_repo.is_dir()
        ):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": f"Target repository '{raw_repo}' must be an existing directory inside workspace."
                },
            )
            return

        auto_approve = bool(body.get("auto_approve", True))
        custom_test_cmd = str(body.get("test_cmd") or "").strip() or _default_test_cmd_for_repo(
            candidate_repo
        )
        raw_issue_num = body.get("issue_number")
        issue_number = (
            int(raw_issue_num)
            if raw_issue_num is not None and str(raw_issue_num).isdigit()
            else None
        )
        issue_url = str(body.get("issue_url") or "").strip()

        if not self.server.worker_lock.acquire(blocking=False):
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "Another Agent Forge run is currently executing. Please wait."},
            )
            return

        run_id = f"run-{uuid.uuid4().hex[:8]}"
        store = StateStore(self.server.db_path)
        initial_record = RunRecord(
            run_id=run_id,
            task_description=task,
            repo_root=str(candidate_repo),
            branch_name=f"forge/{run_id}",
            state=RunState.INIT,
            issue_number=issue_number,
            issue_url=issue_url,
        )
        try:
            store.save_run(initial_record)
        except Exception as exc:
            self.server.worker_lock.release()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"Failed to initialize run record: {exc}"},
            )
            return

        def _worker() -> None:
            try:
                engine = self.server.engine_factory(self.server.db_path, custom_test_cmd)
                try:
                    engine.start_run(
                        task,
                        repo_root=candidate_repo,
                        auto_approve=auto_approve,
                        run_id=run_id,
                        issue_number=issue_number,
                        issue_url=issue_url,
                    )
                except TypeError:
                    engine.start_run(
                        task,
                        repo_root=candidate_repo,
                        auto_approve=auto_approve,
                        run_id=run_id,
                    )
            except Exception as exc:
                err_store = StateStore(self.server.db_path)
                rec = err_store.get_run(run_id) or initial_record
                rec.state = RunState.HALT
                rec.halt_reason = HaltReason.POLICY_VIOLATION
                rec.last_error_output = f"Execution error: {exc}"
                err_store.save_run(rec)
            finally:
                self.server.worker_lock.release()

        threading.Thread(target=_worker, daemon=True).start()
        self._send_json(
            HTTPStatus.ACCEPTED,
            {"run_id": run_id, "state": RunState.INIT.value},
        )

    def _handle_post_resume_run(self, run_id: str, body: dict[str, Any]) -> None:
        store = StateStore(self.server.db_path)
        run = store.get_run(run_id)
        if run is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Run '{run_id}' not found"})
            return
        if run.state != RunState.WAITING_APPROVAL:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Run '{run_id}' is in state {run.state.value}, not WAITING_APPROVAL"},
            )
            return

        if not self.server.worker_lock.acquire(blocking=False):
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "Another Agent Forge run is currently executing. Please wait."},
            )
            return

        approve = bool(body.get("approve", True))
        custom_test_cmd = str(body.get("test_cmd") or "").strip() or _default_test_cmd_for_repo(
            Path(run.repo_root)
        )

        def _resume_worker() -> None:
            try:
                engine = self.server.engine_factory(self.server.db_path, custom_test_cmd)
                engine.resume_run(run_id, approve=approve)
            except Exception as exc:
                err_store = StateStore(self.server.db_path)
                rec = err_store.get_run(run_id)
                if rec is not None:
                    rec.state = RunState.HALT
                    rec.halt_reason = HaltReason.POLICY_VIOLATION
                    rec.last_error_output = f"Resume error: {exc}"
                    err_store.save_run(rec)
            finally:
                self.server.worker_lock.release()

        threading.Thread(target=_resume_worker, daemon=True).start()
        self._send_json(
            HTTPStatus.ACCEPTED,
            {"run_id": run_id, "resuming": True, "approve": approve},
        )

    def _handle_post_retry_run(self, run_id: str, body: dict[str, Any]) -> None:
        store = StateStore(self.server.db_path)
        run = store.get_run(run_id)
        if run is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Run '{run_id}' not found"})
            return
        if run.state != RunState.HALT:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Run '{run_id}' is in state {run.state.value}, not HALT"},
            )
            return

        raw_extra = body.get("extra_attempts", 3)
        if isinstance(raw_extra, bool):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "extra_attempts must be an integer between 1 and 20."},
            )
            return
        try:
            extra_attempts = int(raw_extra)
        except (ValueError, TypeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "extra_attempts must be an integer between 1 and 20."},
            )
            return
        if extra_attempts < 1 or extra_attempts > 20:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "extra_attempts must be between 1 and 20."},
            )
            return

        if not self.server.worker_lock.acquire(blocking=False):
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "Another Agent Forge run is currently executing. Please wait."},
            )
            return

        custom_test_cmd = str(body.get("test_cmd") or "").strip() or _default_test_cmd_for_repo(
            Path(run.repo_root)
        )

        def _retry_worker() -> None:
            try:
                engine = self.server.engine_factory(self.server.db_path, custom_test_cmd)
                engine.retry_from_checkpoint(run_id, extra_attempts=extra_attempts)
            except Exception as exc:
                err_store = StateStore(self.server.db_path)
                rec = err_store.get_run(run_id)
                if rec is not None:
                    rec.state = RunState.HALT
                    rec.halt_reason = HaltReason.POLICY_VIOLATION
                    rec.last_error_output = f"Checkpoint retry error: {exc}"
                    err_store.save_run(rec)
            finally:
                self.server.worker_lock.release()

        threading.Thread(target=_retry_worker, daemon=True).start()
        self._send_json(
            HTTPStatus.ACCEPTED,
            {"run_id": run_id, "retrying": True, "extra_attempts": extra_attempts},
        )

    def _handle_post_pr(self, run_id: str, body: dict[str, Any]) -> None:
        store = StateStore(self.server.db_path)
        run = store.get_run(run_id)
        if run is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Run '{run_id}' not found"})
            return
        if run.state != RunState.DONE:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Run '{run_id}' must be in DONE state to generate or publish a PR."},
            )
            return

        if not run.pr_draft_markdown.strip():
            run.pr_draft_markdown = build_pr_draft_markdown(run)
            store.save_run(run)

        if bool(body.get("publish", False)) and not run.pr_url.strip():
            repo_path = Path(run.repo_root)
            slug = (
                detect_github_repo(repo_path)
                or detect_github_repo(self.server.workspace_root)
                or "ChoiJaeWoon/autoDev"
            )
            token = resolve_github_token(repo_path) or resolve_github_token(
                self.server.workspace_root
            )
            if not token:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "GitHub Personal Access Token not found in environment or config."},
                )
                return
            try:
                pr_url = publish_github_pr(
                    run,
                    repo_slug=slug,
                    token=token,
                    base_branch=str(body.get("base_branch") or "main"),
                    draft=bool(body.get("draft", True)),
                )
                run.pr_url = pr_url
                store.save_run(run)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": f"Failed to publish GitHub PR: {exc}"},
                )
                return

        self._send_json(
            HTTPStatus.OK,
            {
                "run_id": run.run_id,
                "pr_draft_markdown": run.pr_draft_markdown,
                "pr_url": run.pr_url,
            },
        )


def create_dashboard_server(
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace_root: Path | None = None,
    engine_factory: Callable[[Path, str | None], Any] | None = None,
) -> DashboardHTTPServer:
    """Create a configured DashboardHTTPServer instance without blocking."""
    return DashboardHTTPServer(
        (host, port),
        DashboardRequestHandler,
        db_path=db_path,
        workspace_root=workspace_root,
        engine_factory=engine_factory,
    )


def start_dashboard_server(
    db_path: Path, host: str = "127.0.0.1", port: int = 8765
) -> None:
    """Start the dashboard server and block until interrupted."""
    server = create_dashboard_server(db_path=db_path, host=host, port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

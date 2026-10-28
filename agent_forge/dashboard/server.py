"""Standard-library HTTP server and JSON API for the Agent Forge Inspector Dashboard."""

import json
import mimetypes
import re
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_forge.core.store import StateStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


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


class DashboardHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer holding reference to the SQLite checkpoint store."""

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: Any, db_path: Path):
        self.db_path = Path(db_path)
        super().__init__(server_address, RequestHandlerClass)

    def server_bind(self) -> None:
        # Avoid HTTPServer.server_bind()'s socket.getfqdn() reverse-DNS stall on macOS
        import socketserver

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Handles JSON API requests and static inspector frontend files."""

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
        self.send_header("Cache-Control", "no-cache")
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
                }
                for r in runs
            ]
            self._send_json(
                HTTPStatus.OK,
                {
                    "db_path": str(self.server.db_path),
                    "runs": summaries,
                },
            )
            return

        if raw_path.startswith("/api/runs/"):
            run_id = raw_path.removeprefix("/api/runs/").strip("/")
            store = StateStore(self.server.db_path)
            run = store.get_run(run_id)
            if run is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"Run '{run_id}' not found"},
                )
                return

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


def create_dashboard_server(
    db_path: Path, host: str = "127.0.0.1", port: int = 8765
) -> DashboardHTTPServer:
    """Create a configured DashboardHTTPServer instance without blocking."""
    return DashboardHTTPServer((host, port), DashboardRequestHandler, db_path=db_path)


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

"""Subprocess and Docker-isolated test suite runner for Stage 1."""

import subprocess
from pathlib import Path
from agent_forge.harness.loop_guard import extract_error_fingerprint
from agent_forge.verifiers.base import VerificationResult

ALLOWED_EXECUTABLES = {"pytest", "./gradlew", "gradle", "mvn", "./mvnw"}


def _is_allowed_test_command(command: list[str]) -> bool:
    if not command:
        return False
    first = Path(command[0]).name
    if command[0] in ALLOWED_EXECUTABLES or first in ALLOWED_EXECUTABLES:
        return True
    # Allow `<python> -m pytest ...` or `<python> -m unittest ...`
    if (
        len(command) >= 3
        and (first.startswith("python") or command[0].endswith("python"))
        and command[1] == "-m"
        and command[2] in {"pytest", "unittest"}
    ):
        return True
    return False


class SubprocessTestVerifier:
    """Runs whitelisted test commands with a strict timeout and optional Docker isolation."""

    def __init__(
        self,
        command: list[str],
        timeout_seconds: int = 60,
        docker_image: str | None = None,
    ):
        if not _is_allowed_test_command(command):
            raise ValueError(
                f"Command {command} is not in allowed test commands (pytest, gradle, mvn)"
            )
        self.command = list(command)
        self.timeout_seconds = timeout_seconds
        self.docker_image = docker_image

    def _build_exec_command(self, repo_root: Path) -> list[str]:
        if not self.docker_image:
            return self.command
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_root.resolve()}:/workspace",
            "-w",
            "/workspace",
            self.docker_image,
            *self.command,
        ]

    def verify(self, repo_root: Path) -> VerificationResult:
        exec_cmd = self._build_exec_command(Path(repo_root))
        try:
            proc = subprocess.run(
                exec_cmd,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_str = (exc.stdout or b"").decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr_str = (exc.stderr or b"").decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            combined = f"{stdout_str}\n{stderr_str}".strip()
            return VerificationResult(
                passed=False,
                exit_code=124,
                summary=f"Test execution timed out after {self.timeout_seconds}s",
                raw_output=combined,
                error_fingerprint="TIMEOUT",
            )

        raw_output = f"{proc.stdout}\n{proc.stderr}".strip()
        passed = proc.returncode == 0
        fingerprint = None if passed else extract_error_fingerprint(raw_output)
        summary = "All tests passed" if passed else f"Tests failed (exit code {proc.returncode})"
        return VerificationResult(
            passed=passed,
            exit_code=proc.returncode,
            summary=summary,
            raw_output=raw_output,
            error_fingerprint=fingerprint,
        )

"""Base protocol and result payload for all Agent Forge verifiers."""

from pathlib import Path
from typing import Protocol
from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    """Unified output of a verification run across Stage 1 and Stage 2."""

    passed: bool
    exit_code: int
    summary: str
    raw_output: str
    error_fingerprint: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class Verifier(Protocol):
    """Pluggable verifier interface."""

    def verify(self, repo_root: Path) -> VerificationResult: ...

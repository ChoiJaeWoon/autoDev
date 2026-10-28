"""Stage 2 Playwright visual regression and DOM verifier re-export."""

from pathlib import Path
from agent_forge.verifiers.base import VerificationResult
from agent_forge.verifiers.legacy_web import PlaywrightVisualVerifier


class PlaywrightVisualVerifierStub(PlaywrightVisualVerifier):
    """Backward-compatible wrapper around PlaywrightVisualVerifier."""

    def __init__(
        self,
        target_url: str = "http://localhost:8080",
        diff_threshold: float = 0.02,
        artifacts_dir: Path = Path(".forge/artifacts"),
    ):
        super().__init__(artifacts_dir=artifacts_dir)
        self.target_url = target_url
        self.diff_threshold = diff_threshold

    def verify(self, repo_root: Path) -> VerificationResult:
        res = super().verify(repo_root)
        res.artifacts.setdefault("target_url", self.target_url)
        res.artifacts.setdefault("diff_threshold", str(self.diff_threshold))
        return res
